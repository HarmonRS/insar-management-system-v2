import os
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import and_, func, literal, or_, select, text

from .. import database
from ..config import read_int_env, settings, split_env_paths
from ..db_maintenance import inspect_database_structure
from ..models import (
    AiDiagnosisORM,
    AssetInventoryIssueORM,
    AssetInventoryStateORM,
    DinsarResultORM,
    OrbitAssetORM,
    ResultCatalogStateORM,
    ResultProductORM,
    SARSceneGeoORM,
    SceneOrbitBindingORM,
    SourceProductAssetORM,
    SystemTaskORM,
    SystemWorkerHeartbeatORM,
)
from ..idl_service import get_idl_status
from .product_package_schema import CANONICAL_PACKAGE_SCHEMA
from .pairing_state_service import pairing_state_service
from .sbas_insar_catalog_service import sbas_insar_catalog_service
from .wsl_runtime_registry import wsl_runtime_registry


ALLOWED_PRODUCT_PACKAGE_SCHEMAS = {
    CANONICAL_PACKAGE_SCHEMA,
    "insar.gamma-ipta-sbas-run/v1",
    "insar.gamma-sbas-run/v1",
}
DEFAULT_WORKER_TIMEOUT_SECONDS = 60
DEFAULT_SCHEMA_CACHE_SECONDS = 120
_SCHEMA_CACHE_LOCK = asyncio.Lock()
_SCHEMA_CACHE: Dict[str, Any] = {
    "payload": None,
    "expires_at": None,
}


def _get_session_factory():
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal


def _default_schema_status() -> Dict[str, Any]:
    return {
        "schema_ok": False,
        "missing_tables": [],
        "required_tables": [],
        "extra_tables": [],
        "required_table_count": 0,
        "current_table_count": 0,
        "missing_columns": {},
        "extra_columns": {},
        "type_mismatches": [],
        "nullable_mismatches": [],
        "schema_reasons": [],
        "schema_issue_count": 0,
    }


def _apply_schema_details(status: Dict[str, Any], schema_details: Dict[str, Any]) -> None:
    status["required_tables"] = schema_details.get("required_tables", [])
    status["required_table_count"] = int(schema_details.get("required_table_count") or 0)
    status["current_table_count"] = int(schema_details.get("current_table_count") or 0)
    status["missing_tables"] = schema_details.get("missing_tables", [])
    status["extra_tables"] = schema_details.get("extra_tables", [])
    status["missing_columns"] = schema_details.get("missing_columns", {})
    status["extra_columns"] = schema_details.get("extra_columns", {})
    status["type_mismatches"] = schema_details.get("type_mismatches", [])
    status["nullable_mismatches"] = schema_details.get("nullable_mismatches", [])
    status["schema_reasons"] = schema_details.get("reasons", [])
    status["schema_issue_count"] = int(schema_details.get("reason_count") or 0)
    status["schema_ok"] = not bool(schema_details.get("mismatch"))


async def _get_cached_schema_details(force_refresh: bool = False) -> Dict[str, Any]:
    ttl_seconds = read_int_env(
        "HEALTH_SCHEMA_CACHE_SECONDS",
        DEFAULT_SCHEMA_CACHE_SECONDS,
        minimum=5,
        maximum=3600,
    )
    now = datetime.utcnow()
    cached_payload = _SCHEMA_CACHE.get("payload")
    expires_at = _SCHEMA_CACHE.get("expires_at")
    if (
        not force_refresh
        and cached_payload is not None
        and expires_at is not None
        and expires_at > now
    ):
        return cached_payload

    async with _SCHEMA_CACHE_LOCK:
        cached_payload = _SCHEMA_CACHE.get("payload")
        expires_at = _SCHEMA_CACHE.get("expires_at")
        now = datetime.utcnow()
        if (
            not force_refresh
            and cached_payload is not None
            and expires_at is not None
            and expires_at > now
        ):
            return cached_payload

        try:
            session_factory = _get_session_factory()
            async with session_factory() as db:
                schema_details = await db.run_sync(
                    lambda sync_session: inspect_database_structure(sync_session.connection())
                )
        except Exception:
            if cached_payload is not None:
                return cached_payload
            raise

        _SCHEMA_CACHE["payload"] = schema_details
        _SCHEMA_CACHE["expires_at"] = now + timedelta(seconds=ttl_seconds)
        return schema_details


async def _check_database(force_schema_refresh: bool = False) -> Dict[str, Any]:
    status = {
        "ok": False,
        "postgis_ok": False,
        **_default_schema_status(),
        "error": None,
    }
    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            await db.execute(text("SELECT 1"))
            status["ok"] = True

            ext = await db.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
            )
            status["postgis_ok"] = ext.first() is not None
        schema_details = await _get_cached_schema_details(force_refresh=force_schema_refresh)
        _apply_schema_details(status, schema_details)
    except Exception as exc:
        status["error"] = str(exc)

    return status


async def _check_worker(timeout_seconds: int = DEFAULT_WORKER_TIMEOUT_SECONDS) -> Dict[str, Any]:
    status = {
        "ok": False,
        "worker_count": 0,
        "workers": [],
        "timeout_seconds": timeout_seconds,
    }
    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            threshold = datetime.utcnow() - timedelta(seconds=timeout_seconds)
            result = await db.execute(
                select(SystemWorkerHeartbeatORM)
                .where(SystemWorkerHeartbeatORM.last_seen >= threshold)
                .order_by(SystemWorkerHeartbeatORM.last_seen.desc())
            )
            rows = result.scalars().all()
            status["worker_count"] = len(rows)
            status["workers"] = [
                {
                    "worker_id": r.worker_id,
                    "hostname": r.hostname,
                    "pid": r.pid,
                    "last_seen": r.last_seen,
                }
                for r in rows
            ]
            status["ok"] = len(rows) > 0
    except Exception as exc:
        status["error"] = str(exc)
    return status


async def _check_ollama() -> Dict[str, Any]:
    status = {"ok": False, "error": None, "status_code": None, "models": []}
    ollama_base_url = settings.OLLAMA_BASE_URL
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{ollama_base_url}/api/tags")
            status["status_code"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                status["models"] = [m.get("name") for m in data.get("models", [])]
                status["ok"] = True
    except Exception as exc:
        status["error"] = str(exc)
    return status


def _build_catalog_status(
    *,
    catalog_name: str,
    storage_root: str,
    enabled: bool = True,
    storage_roots: Optional[List[str]] = None,
) -> Dict[str, Any]:
    normalized_roots = [str(item or "").strip() for item in (storage_roots or [storage_root]) if str(item or "").strip()]
    if not normalized_roots and storage_root:
        normalized_roots = [storage_root]
    return {
        "ok": False,
        "catalog_name": catalog_name,
        "enabled": enabled,
        "storage_root": storage_root,
        "storage_roots": normalized_roots,
        "storage_root_exists": bool(storage_root) and os.path.isdir(storage_root),
        "storage_roots_status": [
            {"path": root, "exists": os.path.isdir(root)}
            for root in normalized_roots
        ],
        "state_present": False,
        "catalog_status": None,
        "needs_rebuild": None,
        "manifest_count": 0,
        "db_count": 0,
        "issue_count": 0,
        "last_boot_check_at": None,
        "last_full_rebuild_at": None,
        "last_incremental_scan_at": None,
        "last_message": None,
        "error": None,
    }


async def _check_catalog(
    *,
    catalog_name: str,
    storage_root: str,
    enabled: bool = True,
    storage_roots: Optional[List[str]] = None,
) -> Dict[str, Any]:
    status = _build_catalog_status(
        catalog_name=catalog_name,
        storage_root=storage_root,
        enabled=enabled,
        storage_roots=storage_roots,
    )
    if not enabled:
        status["ok"] = True
        status["catalog_status"] = "DISABLED"
        status["needs_rebuild"] = False
        status["last_message"] = "catalog disabled by config"
        return status

    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            result = await db.execute(
                select(ResultCatalogStateORM).where(
                    ResultCatalogStateORM.catalog_name == catalog_name
                )
            )
            state = result.scalar_one_or_none()
            if state is not None:
                status["state_present"] = True
                status["catalog_status"] = state.status
                status["needs_rebuild"] = bool(state.needs_rebuild)
                status["manifest_count"] = int(state.manifest_count or 0)
                status["issue_count"] = int(state.issue_count or 0)
                status["last_boot_check_at"] = state.last_boot_check_at
                status["last_full_rebuild_at"] = state.last_full_rebuild_at
                status["last_incremental_scan_at"] = state.last_incremental_scan_at
                status["last_message"] = state.last_message
                if state.storage_root:
                    status["storage_root"] = state.storage_root
                    root_status_by_path = {
                        item["path"]: item
                        for item in status.get("storage_roots_status", [])
                    }
                    if state.storage_root not in root_status_by_path:
                        status.setdefault("storage_roots", []).insert(0, state.storage_root)
                        status.setdefault("storage_roots_status", []).insert(
                            0,
                            {"path": state.storage_root, "exists": os.path.isdir(state.storage_root)},
                        )
                    status["storage_root_exists"] = os.path.isdir(state.storage_root)

            count_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(
                    ResultProductORM.catalog_name == catalog_name
                )
            )
            status["db_count"] = int(count_result.scalar_one() or 0)

            manifest_count = int(status["manifest_count"] or 0)
            needs_rebuild = bool(status["needs_rebuild"])
            roots_status = status.get("storage_roots_status") or []
            roots_exist = all(bool(item.get("exists")) for item in roots_status) if roots_status else bool(status["storage_root_exists"])
            status["ok"] = roots_exist and not (
                manifest_count > 0 and needs_rebuild
            )
    except Exception as exc:
        status["error"] = str(exc)

    return status


async def _check_result_catalog() -> Dict[str, Any]:
    return await _check_catalog(
        catalog_name="dinsar",
        storage_root=settings.DINSAR_PRODUCT_DIR,
        enabled=True,
    )


async def _check_timeseries_result_catalog() -> Dict[str, Any]:
    return await _check_catalog(
        catalog_name="psinsar",
        storage_root=settings.TIMESERIES_PRODUCT_DIR,
        enabled=bool(settings.TIMESERIES_ENABLED),
    )


async def _check_sbas_insar_result_catalog() -> Dict[str, Any]:
    run_roots = sbas_insar_catalog_service.get_run_roots()
    primary_root = run_roots[0] if run_roots else os.path.join(settings.GAMMA_SBAS_WORK_ROOT, "runs")
    return await _check_catalog(
        catalog_name="sbas_insar",
        storage_root=primary_root,
        storage_roots=run_roots,
        enabled=bool(settings.GAMMA_SBAS_ENABLED or settings.LANDSAR_SBAS_ENABLED),
    )


async def _check_psinsar_result_catalog() -> Dict[str, Any]:
    return await _check_timeseries_result_catalog()


async def _check_nginx() -> Dict[str, Any]:
    status = {"ok": False, "error": None, "status_code": None}
    nginx_health_url = settings.NGINX_HEALTH_URL
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(nginx_health_url)
            status["status_code"] = resp.status_code
            status["ok"] = True
    except Exception as exc:
        status["error"] = str(exc)
    return status


def _as_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


def _sanitize_catalog_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "catalog_name": payload.get("catalog_name"),
        "enabled": _as_optional_bool(payload.get("enabled")),
        "ok": bool(payload.get("ok")),
        "needs_rebuild": _as_optional_bool(payload.get("needs_rebuild")),
        "catalog_status": payload.get("catalog_status"),
        "manifest_count": int(payload.get("manifest_count") or 0),
        "db_count": int(payload.get("db_count") or 0),
        "issue_count": int(payload.get("issue_count") or 0),
    }


def _sanitize_bridge_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(payload.get("ok")),
        "catalog_count": int(payload.get("catalog_count") or 0),
        "compat_count": int(payload.get("compat_count") or 0),
        "matched_count": int(payload.get("matched_count") or 0),
        "missing_compat_count": int(payload.get("missing_compat_count") or 0),
        "orphan_compat_count": int(payload.get("orphan_compat_count") or 0),
        "duplicate_compat_product_count": int(payload.get("duplicate_compat_product_count") or 0),
        "annotation_drift_count": int(payload.get("annotation_drift_count") or 0),
        "diagnosis_total_count": int(payload.get("diagnosis_total_count") or 0),
        "diagnosis_missing_product_row_count": int(payload.get("diagnosis_missing_product_row_count") or 0),
        "diagnosis_product_identity_mismatch_count": int(payload.get("diagnosis_product_identity_mismatch_count") or 0),
        "diagnosis_result_product_mismatch_count": int(payload.get("diagnosis_result_product_mismatch_count") or 0),
        "result_trace_missing_count": int(payload.get("result_trace_missing_count") or 0),
        "result_trace_orphan_count": int(payload.get("result_trace_orphan_count") or 0),
        "result_trace_pair_mismatch_count": int(payload.get("result_trace_pair_mismatch_count") or 0),
    }


def _sanitize_source_roots_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(payload.get("ok")),
        "configured_count": int(payload.get("configured_count") or 0),
        "accessible_count": int(payload.get("accessible_count") or 0),
        "inaccessible_count": int(payload.get("inaccessible_count") or 0),
    }


def _sanitize_sar_analysis_ready_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    scenes = payload.get("scenes", {}) or {}
    roots = payload.get("roots", {}) or {}
    return {
        "ok": bool(payload.get("ok")),
        "configured_root_count": len(roots),
        "accessible_root_count": sum(1 for item in roots.values() if item.get("accessible")),
        "scene_count": int(scenes.get("scene_count") or 0),
        "analysis_scene_count": int(scenes.get("analysis_scene_count") or 0),
        "missing_file_count": int(scenes.get("missing_file_count") or 0),
    }


def _sanitize_product_package_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(payload.get("ok")),
        "total_count": int(payload.get("total_count") or 0),
        "canonical_count": int(payload.get("canonical_count") or 0),
        "valid_schema_count": int(payload.get("valid_schema_count") or 0),
        "invalid_schema_count": int(payload.get("invalid_schema_count") or 0),
        "missing_manifest_count": int(payload.get("missing_manifest_count") or 0),
        "missing_publish_dir_count": int(payload.get("missing_publish_dir_count") or 0),
        "missing_processor_count": int(payload.get("missing_processor_count") or 0),
        "missing_runtime_count": int(payload.get("missing_runtime_count") or 0),
        "missing_native_output_count": int(payload.get("missing_native_output_count") or 0),
    }


def _sanitize_wsl_runtime_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(payload.get("ok")),
        "broker_job_root_exists": bool(payload.get("broker_job_root_exists")),
        "required_runtime_count": int(payload.get("required_runtime_count") or 0),
        "healthy_runtime_count": int(payload.get("healthy_runtime_count") or 0),
        "shared_distro": payload.get("shared_distro"),
        "shared_conda_env_name": payload.get("shared_conda_env_name"),
    }


def _sanitize_pairing_system_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(payload.get("ok")),
        "state_present": bool(payload.get("state_present")),
        "status": payload.get("status"),
        "metric_version": payload.get("metric_version"),
        "scene_count": int(payload.get("scene_count") or 0),
        "pair_count": int(payload.get("pair_count") or 0),
        "dirty_scene_count": int(payload.get("dirty_scene_count") or 0),
        "needs_rebuild": bool(payload.get("needs_rebuild")),
        "cache_ready": bool(payload.get("cache_ready")),
        "network_run_count": int(payload.get("network_run_count") or 0),
        "network_edge_count": int(payload.get("network_edge_count") or 0),
        "duplicate_reverse_pair_count": int(payload.get("duplicate_reverse_pair_count") or 0),
        "orphan_edge_count": int(payload.get("orphan_edge_count") or 0),
    }


def _sanitize_asset_inventory_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    source_roots = payload.get("source_roots", {}) or {}
    orbit_roots = payload.get("orbit_roots", {}) or {}
    source_assets = payload.get("source_assets", {}) or {}
    orbit_assets = payload.get("orbit_assets", {}) or {}
    bindings = payload.get("bindings", {}) or {}
    issues = payload.get("issues", {}) or {}
    return {
        "ok": bool(payload.get("ok")),
        "source_roots": {
            "configured_count": int(source_roots.get("configured_count") or 0),
            "accessible_count": int(source_roots.get("accessible_count") or 0),
            "needs_rescan_count": int(source_roots.get("needs_rescan_count") or 0),
        },
        "orbit_roots": {
            "configured_count": int(orbit_roots.get("configured_count") or 0),
            "accessible_count": int(orbit_roots.get("accessible_count") or 0),
            "needs_rescan_count": int(orbit_roots.get("needs_rescan_count") or 0),
        },
        "source_assets": {
            "total_count": int(source_assets.get("total_count") or 0),
            "lt1_count": int(source_assets.get("lt1_count") or 0),
            "s1_count": int(source_assets.get("s1_count") or 0),
            "parse_failed_count": int(source_assets.get("parse_failed_count") or 0),
        },
        "orbit_assets": {
            "total_count": int(orbit_assets.get("total_count") or 0),
            "lt1_count": int(orbit_assets.get("lt1_count") or 0),
            "s1_count": int(orbit_assets.get("s1_count") or 0),
            "parse_failed_count": int(orbit_assets.get("parse_failed_count") or 0),
        },
        "bindings": {
            "scene_count": int(bindings.get("scene_count") or 0),
            "matched_count": int(bindings.get("matched_count") or 0),
            "missing_count": int(bindings.get("missing_count") or 0),
            "ambiguous_count": int(bindings.get("ambiguous_count") or 0),
        },
        "issues": {
            "open_count": int(issues.get("open_count") or 0),
            "error_count": int(issues.get("error_count") or 0),
            "warning_count": int(issues.get("warning_count") or 0),
        },
    }


def _sanitize_health_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    database = payload.get("database", {}) or {}
    worker = payload.get("worker", {}) or {}
    result_catalog = payload.get("result_catalog", {}) or {}
    dinsar_result_catalog = payload.get("dinsar_result_catalog", {}) or result_catalog
    timeseries_result_catalog = (
        payload.get("timeseries_result_catalog", {}) or payload.get("psinsar_result_catalog", {}) or {}
    )
    psinsar_result_catalog = timeseries_result_catalog
    sbas_insar_result_catalog = payload.get("sbas_insar_result_catalog", {}) or {}
    dinsar_bridge = payload.get("dinsar_bridge", {}) or {}
    source_roots = payload.get("source_roots", {}) or {}
    sar_analysis_ready = payload.get("sar_analysis_ready", {}) or {}
    product_packages = payload.get("product_packages", {}) or {}
    asset_inventory = payload.get("asset_inventory", {}) or {}
    wsl_runtime = payload.get("wsl_runtime", {}) or {}
    pairing_system = payload.get("pairing_system", {}) or {}
    idl = payload.get("idl", {}) or {}
    idl_status = idl.get("status", {}) or {}
    ollama = payload.get("ollama", {}) or {}
    nginx = payload.get("nginx", {}) or {}
    sanitized_dinsar_catalog = _sanitize_catalog_status(dinsar_result_catalog)
    sanitized_timeseries_catalog = _sanitize_catalog_status(timeseries_result_catalog)
    sanitized_psinsar_catalog = sanitized_timeseries_catalog
    sanitized_sbas_insar_catalog = _sanitize_catalog_status(sbas_insar_result_catalog)
    sanitized_dinsar_bridge = _sanitize_bridge_status(dinsar_bridge)
    sanitized_source_roots = _sanitize_source_roots_status(source_roots)
    sanitized_sar_analysis_ready = _sanitize_sar_analysis_ready_status(sar_analysis_ready)
    sanitized_product_packages = _sanitize_product_package_status(product_packages)
    sanitized_asset_inventory = _sanitize_asset_inventory_status(asset_inventory)
    sanitized_wsl_runtime = _sanitize_wsl_runtime_status(wsl_runtime)
    sanitized_pairing_system = _sanitize_pairing_system_status(pairing_system)

    return {
        "ok": bool(payload.get("ok")),
        "timestamp": payload.get("timestamp"),
        "database": {
            "ok": bool(database.get("ok")),
            "postgis_ok": bool(database.get("postgis_ok")),
            "schema_ok": bool(database.get("schema_ok")),
        },
        "worker": {
            "ok": bool(worker.get("ok")),
            "worker_count": int(worker.get("worker_count") or 0),
            "timeout_seconds": int(worker.get("timeout_seconds") or DEFAULT_WORKER_TIMEOUT_SECONDS),
        },
        "result_catalog": sanitized_dinsar_catalog,
        "dinsar_result_catalog": sanitized_dinsar_catalog,
        "timeseries_result_catalog": sanitized_timeseries_catalog,
        "psinsar_result_catalog": sanitized_psinsar_catalog,
        "sbas_insar_result_catalog": sanitized_sbas_insar_catalog,
        "catalogs": {
            "dinsar": sanitized_dinsar_catalog,
            "timeseries": sanitized_timeseries_catalog,
            "psinsar": sanitized_psinsar_catalog,
            "sbas_insar": sanitized_sbas_insar_catalog,
        },
        "dinsar_bridge": sanitized_dinsar_bridge,
        "source_roots": sanitized_source_roots,
        "sar_analysis_ready": sanitized_sar_analysis_ready,
        "product_packages": sanitized_product_packages,
        "asset_inventory": sanitized_asset_inventory,
        "wsl_runtime": sanitized_wsl_runtime,
        "pairing_system": sanitized_pairing_system,
        "idl": {
            "ok": bool(idl.get("ok")),
            "status": {
                "is_running": bool(idl_status.get("is_running")),
            },
        },
        "ollama": {
            "ok": _as_optional_bool(ollama.get("ok")),
        },
        "nginx": {
            "ok": _as_optional_bool(nginx.get("ok")),
        },
    }


async def _check_dinsar_engines() -> Dict[str, Any]:
    """检查所有 D-InSAR 引擎可用性（快速，不做 WSL 深度校验）。"""
    try:
        from ..dinsar_engines import registry

        engines = registry.list_engines()
        results = []
        any_available = False

        for engine in engines:
            avail = await asyncio.to_thread(engine.check_available)
            results.append({
                "engine_code": avail.engine_code,
                "status": avail.status,
                "available": avail.available,
                "message": avail.message,
            })
            if avail.available:
                any_available = True

        # 整体状态：至少一个引擎可用即为 ok/degraded
        non_placeholder = [r for r in results if r["status"] != "not_implemented"]
        all_available = all(r["available"] for r in non_placeholder)
        if all_available and any_available:
            overall = "ok"
        elif any_available:
            overall = "degraded"
        else:
            overall = "unavailable"

        return {
            "ok": any_available,
            "overall": overall,
            "engines": results,
        }
    except Exception as exc:
        return {"ok": False, "overall": "error", "engines": [], "error": str(exc)}


async def _check_dinsar_bridge() -> Dict[str, Any]:
    status = {
        "ok": False,
        "catalog_count": 0,
        "compat_count": 0,
        "matched_count": 0,
        "missing_compat_count": 0,
        "orphan_compat_count": 0,
        "duplicate_compat_product_count": 0,
        "annotation_drift_count": 0,
        "diagnosis_total_count": 0,
        "diagnosis_with_product_ref_count": 0,
        "diagnosis_missing_product_ref_count": 0,
        "diagnosis_with_product_id_count": 0,
        "diagnosis_missing_product_id_count": 0,
        "diagnosis_missing_product_row_count": 0,
        "diagnosis_product_identity_mismatch_count": 0,
        "diagnosis_result_product_mismatch_count": 0,
        "result_trace_missing_count": 0,
        "result_trace_orphan_count": 0,
        "result_trace_pair_mismatch_count": 0,
        "error": None,
    }
    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            catalog_count_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(
                    ResultProductORM.catalog_name == "dinsar"
                )
            )
            status["catalog_count"] = int(catalog_count_result.scalar_one() or 0)

            compat_count_result = await db.execute(
                select(func.count(DinsarResultORM.id)).where(
                    DinsarResultORM.compat_product_id.is_not(None)
                )
            )
            status["compat_count"] = int(compat_count_result.scalar_one() or 0)

            matched_count_result = await db.execute(
                select(func.count(DinsarResultORM.id))
                .select_from(DinsarResultORM)
                .join(
                    ResultProductORM,
                    and_(
                        ResultProductORM.product_id == DinsarResultORM.compat_product_id,
                        ResultProductORM.catalog_name == "dinsar",
                    ),
                )
            )
            status["matched_count"] = int(matched_count_result.scalar_one() or 0)

            missing_compat_count_result = await db.execute(
                select(func.count(ResultProductORM.id))
                .select_from(ResultProductORM)
                .outerjoin(
                    DinsarResultORM,
                    DinsarResultORM.compat_product_id == ResultProductORM.product_id,
                )
                .where(
                    ResultProductORM.catalog_name == "dinsar",
                    DinsarResultORM.id.is_(None),
                )
            )
            status["missing_compat_count"] = int(missing_compat_count_result.scalar_one() or 0)

            orphan_compat_count_result = await db.execute(
                select(func.count(DinsarResultORM.id))
                .select_from(DinsarResultORM)
                .outerjoin(
                    ResultProductORM,
                    and_(
                        ResultProductORM.product_id == DinsarResultORM.compat_product_id,
                        ResultProductORM.catalog_name == "dinsar",
                    ),
                )
                .where(
                    DinsarResultORM.compat_product_id.is_not(None),
                    ResultProductORM.id.is_(None),
                )
            )
            status["orphan_compat_count"] = int(orphan_compat_count_result.scalar_one() or 0)

            duplicate_subquery = (
                select(DinsarResultORM.compat_product_id)
                .where(DinsarResultORM.compat_product_id.is_not(None))
                .group_by(DinsarResultORM.compat_product_id)
                .having(func.count(DinsarResultORM.id) > 1)
                .subquery()
            )
            duplicate_count_result = await db.execute(
                select(func.count()).select_from(duplicate_subquery)
            )
            status["duplicate_compat_product_count"] = int(duplicate_count_result.scalar_one() or 0)

            annotation_drift_result = await db.execute(
                select(func.count(DinsarResultORM.id))
                .select_from(DinsarResultORM)
                .join(
                    ResultProductORM,
                    and_(
                        ResultProductORM.product_id == DinsarResultORM.compat_product_id,
                        ResultProductORM.catalog_name == "dinsar",
                    ),
                )
                .where(
                    or_(
                        func.coalesce(ResultProductORM.ai_score, literal(-1.0))
                        != func.coalesce(DinsarResultORM.ai_score, literal(-1.0)),
                        func.coalesce(ResultProductORM.user_label, literal(-999999))
                        != func.coalesce(DinsarResultORM.user_label, literal(-999999)),
                    )
                )
            )
            status["annotation_drift_count"] = int(annotation_drift_result.scalar_one() or 0)

            diagnosis_total_result = await db.execute(
                select(func.count(AiDiagnosisORM.id))
            )
            status["diagnosis_total_count"] = int(diagnosis_total_result.scalar_one() or 0)

            diagnosis_with_product_ref_result = await db.execute(
                select(func.count(AiDiagnosisORM.id)).where(AiDiagnosisORM.product_ref_id.is_not(None))
            )
            status["diagnosis_with_product_ref_count"] = int(diagnosis_with_product_ref_result.scalar_one() or 0)
            status["diagnosis_missing_product_ref_count"] = (
                status["diagnosis_total_count"] - status["diagnosis_with_product_ref_count"]
            )

            diagnosis_with_product_id_result = await db.execute(
                select(func.count(AiDiagnosisORM.id)).where(AiDiagnosisORM.product_id.is_not(None))
            )
            status["diagnosis_with_product_id_count"] = int(diagnosis_with_product_id_result.scalar_one() or 0)
            status["diagnosis_missing_product_id_count"] = (
                status["diagnosis_total_count"] - status["diagnosis_with_product_id_count"]
            )

            diagnosis_missing_product_row_result = await db.execute(
                select(func.count(AiDiagnosisORM.id))
                .select_from(AiDiagnosisORM)
                .outerjoin(ResultProductORM, ResultProductORM.id == AiDiagnosisORM.product_ref_id)
                .where(
                    AiDiagnosisORM.product_ref_id.is_not(None),
                    ResultProductORM.id.is_(None),
                )
            )
            status["diagnosis_missing_product_row_count"] = int(
                diagnosis_missing_product_row_result.scalar_one() or 0
            )

            diagnosis_product_identity_mismatch_result = await db.execute(
                select(func.count(AiDiagnosisORM.id))
                .select_from(AiDiagnosisORM)
                .join(ResultProductORM, ResultProductORM.id == AiDiagnosisORM.product_ref_id)
                .where(
                    AiDiagnosisORM.product_id.is_not(None),
                    AiDiagnosisORM.product_id != ResultProductORM.product_id,
                )
            )
            status["diagnosis_product_identity_mismatch_count"] = int(
                diagnosis_product_identity_mismatch_result.scalar_one() or 0
            )

            diagnosis_result_product_mismatch_result = await db.execute(
                select(func.count(AiDiagnosisORM.id))
                .select_from(AiDiagnosisORM)
                .join(DinsarResultORM, DinsarResultORM.id == AiDiagnosisORM.result_id)
                .where(
                    or_(
                        DinsarResultORM.compat_product_id.is_(None),
                        and_(
                            AiDiagnosisORM.product_id.is_not(None),
                            DinsarResultORM.compat_product_id != AiDiagnosisORM.product_id,
                        ),
                    )
                )
            )
            status["diagnosis_result_product_mismatch_count"] = int(
                diagnosis_result_product_mismatch_result.scalar_one() or 0
            )

            result_trace_missing_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(
                    ResultProductORM.catalog_name == "dinsar",
                    or_(
                        ResultProductORM.pair_uid.is_(None),
                        ResultProductORM.network_run_id.is_(None),
                        ResultProductORM.network_edge_id.is_(None),
                        ResultProductORM.policy_version.is_(None),
                    ),
                )
            )
            status["result_trace_missing_count"] = int(
                result_trace_missing_result.scalar_one() or 0
            )

            result_trace_orphan_result = await db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM result_products rp
                    LEFT JOIN pairing_network_runs pnr
                        ON pnr.network_run_id = rp.network_run_id
                    LEFT JOIN pairing_network_edges pne
                        ON pne.id = rp.network_edge_id
                        AND pne.network_run_ref_id = pnr.id
                    WHERE rp.catalog_name = 'dinsar'
                      AND COALESCE(rp.pair_uid, '') <> ''
                      AND COALESCE(rp.network_run_id, '') <> ''
                      AND rp.network_edge_id IS NOT NULL
                      AND (pnr.id IS NULL OR pne.id IS NULL)
                    """
                )
            )
            status["result_trace_orphan_count"] = int(
                result_trace_orphan_result.scalar_one() or 0
            )

            result_trace_pair_mismatch_result = await db.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM result_products rp
                    JOIN pairing_network_runs pnr
                        ON pnr.network_run_id = rp.network_run_id
                    JOIN pairing_network_edges pne
                        ON pne.id = rp.network_edge_id
                        AND pne.network_run_ref_id = pnr.id
                    JOIN pairing_metric_cache pmc
                        ON pmc.id = pne.metric_cache_ref_id
                    WHERE rp.catalog_name = 'dinsar'
                      AND COALESCE(rp.pair_uid, '') <> ''
                      AND COALESCE(pmc.pair_uid, '') <> ''
                      AND rp.pair_uid <> pmc.pair_uid
                    """
                )
            )
            status["result_trace_pair_mismatch_count"] = int(
                result_trace_pair_mismatch_result.scalar_one() or 0
            )

            status["ok"] = all(
                [
                    status["missing_compat_count"] == 0,
                    status["orphan_compat_count"] == 0,
                    status["duplicate_compat_product_count"] == 0,
                    status["annotation_drift_count"] == 0,
                    status["diagnosis_missing_product_row_count"] == 0,
                    status["diagnosis_product_identity_mismatch_count"] == 0,
                    status["diagnosis_result_product_mismatch_count"] == 0,
                    status["result_trace_orphan_count"] == 0,
                    status["result_trace_pair_mismatch_count"] == 0,
                ]
            )
    except Exception as exc:
        status["error"] = str(exc)
    return status


def _probe_directory_status(path: str) -> Dict[str, Any]:
    normalized = str(path or "").strip()
    payload = {
        "path": normalized,
        "exists": False,
        "accessible": False,
        "error": None,
    }
    if not normalized:
        payload["error"] = "empty path"
        return payload

    try:
        if os.path.isdir(normalized):
            payload["exists"] = True
            try:
                with os.scandir(normalized) as iterator:
                    next(iterator, None)
                payload["accessible"] = True
            except Exception as exc:
                payload["error"] = str(exc)
            return payload

        os.stat(normalized)
        payload["error"] = "path exists but is not a directory"
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


def _probe_file_status(path: str) -> Dict[str, Any]:
    normalized = str(path or "").strip()
    payload = {
        "path": normalized,
        "exists": False,
        "accessible": False,
        "error": None,
    }
    if not normalized:
        payload["error"] = "empty path"
        return payload

    try:
        if os.path.isfile(normalized):
            payload["exists"] = True
            try:
                with open(normalized, "rb") as stream:
                    stream.read(1)
                payload["accessible"] = True
            except Exception as exc:
                payload["error"] = str(exc)
            return payload

        os.stat(normalized)
        payload["error"] = "path exists but is not a file"
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


async def _check_source_roots() -> Dict[str, Any]:
    items = []

    for path in split_env_paths(settings.MONITOR_RADAR_DIRS):
        status = _probe_directory_status(path)
        status["role"] = "radar_source"
        items.append(status)

    orbit_dir = str(settings.MONITOR_ORBIT_DIR or "").strip()
    if orbit_dir:
        status = _probe_directory_status(orbit_dir)
        status["role"] = "orbit_source"
        items.append(status)

    for path in split_env_paths(settings.MONITOR_DINSAR_DIRS):
        status = _probe_directory_status(path)
        status["role"] = "dinsar_source"
        items.append(status)

    for path in split_env_paths(settings.GF3_ARCHIVE_SOURCE_DIRS):
        status = _probe_directory_status(path)
        status["role"] = "gf3_archive_source"
        items.append(status)

    for path in split_env_paths(settings.GF3_SOURCE_DIRS):
        status = _probe_directory_status(path)
        status["role"] = (
            "gf3_l1a_source"
            if settings.GF3_LEGACY_GDAL_ENABLED
            else "gf3_legacy_l1a_source_disabled"
        )
        items.append(status)

    for path in split_env_paths(settings.GF3_SARSCAPE_NATIVE_DIRS):
        status = _probe_directory_status(path)
        status["role"] = "gf3_sarscape_native"
        items.append(status)

    for path in split_env_paths(settings.GF3_STORAGE_DIRS):
        status = _probe_directory_status(path)
        status["role"] = "gf3_l2_storage"
        items.append(status)

    runtime_dir = str(getattr(settings, "GF3_SARSCAPE_RUNTIME_DIR", "") or "").strip()
    if runtime_dir:
        status = _probe_directory_status(runtime_dir)
        status["role"] = "gf3_sarscape_runtime"
        items.append(status)

    wrapper_exe = str(settings.GF3_SARSCAPE_WRAPPER_EXE or "").strip()
    if wrapper_exe:
        status = _probe_file_status(wrapper_exe)
        status["role"] = "gf3_sarscape_wrapper"
        items.append(status)

    idlrt_path = str(settings.GF3_SARSCAPE_IDLRT_PATH or "").strip()
    if idlrt_path:
        status = _probe_file_status(idlrt_path)
        status["role"] = "gf3_sarscape_idlrt"
        items.append(status)

    dem_path = str(settings.GF3_SARSCAPE_DEM_PATH or settings.GF3_GEO_DEM_PATH or "").strip()
    if dem_path:
        status = _probe_file_status(dem_path)
        status["role"] = "gf3_sarscape_dem"
        items.append(status)

    configured_count = len(items)
    accessible_count = sum(1 for item in items if item.get("accessible"))
    inaccessible_count = configured_count - accessible_count

    return {
        "ok": inaccessible_count == 0,
        "configured_count": configured_count,
        "accessible_count": accessible_count,
        "inaccessible_count": inaccessible_count,
        "items": items,
    }


async def _check_sar_analysis_ready() -> Dict[str, Any]:
    roots = {
        "ready": _probe_directory_status(settings.SAR_ANALYSIS_READY_ROOT),
        "work": _probe_directory_status(settings.SAR_ANALYSIS_WORK_ROOT),
    }
    for role, payload in roots.items():
        payload["role"] = role

    status: Dict[str, Any] = {
        "ok": False,
        "roots": roots,
        "scenes": {
            "scene_count": 0,
            "analysis_scene_count": 0,
            "missing_file_count": 0,
            "missing_files": [],
        },
        "error": None,
    }
    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            scene_count_result = await db.execute(select(func.count(SARSceneGeoORM.id)))
            status["scenes"]["scene_count"] = int(scene_count_result.scalar_one() or 0)
            analysis_rows_result = await db.execute(
                select(SARSceneGeoORM.id, SARSceneGeoORM.analysis_tif_path)
                .where(SARSceneGeoORM.analysis_tif_path.is_not(None))
                .order_by(SARSceneGeoORM.id.desc())
            )
            analysis_rows = analysis_rows_result.all()
            status["scenes"]["analysis_scene_count"] = len(analysis_rows)

        missing_files = []
        for scene_id, tif_path in analysis_rows:
            path_text = str(tif_path or "").strip()
            if path_text and not os.path.isfile(path_text):
                missing_files.append({"scene_id": scene_id, "path": path_text})
        status["scenes"]["missing_file_count"] = len(missing_files)
        status["scenes"]["missing_files"] = missing_files[:20]

        status["ok"] = (
            all(item.get("accessible") for item in roots.values())
            and status["scenes"]["missing_file_count"] == 0
        )
    except Exception as exc:
        status["error"] = str(exc)
    return status


async def _check_product_packages() -> Dict[str, Any]:
    status = {
        "ok": False,
        "canonical_schema": CANONICAL_PACKAGE_SCHEMA,
        "allowed_schemas": sorted(ALLOWED_PRODUCT_PACKAGE_SCHEMAS),
        "total_count": 0,
        "canonical_count": 0,
        "valid_schema_count": 0,
        "invalid_schema_count": 0,
        "missing_manifest_count": 0,
        "missing_publish_dir_count": 0,
        "missing_processor_count": 0,
        "missing_runtime_count": 0,
        "missing_native_output_count": 0,
        "by_family": {},
        "by_engine": {},
        "error": None,
    }
    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            result = await db.execute(
                select(
                    ResultProductORM.product_family,
                    ResultProductORM.engine_code,
                    ResultProductORM.package_schema,
                    ResultProductORM.processor_code,
                    ResultProductORM.runtime_id,
                    ResultProductORM.manifest_path,
                    ResultProductORM.publish_dir,
                    ResultProductORM.native_output_dir,
                )
            )
            rows = result.all()

        status["total_count"] = len(rows)
        for (
            product_family,
            engine_code,
            package_schema,
            processor_code,
            runtime_id,
            manifest_path,
            publish_dir,
            native_output_dir,
        ) in rows:
            family_key = str(product_family or "unknown").strip() or "unknown"
            engine_key = str(engine_code or "unknown").strip() or "unknown"
            status["by_family"][family_key] = int(status["by_family"].get(family_key, 0)) + 1
            status["by_engine"][engine_key] = int(status["by_engine"].get(engine_key, 0)) + 1

            schema_key = str(package_schema or "").strip()
            if schema_key == CANONICAL_PACKAGE_SCHEMA:
                status["canonical_count"] += 1
            if schema_key in ALLOWED_PRODUCT_PACKAGE_SCHEMAS:
                status["valid_schema_count"] += 1
            else:
                status["invalid_schema_count"] += 1
            if not str(manifest_path or "").strip() or not os.path.isfile(str(manifest_path)):
                status["missing_manifest_count"] += 1
            if not str(publish_dir or "").strip() or not os.path.isdir(str(publish_dir)):
                status["missing_publish_dir_count"] += 1
            if not str(processor_code or "").strip():
                status["missing_processor_count"] += 1
            if engine_key in {"pyint", "gamma"} and not str(runtime_id or "").strip():
                status["missing_runtime_count"] += 1
            if not str(native_output_dir or "").strip():
                status["missing_native_output_count"] += 1

        status["ok"] = all(
            [
                status["missing_manifest_count"] == 0,
                status["missing_publish_dir_count"] == 0,
                status["missing_processor_count"] == 0,
                status["missing_runtime_count"] == 0,
                status["missing_native_output_count"] == 0,
                status["invalid_schema_count"] == 0,
            ]
        )
    except Exception as exc:
        status["error"] = str(exc)
    return status


async def _check_asset_inventory() -> Dict[str, Any]:
    status: Dict[str, Any] = {
        "ok": False,
        "source_roots": {
            "configured_count": 0,
            "accessible_count": 0,
            "inaccessible_count": 0,
            "needs_rescan_count": 0,
            "items": [],
        },
        "orbit_roots": {
            "configured_count": 0,
            "accessible_count": 0,
            "inaccessible_count": 0,
            "needs_rescan_count": 0,
            "items": [],
        },
        "source_assets": {
            "total_count": 0,
            "lt1_count": 0,
            "s1_count": 0,
            "parse_failed_count": 0,
            "by_family": {},
        },
        "orbit_assets": {
            "total_count": 0,
            "lt1_count": 0,
            "s1_count": 0,
            "parse_failed_count": 0,
            "by_family": {},
        },
        "bindings": {
            "scene_count": 0,
            "matched_count": 0,
            "missing_count": 0,
            "ambiguous_count": 0,
        },
        "issues": {
            "open_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "by_code": {},
        },
        "error": None,
    }

    source_paths: List[str] = []
    for value in (
        settings.SOURCE_PRODUCT_DIRS,
        settings.INSAR_STORAGE_DIRS,
        settings.MONITOR_RADAR_DIRS,
    ):
        for path in split_env_paths(value):
            if path not in source_paths:
                source_paths.append(path)

    orbit_paths: List[str] = []
    for value in (
        settings.ORBIT_SOURCE_DIRS,
        settings.MONITOR_ORBIT_DIR,
    ):
        for path in split_env_paths(value):
            if path not in orbit_paths:
                orbit_paths.append(path)

    for path in source_paths:
        item = _probe_directory_status(path)
        item["role"] = "source_product_pool"
        status["source_roots"]["items"].append(item)
    for path in orbit_paths:
        item = _probe_directory_status(path)
        item["role"] = "orbit_asset_pool"
        status["orbit_roots"]["items"].append(item)

    for key in ("source_roots", "orbit_roots"):
        root_status = status[key]
        root_status["configured_count"] = len(root_status["items"])
        root_status["accessible_count"] = sum(1 for item in root_status["items"] if item.get("accessible"))
        root_status["inaccessible_count"] = root_status["configured_count"] - root_status["accessible_count"]

    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            state_rows = (
                await db.execute(
                    select(
                        AssetInventoryStateORM.inventory_type,
                        func.count(AssetInventoryStateORM.id),
                    )
                    .where(AssetInventoryStateORM.needs_rescan == True)  # noqa: E712
                    .group_by(AssetInventoryStateORM.inventory_type)
                )
            ).all()
            for inventory_type, count in state_rows:
                key = "orbit_roots" if str(inventory_type or "").lower().startswith("orbit") else "source_roots"
                status[key]["needs_rescan_count"] += int(count or 0)

            source_family_rows = (
                await db.execute(
                    select(
                        SourceProductAssetORM.satellite_family,
                        func.count(SourceProductAssetORM.id),
                    )
                    .where(
                        SourceProductAssetORM.is_active == True,  # noqa: E712
                        SourceProductAssetORM.source_format != "S1_ZIP",
                    )
                    .group_by(SourceProductAssetORM.satellite_family)
                )
            ).all()
            for family, count in source_family_rows:
                family_key = str(family or "unknown").strip().upper() or "unknown"
                value = int(count or 0)
                status["source_assets"]["by_family"][family_key] = value
                status["source_assets"]["total_count"] += value
            status["source_assets"]["lt1_count"] = int(status["source_assets"]["by_family"].get("LT1", 0))
            status["source_assets"]["s1_count"] = int(status["source_assets"]["by_family"].get("S1", 0))
            source_parse_failed = await db.execute(
                select(func.count(SourceProductAssetORM.id)).where(
                    SourceProductAssetORM.parse_status == "FAILED",
                    SourceProductAssetORM.source_format != "S1_ZIP",
                )
            )
            status["source_assets"]["parse_failed_count"] = int(source_parse_failed.scalar_one() or 0)

            orbit_family_rows = (
                await db.execute(
                    select(
                        OrbitAssetORM.satellite_family,
                        func.count(OrbitAssetORM.id),
                    )
                    .where(OrbitAssetORM.is_active == True)  # noqa: E712
                    .group_by(OrbitAssetORM.satellite_family)
                )
            ).all()
            for family, count in orbit_family_rows:
                family_key = str(family or "unknown").strip().upper() or "unknown"
                value = int(count or 0)
                status["orbit_assets"]["by_family"][family_key] = value
                status["orbit_assets"]["total_count"] += value
            status["orbit_assets"]["lt1_count"] = int(status["orbit_assets"]["by_family"].get("LT1", 0))
            status["orbit_assets"]["s1_count"] = int(status["orbit_assets"]["by_family"].get("S1", 0))
            orbit_parse_failed = await db.execute(
                select(func.count(OrbitAssetORM.id)).where(OrbitAssetORM.parse_status == "FAILED")
            )
            status["orbit_assets"]["parse_failed_count"] = int(orbit_parse_failed.scalar_one() or 0)

            scene_count = await db.execute(select(func.count(SceneOrbitBindingORM.radar_data_id.distinct())))
            status["bindings"]["scene_count"] = int(scene_count.scalar_one() or 0)
            selected_count = await db.execute(
                select(func.count(SceneOrbitBindingORM.id)).where(SceneOrbitBindingORM.selection_status == "SELECTED")
            )
            status["bindings"]["matched_count"] = int(selected_count.scalar_one() or 0)
            missing_count = await db.execute(
                select(func.count(AssetInventoryIssueORM.id)).where(
                    AssetInventoryIssueORM.status == "OPEN",
                    AssetInventoryIssueORM.issue_code == "scene_missing_orbit",
                )
            )
            status["bindings"]["missing_count"] = int(missing_count.scalar_one() or 0)
            ambiguous_count = await db.execute(
                select(func.count(AssetInventoryIssueORM.id)).where(
                    AssetInventoryIssueORM.status == "OPEN",
                    AssetInventoryIssueORM.issue_code == "scene_ambiguous_orbit",
                )
            )
            status["bindings"]["ambiguous_count"] = int(ambiguous_count.scalar_one() or 0)

            issue_rows = (
                await db.execute(
                    select(
                        AssetInventoryIssueORM.severity,
                        func.count(AssetInventoryIssueORM.id),
                    )
                    .where(AssetInventoryIssueORM.status == "OPEN")
                    .group_by(AssetInventoryIssueORM.severity)
                )
            ).all()
            for severity, count in issue_rows:
                severity_key = str(severity or "warning").strip().lower() or "warning"
                value = int(count or 0)
                status["issues"]["open_count"] += value
                if severity_key == "error":
                    status["issues"]["error_count"] += value
                elif severity_key == "warning":
                    status["issues"]["warning_count"] += value

            issue_code_rows = (
                await db.execute(
                    select(
                        AssetInventoryIssueORM.issue_code,
                        func.count(AssetInventoryIssueORM.id),
                    )
                    .where(AssetInventoryIssueORM.status == "OPEN")
                    .group_by(AssetInventoryIssueORM.issue_code)
                )
            ).all()
            status["issues"]["by_code"] = {
                str(code or "unknown"): int(count or 0)
                for code, count in issue_code_rows
            }

        status["ok"] = (
            status["source_roots"]["inaccessible_count"] == 0
            and status["orbit_roots"]["inaccessible_count"] == 0
            and status["source_assets"]["parse_failed_count"] == 0
            and status["orbit_assets"]["parse_failed_count"] == 0
            and status["issues"]["error_count"] == 0
        )
    except Exception as exc:
        status["error"] = str(exc)

    return status


async def _check_wsl_runtime() -> Dict[str, Any]:
    status = {
        "ok": False,
        "shared_distro": wsl_runtime_registry.shared_distro,
        "shared_conda_env_name": wsl_runtime_registry.shared_conda_env_name,
        "shared_python_path": wsl_runtime_registry.shared_python_path,
        "broker_job_root_windows": wsl_runtime_registry.broker_job_root_windows,
        "broker_job_root_exists": os.path.isdir(wsl_runtime_registry.broker_job_root_windows),
        "required_runtime_count": 0,
        "healthy_runtime_count": 0,
        "runtimes": [],
        "error": None,
    }
    try:
        required_by_engine = {
            "isce2": bool(settings.ISCE2_ENABLED),
            "pyint": bool(settings.PYINT_ENABLED),
            "gamma": bool(settings.GAMMA_SBAS_ENABLED),
        }
        for runtime in wsl_runtime_registry.runtimes.values():
            required = bool(required_by_engine.get(runtime.engine_code, False))
            runner_exists = os.path.isfile(runtime.runner_path_windows)
            env_profile_exists = None
            if str(runtime.env_profile_path_windows or "").strip():
                env_profile_exists = os.path.isfile(runtime.env_profile_path_windows)
            python_matches_shared = str(runtime.python_path or "").strip() == str(
                wsl_runtime_registry.shared_python_path or ""
            ).strip()
            distro_matches_shared = str(runtime.distro or "").strip() == str(
                wsl_runtime_registry.shared_distro or ""
            ).strip()
            if runtime.engine_code == "gamma":
                python_matches_shared = bool(str(runtime.python_path or "").strip())
                distro_matches_shared = bool(str(runtime.distro or "").strip())
            runtime_ok = runner_exists and python_matches_shared and distro_matches_shared
            if env_profile_exists is False and required:
                runtime_ok = False
            if required:
                status["required_runtime_count"] += 1
                if runtime_ok:
                    status["healthy_runtime_count"] += 1
            status["runtimes"].append(
                {
                    "runtime_id": runtime.runtime_id,
                    "engine_code": runtime.engine_code,
                    "display_name": runtime.display_name,
                    "required": required,
                    "ok": runtime_ok,
                    "runner_exists": runner_exists,
                    "env_profile_exists": env_profile_exists,
                    "python_matches_shared": python_matches_shared,
                    "distro_matches_shared": distro_matches_shared,
                    "allowed_operations": list(runtime.allowed_operations or ()),
                }
            )
        status["ok"] = bool(status["broker_job_root_exists"]) and (
            status["healthy_runtime_count"] >= status["required_runtime_count"]
        )
    except Exception as exc:
        status["error"] = str(exc)
    return status



STUCK_TASK_THRESHOLD_SECONDS = read_int_env(
    "HEALTH_STUCK_TASK_THRESHOLD_SECONDS",
    3600,
    minimum=300,
    maximum=86400,
)


async def _check_stuck_tasks() -> Dict[str, Any]:
    """Detect RUNNING tasks whose updated_at has not changed for too long."""
    from datetime import timedelta

    from ..database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        now = datetime.utcnow()
        cutoff = now - timedelta(seconds=STUCK_TASK_THRESHOLD_SECONDS)
        result = await db.execute(
            select(SystemTaskORM).where(
                SystemTaskORM.status == "RUNNING",
                SystemTaskORM.updated_at < cutoff,
            ).order_by(SystemTaskORM.updated_at.asc())
        )
        stuck = result.scalars().all()
        items = []
        for task in stuck:
            minutes_stuck = max(0.0, (now - task.updated_at).total_seconds()) / 60.0
            items.append({
                "task_id": task.task_id,
                "task_type": task.task_type,
                "task_name": task.task_name,
                "progress": task.progress,
                "message": task.message,
                "stuck_minutes": round(minutes_stuck, 1),
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "last_updated_at": task.updated_at.isoformat() if task.updated_at else None,
            })
        return {
            "ok": len(items) == 0,
            "stuck_count": len(items),
            "threshold_seconds": STUCK_TASK_THRESHOLD_SECONDS,
            "stuck_tasks": items,
        }

async def get_health_status(
    include_external: bool = True,
    include_details: bool = False,
    full: bool = False,
    refresh: bool = False,
) -> Dict[str, Any]:
    db_status = await _check_database(force_schema_refresh=refresh)
    worker_timeout = read_int_env("JOB_WORKER_HEALTH_TIMEOUT", DEFAULT_WORKER_TIMEOUT_SECONDS, minimum=5, maximum=86400)
    worker_status = await _check_worker(worker_timeout)

    idl_status = get_idl_status()
    idl_ok = bool(idl_status.get("is_installed"))

    nginx_status = {"ok": None}
    ollama_status = {"ok": None}
    if include_external:
        nginx_status = await _check_nginx()
        ollama_status = await _check_ollama()

    result_catalog_status = await _check_result_catalog()
    timeseries_result_catalog_status = await _check_timeseries_result_catalog()
    psinsar_result_catalog_status = timeseries_result_catalog_status
    sbas_insar_result_catalog_status = await _check_sbas_insar_result_catalog()
    dinsar_bridge_status = await _check_dinsar_bridge()
    source_roots_status = await _check_source_roots()
    sar_analysis_ready_status = await _check_sar_analysis_ready()
    product_packages_status = await _check_product_packages()
    asset_inventory_status = await _check_asset_inventory()
    wsl_runtime_status = await _check_wsl_runtime()
    pairing_system_status = await pairing_state_service.get_pairing_system_status()
    stuck_task_status = await _check_stuck_tasks()
    engines_status = {"ok": None, "overall": None, "engines": []}
    if full or include_details:
        engines_status = await _check_dinsar_engines()

    overall_ok = all(
        [
            db_status.get("ok"),
            db_status.get("schema_ok"),
            db_status.get("postgis_ok"),
            worker_status.get("ok"),
            result_catalog_status.get("ok"),
            dinsar_bridge_status.get("ok"),
            source_roots_status.get("ok"),
            sar_analysis_ready_status.get("ok"),
            product_packages_status.get("ok"),
            asset_inventory_status.get("ok"),
            wsl_runtime_status.get("ok"),
            pairing_system_status.get("ok"),
            stuck_task_status.get("ok"),
            (not settings.TIMESERIES_ENABLED) or timeseries_result_catalog_status.get("ok"),
            (not (settings.GAMMA_SBAS_ENABLED or settings.LANDSAR_SBAS_ENABLED))
            or sbas_insar_result_catalog_status.get("ok"),
        ]
    )

    full_payload = {
        "ok": overall_ok,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "database": db_status,
        "worker": worker_status,
        "result_catalog": result_catalog_status,
        "dinsar_result_catalog": result_catalog_status,
        "timeseries_result_catalog": timeseries_result_catalog_status,
        "psinsar_result_catalog": psinsar_result_catalog_status,
        "sbas_insar_result_catalog": sbas_insar_result_catalog_status,
        "catalogs": {
            "dinsar": result_catalog_status,
            "timeseries": timeseries_result_catalog_status,
            "psinsar": psinsar_result_catalog_status,
            "sbas_insar": sbas_insar_result_catalog_status,
        },
        "dinsar_bridge": dinsar_bridge_status,
        "source_roots": source_roots_status,
        "sar_analysis_ready": sar_analysis_ready_status,
        "product_packages": product_packages_status,
        "asset_inventory": asset_inventory_status,
        "wsl_runtime": wsl_runtime_status,
        "pairing_system": pairing_system_status,
        "stuck_tasks": stuck_task_status,
        "idl": {
            "ok": idl_ok,
            "status": idl_status,
        },
        "ollama": ollama_status,
        "nginx": nginx_status,
        "dinsar_engines": engines_status,
    }
    if include_details:
        return full_payload
    return _sanitize_health_status(full_payload)
