from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from geoalchemy2.shape import from_shape
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from shapely.geometry import Polygon, shape

from ..config import settings
from ..models import (
    DinsarProductProfileORM,
    DinsarTaskItemORM,
    PairingMetricCacheORM,
    PairingNetworkEdgeORM,
    PairingNetworkRunORM,
    ResultAssetORM,
    ResultCatalogStateORM,
    ResultIssueORM,
    ResultProductORM,
)
from .manifest_snapshot_service import (
    build_manifest_snapshot,
    evaluate_manifest_reconcile,
    iter_manifest_paths,
)
from .image_service import image_service
from .dinsar_naming import (
    PAIR_META_FILENAME,
    RUN_META_FILENAME,
    build_fallback_pair_key,
    find_json_sidecar,
)
from .dinsar_result_layout_service import (
    RUN_CURRENT_DIRNAME,
    RUN_NATIVE_DIRNAME,
    RUN_PREVIEW_DIRNAME,
    is_path_within_native_dir,
    is_standard_envi_disp_file,
    is_standard_isce2_disp_file,
)
from .product_package_schema import build_canonical_descriptor, normalize_package_manifest
from .product_packaging import build_dinsar_package_manifest


DINSAR_CATALOG_NAME = "dinsar"
JOB_TYPE_PUBLISH_DINSAR_PRODUCTS = "PUBLISH_DINSAR_PRODUCTS"
JOB_TYPE_REBUILD_DINSAR_CATALOG = "REBUILD_DINSAR_CATALOG"
TASK_TYPE_PUBLISH_DINSAR_PRODUCTS = "PUBLISH_DINSAR_PRODUCTS"
TASK_TYPE_REBUILD_DINSAR_CATALOG = "REBUILD_DINSAR_CATALOG"

_DATE_RE = re.compile(r"(\d{8})")
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")
_ISCE2_DISP_RE = re.compile(r"^.+_disp\.(?:tif|tiff)$", re.IGNORECASE)
_ISCE2_SKIP_RE = re.compile(r"^.+_disp_full\.(?:tif|tiff)$", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _slugify(value: Optional[str], *, default: str = "item", max_len: int = 48) -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip()).strip("._")
    if not text:
        text = default
    return text[:max_len]


def _stable_digest(*parts: str, length: int = 12) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _extract_dates_from_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    matches = _DATE_RE.findall(name or "")
    if len(matches) >= 2:
        return matches[0], matches[1]
    if len(matches) == 1:
        return matches[0], None
    return None, None


def _task_name_from_candidate_name(name: str) -> str:
    text = str(name or "").strip()
    for suffix in ("_geo_disp", "_disp"):
        if text.lower().endswith(suffix):
            return text[: -len(suffix)]
    return text


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _runtime_id_for_engine(engine_code: Optional[str]) -> Optional[str]:
    normalized = str(engine_code or "").strip().lower()
    if normalized == "isce2":
        return settings.ISCE2_RUNTIME_ID or None
    if normalized in {"pyint", "gamma"}:
        return settings.PYINT_RUNTIME_ID or None
    return None


def _build_pairing_trace_payload(
    candidate_meta: Dict[str, Any],
    task_item: Optional[DinsarTaskItemORM],
) -> Dict[str, Any]:
    task_pair_uid = getattr(task_item, "scene_pair_uid", None) if task_item is not None else None
    task_network_run_id = getattr(task_item, "network_run_id", None) if task_item is not None else None
    task_network_edge_id = getattr(task_item, "network_edge_id", None) if task_item is not None else None
    task_policy_version = getattr(task_item, "policy_version", None) if task_item is not None else None
    task_selection_strategy = getattr(task_item, "selection_strategy", None) if task_item is not None else None

    candidate_network_edge_id = _coerce_optional_int(candidate_meta.get("network_edge_id"))
    task_network_edge_id = _coerce_optional_int(task_network_edge_id)

    trace = {
        "pair_uid": _first_text(
            candidate_meta.get("pair_uid"),
            candidate_meta.get("scene_pair_uid"),
            task_pair_uid,
        ),
        "network_run_id": _first_text(
            candidate_meta.get("network_run_id"),
            task_network_run_id,
        ),
        "network_edge_id": (
            candidate_network_edge_id
            if candidate_network_edge_id is not None
            else task_network_edge_id
        ),
        "policy_version": _first_text(
            candidate_meta.get("policy_version"),
            task_policy_version,
        ),
        "selection_strategy": _first_text(
            candidate_meta.get("selection_strategy"),
            task_selection_strategy,
        ),
    }
    return {
        key: value
        for key, value in trace.items()
        if value not in (None, "")
    }


def _resolve_candidate_identity(candidate: Dict[str, Any]) -> Dict[str, Any]:
    source_dir = candidate["source_dir"]
    run_meta = find_json_sidecar(source_dir, RUN_META_FILENAME, max_levels=4) or {}
    pair_meta = find_json_sidecar(source_dir, PAIR_META_FILENAME, max_levels=6) or {}
    task_alias = _first_text(
        run_meta.get("task_alias"),
        pair_meta.get("task_alias"),
        candidate.get("task_name"),
    ) or "Task_unknown_unknown"
    pair_key = _first_text(
        run_meta.get("pair_key"),
        pair_meta.get("pair_key"),
    ) or build_fallback_pair_key(task_alias, source_dir)
    run_key = _first_text(run_meta.get("run_key")) or (
        "legacy_" + _stable_digest(candidate.get("engine_code"), pair_key, source_dir, candidate["primary_file"], length=16)
    )
    engine_code = _first_text(run_meta.get("engine_code"), candidate.get("engine_code")) or "unknown"

    resolved: Dict[str, Any] = {
        "engine_code": engine_code,
        "task_name": _first_text(candidate.get("task_name"), task_alias),
        "task_alias": task_alias,
        "pair_key": pair_key,
        "run_key": run_key,
        "profile_code": _first_text(run_meta.get("profile_code")),
        "engine_version": _first_text(run_meta.get("engine_version")),
        "source_root": _first_text(run_meta.get("source_root")),
        "task_dir": _first_text(run_meta.get("task_dir")),
        "work_dir": _first_text(run_meta.get("work_dir")),
        "output_dir": _first_text(run_meta.get("output_dir"), source_dir),
        "native_output_dir": _first_text(run_meta.get("native_output_dir")),
        "started_at": _first_text(run_meta.get("started_at")),
        "finished_at": _first_text(run_meta.get("finished_at")),
        "params": run_meta.get("params") if isinstance(run_meta.get("params"), dict) else {},
        "metrics": run_meta.get("metrics") if isinstance(run_meta.get("metrics"), dict) else {},
    }
    if not resolved["native_output_dir"]:
        native_dir = os.path.join(str(resolved["output_dir"] or source_dir), RUN_NATIVE_DIRNAME)
        if os.path.isdir(native_dir):
            resolved["native_output_dir"] = native_dir
        else:
            resolved["native_output_dir"] = resolved["output_dir"]

    for field in (
        "master_path",
        "slave_path",
        "master_satellite",
        "slave_satellite",
        "master_imaging_date",
        "slave_imaging_date",
        "master_imaging_mode",
        "slave_imaging_mode",
        "master_polarization",
        "slave_polarization",
        "time_baseline_days",
        "spatial_baseline_meters",
    ):
        resolved[field] = run_meta.get(field)
        if resolved[field] in (None, ""):
            resolved[field] = pair_meta.get(field)
    resolved["scene_pair_uid"] = _first_text(
        run_meta.get("scene_pair_uid"),
        pair_meta.get("scene_pair_uid"),
        run_meta.get("pair_uid"),
        pair_meta.get("pair_uid"),
    )
    resolved["pair_uid"] = _first_text(
        run_meta.get("pair_uid"),
        pair_meta.get("pair_uid"),
        resolved["scene_pair_uid"],
    )
    resolved["network_run_id"] = _first_text(
        run_meta.get("network_run_id"),
        pair_meta.get("network_run_id"),
    )
    resolved["network_edge_id"] = _coerce_optional_int(run_meta.get("network_edge_id"))
    if resolved["network_edge_id"] is None:
        resolved["network_edge_id"] = _coerce_optional_int(pair_meta.get("network_edge_id"))
    resolved["policy_version"] = _first_text(
        run_meta.get("policy_version"),
        pair_meta.get("policy_version"),
    )
    resolved["selection_strategy"] = _first_text(
        run_meta.get("selection_strategy"),
        pair_meta.get("selection_strategy"),
    )
    return resolved


def _iter_flat_result_candidates(root_dir: str) -> Iterable[Dict[str, Any]]:
    normalized_root = _normalize_path(root_dir)
    stack = [normalized_root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            rel_name = os.path.relpath(entry.path, normalized_root)
                            rel_parts = [part.lower() for part in rel_name.split(os.sep) if part]
                            if any(
                                part in {
                                    RUN_NATIVE_DIRNAME,
                                    RUN_CURRENT_DIRNAME,
                                    RUN_PREVIEW_DIRNAME,
                                }
                                for part in rel_parts
                            ):
                                continue
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if is_path_within_native_dir(normalized_root, entry.path):
                            continue

                        lower_name = entry.name.lower()
                        if is_standard_envi_disp_file(normalized_root, entry.path):
                            primary_file = os.path.join(os.path.dirname(entry.path), "disp")
                            source_dir = os.path.dirname(os.path.dirname(os.path.dirname(primary_file)))
                            sidecars = []
                            if os.path.isfile(primary_file + ".hdr"):
                                sidecars.append(primary_file + ".hdr")
                            if os.path.isfile(primary_file + ".sml"):
                                sidecars.append(primary_file + ".sml")
                            yield {
                                "engine_code": "envi",
                                "name": "disp",
                                "task_name": "",
                                "source_dir": source_dir,
                                "primary_file": primary_file,
                                "source_files": [primary_file] + sidecars,
                            }
                            continue

                        if is_standard_isce2_disp_file(normalized_root, entry.path):
                            source_dir = os.path.dirname(os.path.dirname(os.path.dirname(entry.path)))
                            source_files = [entry.path]
                            coh_candidates = (
                                os.path.join(source_dir, "assets", "coh", "coh.tif"),
                                os.path.join(source_dir, "assets", "coh", "coh.tiff"),
                            )
                            for coh_path in coh_candidates:
                                if os.path.isfile(coh_path):
                                    source_files.append(coh_path)
                                    break
                            yield {
                                "engine_code": "isce2",
                                "name": os.path.splitext(entry.name)[0],
                                "task_name": "",
                                "source_dir": source_dir,
                                "primary_file": entry.path,
                                "source_files": source_files,
                            }
                            continue

                        if lower_name.endswith(".hdr"):
                            base_name, _ = os.path.splitext(entry.name)
                            if not base_name.lower().endswith("_disp"):
                                continue
                            primary_file = os.path.join(os.path.dirname(entry.path), base_name)
                            if not os.path.isfile(primary_file):
                                continue
                            sidecars = [entry.path]
                            sml_path = primary_file + ".sml"
                            if os.path.isfile(sml_path):
                                sidecars.append(sml_path)
                            yield {
                                "engine_code": "envi",
                                "name": base_name,
                                "task_name": _task_name_from_candidate_name(base_name),
                                "source_dir": os.path.dirname(primary_file),
                                "primary_file": primary_file,
                                "source_files": [primary_file] + sidecars,
                            }
                            continue

                        if not lower_name.endswith((".tif", ".tiff")):
                            continue
                        if _ISCE2_SKIP_RE.match(entry.name):
                            continue
                        if not _ISCE2_DISP_RE.match(entry.name):
                            continue

                        base_name, ext = os.path.splitext(entry.name)
                        task_name = _task_name_from_candidate_name(base_name)
                        source_files = [entry.path]
                        for coh_name in (
                            f"{task_name}_coh{ext}",
                            f"{task_name}_coh.tif",
                            f"{task_name}_coh.tiff",
                        ):
                            coh_path = os.path.join(os.path.dirname(entry.path), coh_name)
                            if os.path.isfile(coh_path):
                                source_files.append(coh_path)
                                break

                        yield {
                            "engine_code": "isce2",
                            "name": base_name,
                            "task_name": task_name,
                            "source_dir": os.path.dirname(entry.path),
                            "primary_file": entry.path,
                            "source_files": source_files,
                        }
                    except OSError:
                        continue
        except OSError:
            continue


def _ensure_directory(path: str) -> str:
    normalized = _normalize_path(path)
    os.makedirs(normalized, exist_ok=True)
    return normalized


def _copy_file_if_needed(src: str, dst: str) -> str:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(dst):
        try:
            if os.path.getsize(src) == os.path.getsize(dst):
                return "skipped"
        except OSError:
            pass
        shutil.copy2(src, dst)
        return "overwritten"
    shutil.copy2(src, dst)
    return "copied"


def _resolve_relative_path(base_dir: str, relative_path: str) -> str:
    base_dir = _normalize_path(base_dir)
    target = _normalize_path(os.path.join(base_dir, relative_path))
    if not target.startswith(base_dir + os.sep) and target != base_dir:
        raise ValueError(f"Invalid relative path outside package: {relative_path}")
    return target


def _is_path_within(base_dir: str, candidate_path: str) -> bool:
    base = _normalize_path(base_dir)
    candidate = _normalize_path(candidate_path)
    try:
        return os.path.commonpath([base, candidate]) == base
    except ValueError:
        return False


def _build_bbox_polygon(
    min_lon: Optional[float],
    min_lat: Optional[float],
    max_lon: Optional[float],
    max_lat: Optional[float],
):
    if None in (min_lon, min_lat, max_lon, max_lat):
        return None
    return Polygon(
        [
            (min_lon, min_lat),
            (max_lon, min_lat),
            (max_lon, max_lat),
            (min_lon, max_lat),
            (min_lon, min_lat),
        ]
    )


class ResultCatalogService:
    def get_publish_root(self, publish_root: Optional[str] = None) -> str:
        return _ensure_directory(publish_root or settings.DINSAR_PRODUCT_DIR)

    async def _get_or_create_catalog_state(
        self,
        db: AsyncSession,
        *,
        storage_root: Optional[str] = None,
    ) -> ResultCatalogStateORM:
        root = self.get_publish_root(storage_root)
        result = await db.execute(
            select(ResultCatalogStateORM).where(
                ResultCatalogStateORM.catalog_name == DINSAR_CATALOG_NAME
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = ResultCatalogStateORM(
                catalog_name=DINSAR_CATALOG_NAME,
                product_family="dinsar",
                storage_root=root,
                status="READY",
                needs_rebuild=False,
            )
            db.add(state)
            await db.flush()
        elif state.storage_root != root:
            state.storage_root = root
        if state.product_family != "dinsar":
            state.product_family = "dinsar"
        return state

    async def _lookup_task_item(
        self,
        db: AsyncSession,
        *,
        pair_key: Optional[str] = None,
        task_alias: Optional[str] = None,
        task_name: Optional[str] = None,
    ) -> Optional[DinsarTaskItemORM]:
        if pair_key:
            result = await db.execute(
                select(DinsarTaskItemORM)
                .where(DinsarTaskItemORM.pair_key == pair_key)
                .order_by(DinsarTaskItemORM.id.desc())
                .limit(1)
            )
            match = result.scalar_one_or_none()
            if match is not None:
                return match
        alias = task_alias or task_name
        if not alias:
            return None
        result = await db.execute(
            select(DinsarTaskItemORM)
            .where(
                or_(
                    DinsarTaskItemORM.task_alias == alias,
                    DinsarTaskItemORM.task_name == alias,
                )
            )
            .order_by(DinsarTaskItemORM.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _build_pairing_network_detail(
        self,
        db: AsyncSession,
        *,
        product: ResultProductORM,
    ) -> Optional[Dict[str, Any]]:
        network_run_id = str(product.network_run_id or "").strip()
        if not network_run_id:
            return None

        run_result = await db.execute(
            select(PairingNetworkRunORM).where(
                PairingNetworkRunORM.network_run_id == network_run_id
            )
        )
        run = run_result.scalar_one_or_none()
        if run is None:
            return {
                "network_run_id": network_run_id,
                "network_edge_id": product.network_edge_id,
                "pair_uid": product.pair_uid,
                "run_found": False,
                "edge_found": False,
            }

        edge = None
        if product.network_edge_id is not None:
            edge_result = await db.execute(
                select(PairingNetworkEdgeORM).where(
                    PairingNetworkEdgeORM.id == int(product.network_edge_id),
                    PairingNetworkEdgeORM.network_run_ref_id == run.id,
                )
            )
            edge = edge_result.scalar_one_or_none()

        metric = None
        if edge is not None:
            metric_result = await db.execute(
                select(PairingMetricCacheORM).where(
                    PairingMetricCacheORM.id == edge.metric_cache_ref_id
                )
            )
            metric = metric_result.scalar_one_or_none()

        return {
            "network_run_id": run.network_run_id,
            "network_edge_id": product.network_edge_id,
            "pair_uid": product.pair_uid,
            "run_found": True,
            "edge_found": edge is not None,
            "run": {
                "strategy": run.strategy,
                "policy_version": run.policy_version,
                "candidate_count": run.candidate_count,
                "selected_edge_count": run.selected_edge_count,
                "warning_count": run.warning_count,
                "status": run.status,
                "fallback_used": run.fallback_used,
                "created_at": run.created_at,
            },
            "edge": (
                {
                    "id": edge.id,
                    "edge_rank": edge.edge_rank,
                    "selection_reason": edge.selection_reason,
                    "selection_score": edge.selection_score,
                    "selection_meta_json": edge.selection_meta_json,
                    "is_reference_edge": edge.is_reference_edge,
                    "metric_cache_ref_id": edge.metric_cache_ref_id,
                    "created_at": edge.created_at,
                }
                if edge is not None
                else None
            ),
            "metric": (
                {
                    "pair_uid": metric.pair_uid,
                    "master_scene_uid": metric.master_scene_uid,
                    "slave_scene_uid": metric.slave_scene_uid,
                    "master_imaging_date": metric.master_imaging_date,
                    "slave_imaging_date": metric.slave_imaging_date,
                    "master_satellite": metric.master_satellite,
                    "slave_satellite": metric.slave_satellite,
                    "master_imaging_mode": metric.master_imaging_mode,
                    "slave_imaging_mode": metric.slave_imaging_mode,
                    "master_polarization": metric.master_polarization,
                    "slave_polarization": metric.slave_polarization,
                    "time_baseline_days": metric.time_baseline_days,
                    "spatial_baseline_meters": metric.spatial_baseline_meters,
                    "scene_overlap_ratio": metric.scene_overlap_ratio,
                    "same_satellite": metric.same_satellite,
                    "same_imaging_mode": metric.same_imaging_mode,
                    "same_polarization": metric.same_polarization,
                    "status": metric.status,
                    "metric_version": metric.metric_version,
                }
                if metric is not None
                else None
            ),
        }

    def _build_product_id(self, engine_code: str, pair_key: str, run_key: str, primary_file: str) -> str:
        digest = _stable_digest(engine_code, pair_key, run_key, _normalize_path(primary_file), length=20)
        return f"dinsar_{_slugify(engine_code, default='engine', max_len=12)}_{digest}"

    def _build_manifest(
        self,
        *,
        package_dir: str,
        product_id: str,
        display_name: str,
        engine_code: str,
        source_primary_path: str,
        source_dir: str,
        candidate_meta: Dict[str, Any],
        task_item: Optional[DinsarTaskItemORM],
        primary_asset_relative: str,
        preview_relative: Optional[str],
        asset_rows: List[Dict[str, Any]],
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        master_date = getattr(task_item, "master_imaging_date", None) or candidate_meta.get("master_imaging_date")
        slave_date = getattr(task_item, "slave_imaging_date", None) or candidate_meta.get("slave_imaging_date")
        if not master_date or not slave_date:
            fallback_master, fallback_slave = _extract_dates_from_name(display_name)
            master_date = master_date or fallback_master
            slave_date = slave_date or fallback_slave

        published_at = _utcnow().isoformat() + "Z"
        profile_params = candidate_meta.get("params") if isinstance(candidate_meta.get("params"), dict) else {}
        profile_metrics = candidate_meta.get("metrics") if isinstance(candidate_meta.get("metrics"), dict) else {}
        pairing_trace = _build_pairing_trace_payload(candidate_meta, task_item)
        summary_payload = {
            "primary_asset_relative": primary_asset_relative,
            "preview_relative": preview_relative,
        }
        if pairing_trace:
            summary_payload["pairing_trace"] = pairing_trace
        return build_dinsar_package_manifest(
            product_id=product_id,
            display_name=display_name,
            task_name=candidate_meta.get("task_alias") or display_name,
            engine_code=engine_code,
            engine_version=candidate_meta.get("engine_version") or "",
            processor_code=candidate_meta.get("profile_code") or engine_code,
            profile_code=candidate_meta.get("profile_code"),
            runtime_id=_runtime_id_for_engine(engine_code),
            source_primary_path=source_primary_path,
            source_dir=source_dir,
            publish_dir=package_dir,
            identity={
                "pair_key": candidate_meta.get("pair_key"),
                "task_alias": candidate_meta.get("task_alias") or display_name,
                "run_key": candidate_meta.get("run_key"),
            },
            source={
                "source_root": candidate_meta.get("source_root"),
                "task_dir": candidate_meta.get("task_dir"),
                "work_dir": candidate_meta.get("work_dir"),
                "output_dir": candidate_meta.get("output_dir"),
                "native_output_dir": candidate_meta.get("native_output_dir") or candidate_meta.get("output_dir"),
            },
            run={
                "engine_code": engine_code,
                "profile_code": candidate_meta.get("profile_code"),
                "source_root": candidate_meta.get("source_root"),
                "task_dir": candidate_meta.get("task_dir"),
                "work_dir": candidate_meta.get("work_dir"),
                "output_dir": candidate_meta.get("output_dir"),
                "native_output_dir": candidate_meta.get("native_output_dir") or candidate_meta.get("output_dir"),
                "started_at": candidate_meta.get("started_at"),
                "finished_at": candidate_meta.get("finished_at"),
                "params": profile_params,
                "metrics": profile_metrics,
            },
            temporal={
                "master_imaging_date": master_date,
                "slave_imaging_date": slave_date,
                "produced_at": candidate_meta.get("finished_at") or candidate_meta.get("started_at"),
                "published_at": published_at,
            },
            spatial={
                "min_lon": meta.get("min_lon"),
                "min_lat": meta.get("min_lat"),
                "max_lon": meta.get("max_lon"),
                "max_lat": meta.get("max_lat"),
                "coverage_polygon": meta.get("coverage_polygon"),
            },
            dinsar_profile={
                "master_path": getattr(task_item, "master_path", None) or candidate_meta.get("master_path"),
                "slave_path": getattr(task_item, "slave_path", None) or candidate_meta.get("slave_path"),
                "master_satellite": getattr(task_item, "master_satellite", None) or candidate_meta.get("master_satellite"),
                "slave_satellite": getattr(task_item, "slave_satellite", None) or candidate_meta.get("slave_satellite"),
                "master_imaging_date": master_date,
                "slave_imaging_date": slave_date,
                "master_imaging_mode": getattr(task_item, "master_imaging_mode", None) or candidate_meta.get("master_imaging_mode"),
                "slave_imaging_mode": getattr(task_item, "slave_imaging_mode", None) or candidate_meta.get("slave_imaging_mode"),
                "master_polarization": getattr(task_item, "master_polarization", None) or candidate_meta.get("master_polarization"),
                "slave_polarization": getattr(task_item, "slave_polarization", None) or candidate_meta.get("slave_polarization"),
                "orbit_direction": None,
                "time_baseline_days": getattr(task_item, "time_baseline_days", None) or candidate_meta.get("time_baseline_days"),
                "spatial_baseline_meters": getattr(task_item, "spatial_baseline_meters", None) or candidate_meta.get("spatial_baseline_meters"),
                "grid_size_m": profile_params.get("target_grid_size_m") or profile_params.get("geocoding_pixel_size_m"),
                "radar_wavelength": profile_params.get("wavelength"),
                "orbit_clip_margin": profile_params.get("orbit_margin_sec"),
                "bbox_margin": profile_params.get("bbox_margin"),
                "coherence_threshold": profile_params.get("coh_threshold"),
                "params": profile_params,
                "metrics": profile_metrics,
            },
            pairing_trace=pairing_trace,
            labels={
                "ai_score": None,
                "user_label": None,
            },
            summary=summary_payload,
            assets=asset_rows,
            issues=[],
        )

    async def publish_from_sources(
        self,
        db: AsyncSession,
        source_dirs: List[str],
        *,
        publish_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_root = self.get_publish_root(publish_root)
        processed = 0
        copied = 0
        skipped = 0
        overwritten = 0
        failed = 0
        details: List[Dict[str, Any]] = []

        for raw_root in source_dirs:
            if not raw_root:
                continue
            source_root = _normalize_path(raw_root)
            if not os.path.isdir(source_root):
                details.append(
                    {
                        "source_root": source_root,
                        "status": "error",
                        "message": "source directory not found",
                    }
                )
                failed += 1
                continue

            for candidate in _iter_flat_result_candidates(source_root):
                processed += 1
                primary_file = candidate["primary_file"]
                candidate_meta = _resolve_candidate_identity(candidate)
                task_name = candidate_meta["task_name"]
                task_alias = candidate_meta["task_alias"]
                pair_key = candidate_meta["pair_key"]
                run_key = candidate_meta["run_key"]
                product_id = self._build_product_id(
                    candidate_meta["engine_code"],
                    pair_key,
                    run_key,
                    primary_file,
                )
                package_dir = _ensure_directory(os.path.join(target_root, pair_key, "runs", run_key))
                source_dir = _normalize_path(candidate["source_dir"])
                in_place_source = _is_path_within(package_dir, source_dir)
                task_item = await self._lookup_task_item(
                    db,
                    pair_key=pair_key,
                    task_alias=task_alias,
                    task_name=task_name,
                )

                try:
                    meta = await asyncio.to_thread(image_service.extract_footprint, primary_file)
                except Exception as exc:
                    failed += 1
                    details.append(
                        {
                            "product_id": product_id,
                            "task_name": task_name,
                            "status": "error",
                            "message": f"footprint extract failed: {exc}",
                        }
                    )
                    continue

                disp_dir = os.path.join(package_dir, "assets", "disp")
                preview_dir = _ensure_directory(os.path.join(package_dir, "preview"))
                asset_rows: List[Dict[str, Any]] = []

                if candidate["engine_code"] == "envi":
                    source_primary = _normalize_path(candidate["source_files"][0])
                    if in_place_source:
                        target_primary = source_primary
                    else:
                        _ensure_directory(disp_dir)
                        target_base = os.path.join(disp_dir, "disp")
                        target_primary = target_base
                        for src_path in candidate["source_files"]:
                            suffix = src_path[len(source_primary):]
                            dst_path = target_base + suffix
                            op = _copy_file_if_needed(src_path, dst_path)
                            if op == "copied":
                                copied += 1
                            elif op == "overwritten":
                                overwritten += 1
                            else:
                                skipped += 1
                    asset_rows.append(
                        {
                            "role": "disp",
                            "asset_name": os.path.basename(target_primary) or "disp",
                            "relative_path": os.path.relpath(target_primary, package_dir),
                            "format": "envi",
                            "media_type": "application/octet-stream",
                            "is_required": True,
                            "is_primary": True,
                        }
                    )
                    hdr_path = target_primary + ".hdr"
                    if os.path.isfile(hdr_path):
                        asset_rows.append(
                            {
                                "role": "disp_header",
                                "asset_name": os.path.basename(hdr_path),
                                "relative_path": os.path.relpath(hdr_path, package_dir),
                                "format": "hdr",
                                "media_type": "text/plain",
                                "is_required": True,
                                "is_primary": False,
                            }
                        )
                    sml_path = target_primary + ".sml"
                    if os.path.isfile(sml_path):
                        asset_rows.append(
                            {
                                "role": "disp_sidecar",
                                "asset_name": os.path.basename(sml_path),
                                "relative_path": os.path.relpath(sml_path, package_dir),
                                "format": "sml",
                                "media_type": "text/plain",
                                "is_required": False,
                                "is_primary": False,
                            }
                        )
                else:
                    if in_place_source:
                        target_primary = _normalize_path(primary_file)
                    else:
                        _ensure_directory(disp_dir)
                        target_primary = os.path.join(disp_dir, "disp.tif")
                        op = _copy_file_if_needed(primary_file, target_primary)
                        if op == "copied":
                            copied += 1
                        elif op == "overwritten":
                            overwritten += 1
                        else:
                            skipped += 1
                    asset_rows.append(
                        {
                            "role": "disp",
                            "asset_name": os.path.basename(target_primary),
                            "relative_path": os.path.relpath(target_primary, package_dir),
                            "format": "geotiff",
                            "media_type": "image/tiff",
                            "is_required": True,
                            "is_primary": True,
                        }
                    )
                    if len(candidate["source_files"]) > 1:
                        source_coh = candidate["source_files"][1]
                        if in_place_source:
                            target_coh = _normalize_path(source_coh)
                        else:
                            coh_dir = _ensure_directory(os.path.join(package_dir, "assets", "coh"))
                            coh_ext = os.path.splitext(source_coh)[1] or ".tif"
                            target_coh = os.path.join(coh_dir, f"coh{coh_ext}")
                            op = _copy_file_if_needed(source_coh, target_coh)
                            if op == "copied":
                                copied += 1
                            elif op == "overwritten":
                                overwritten += 1
                            else:
                                skipped += 1
                        asset_rows.append(
                            {
                                "role": "coh",
                                "asset_name": os.path.basename(target_coh),
                                "relative_path": os.path.relpath(target_coh, package_dir),
                                "format": "geotiff",
                                "media_type": "image/tiff",
                                "is_required": False,
                                "is_primary": False,
                            }
                        )

                thumb_path = os.path.join(preview_dir, "thumb.webp")
                thumb_ok = await asyncio.to_thread(
                    image_service.create_cached_image,
                    target_primary,
                    thumb_path,
                    (960, 960),
                )
                if thumb_ok:
                    asset_rows.append(
                        {
                            "role": "thumb",
                            "asset_name": "thumb.webp",
                            "relative_path": os.path.relpath(thumb_path, package_dir),
                            "format": "webp",
                            "media_type": "image/webp",
                            "is_required": True,
                            "is_primary": False,
                        }
                    )

                manifest = self._build_manifest(
                    package_dir=package_dir,
                    product_id=product_id,
                    display_name=task_alias,
                    engine_code=candidate_meta["engine_code"],
                    source_primary_path=primary_file,
                    source_dir=candidate["source_dir"],
                    candidate_meta=candidate_meta,
                    task_item=task_item,
                    primary_asset_relative=os.path.relpath(target_primary, package_dir),
                    preview_relative=os.path.relpath(thumb_path, package_dir) if thumb_ok else None,
                    asset_rows=asset_rows,
                    meta=meta,
                )
                manifest_path = os.path.join(package_dir, "manifest.json")
                with open(manifest_path, "w", encoding="utf-8") as fp:
                    json.dump(manifest, fp, ensure_ascii=False, indent=2)

                details.append(
                    {
                        "product_id": product_id,
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "pair_key": pair_key,
                        "run_key": run_key,
                        "engine_code": candidate_meta["engine_code"],
                        "package_dir": package_dir,
                        "in_place": in_place_source,
                        "thumb_created": thumb_ok,
                        "status": "ok",
                    }
                )

        await db.commit()
        return {
            "publish_root": target_root,
            "processed": processed,
            "copied": copied,
            "skipped": skipped,
            "overwritten": overwritten,
            "failed": failed,
            "details": details,
        }

    def _iter_manifest_paths(self, publish_root: str) -> List[str]:
        return iter_manifest_paths(self.get_publish_root(publish_root))

    def _load_manifest(self, manifest_path: str) -> Dict[str, Any]:
        with open(manifest_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        normalized = normalize_package_manifest(payload)
        if str(normalized.get("product_family") or "").strip().lower() != "dinsar":
            raise ValueError("manifest product_family is not dinsar")
        if not str(normalized.get("product_id") or "").strip():
            raise ValueError("manifest product_id is empty")
        return normalized

    def _build_rows_from_manifest(
        self,
        manifest_path: str,
        manifest: Dict[str, Any],
    ) -> ResultProductORM:
        package_dir = _normalize_path(os.path.dirname(manifest_path))
        spatial = manifest.get("spatial") or {}
        coverage_polygon = spatial.get("coverage_polygon")
        poly = None
        if coverage_polygon:
            try:
                poly = shape(coverage_polygon)
                if not poly.is_valid:
                    poly = poly.buffer(0)
            except Exception:
                poly = None
        if poly is None:
            poly = _build_bbox_polygon(
                spatial.get("min_lon"),
                spatial.get("min_lat"),
                spatial.get("max_lon"),
                spatial.get("max_lat"),
            )

        manifest_issues = list(manifest.get("issues") or [])
        assets_payload = list(manifest.get("assets") or [])
        summary = manifest.get("summary") or {}
        identity = manifest.get("identity") or {}
        run_payload = manifest.get("run") or {}
        source = manifest.get("source") or {}
        temporal = manifest.get("temporal") or {}
        profile_payload = manifest.get("dinsar_profile") or {}
        labels = manifest.get("labels") or {}
        pairing_trace = manifest.get("pairing_trace") or {}
        processor_payload = manifest.get("processor") or {}
        runtime_payload = manifest.get("runtime") or {}
        canonical_payload = manifest.get("canonical") or build_canonical_descriptor(
            assets_payload,
            product_family="dinsar",
        )

        summary_json: Optional[Dict[str, Any]] = None
        if summary or identity or run_payload or pairing_trace or canonical_payload:
            summary_json = {
                **summary,
                "identity": identity,
                "run": run_payload,
            }
            if processor_payload:
                summary_json["processor"] = processor_payload
            if runtime_payload:
                summary_json["runtime"] = runtime_payload
            if canonical_payload:
                summary_json["canonical"] = canonical_payload
            if pairing_trace:
                summary_json["pairing_trace"] = pairing_trace

        product = ResultProductORM(
            product_id=str(manifest.get("product_id")).strip(),
            catalog_name=str(manifest.get("catalog_name") or DINSAR_CATALOG_NAME).strip() or DINSAR_CATALOG_NAME,
            product_family=str(manifest.get("product_family") or "dinsar").strip() or "dinsar",
            product_type=str(manifest.get("product_type") or "dinsar_interferogram").strip() or "dinsar_interferogram",
            display_name=str(manifest.get("display_name") or manifest.get("task_name") or manifest.get("product_id")),
            task_name=str(manifest.get("task_name") or manifest.get("display_name") or "").strip() or None,
            task_alias=str(identity.get("task_alias") or manifest.get("task_name") or "").strip() or None,
            pair_key=str(identity.get("pair_key") or "").strip() or None,
            stack_key=str(identity.get("stack_key") or "").strip() or None,
            pair_uid=str(pairing_trace.get("pair_uid") or "").strip() or None,
            run_key=str(identity.get("run_key") or "").strip() or None,
            network_run_id=str(pairing_trace.get("network_run_id") or "").strip() or None,
            network_edge_id=_coerce_optional_int(pairing_trace.get("network_edge_id")),
            policy_version=str(pairing_trace.get("policy_version") or "").strip() or None,
            selection_strategy=str(pairing_trace.get("selection_strategy") or "").strip() or None,
            profile_code=str(processor_payload.get("profile_code") or run_payload.get("profile_code") or "").strip() or None,
            engine_code=str(((manifest.get("engine") or {}).get("code")) or "unknown"),
            engine_version=str(((manifest.get("engine") or {}).get("version")) or "") or None,
            package_schema=str(manifest.get("schema_version") or "").strip() or None,
            package_layout=str(manifest.get("package_layout") or "").strip() or None,
            processor_code=str(processor_payload.get("code") or manifest.get("processor_code") or "").strip() or None,
            runtime_id=str(runtime_payload.get("runtime_id") or manifest.get("runtime_id") or "").strip() or None,
            status="READY",
            health_status="OK",
            publish_dir=package_dir,
            manifest_path=_normalize_path(manifest_path),
            source_primary_path=source.get("primary_path"),
            native_output_dir=source.get("native_output_dir"),
            preview_path=None,
            primary_asset_path=None,
            summary_json=summary_json,
            tags_json=manifest.get("tags"),
            ai_score=labels.get("ai_score"),
            user_label=labels.get("user_label"),
            min_lon=spatial.get("min_lon"),
            min_lat=spatial.get("min_lat"),
            max_lon=spatial.get("max_lon"),
            max_lat=spatial.get("max_lat"),
            geom=from_shape(poly, srid=4326) if poly is not None else None,
            coverage_polygon=coverage_polygon,
            produced_at=_parse_datetime(
                temporal.get("produced_at")
                or run_payload.get("finished_at")
                or run_payload.get("started_at")
            ),
            published_at=_parse_datetime(temporal.get("published_at")),
            registered_at=_utcnow(),
        )

        product.profile = DinsarProductProfileORM(
            master_path=profile_payload.get("master_path"),
            slave_path=profile_payload.get("slave_path"),
            master_satellite=profile_payload.get("master_satellite"),
            slave_satellite=profile_payload.get("slave_satellite"),
            master_imaging_date=profile_payload.get("master_imaging_date"),
            slave_imaging_date=profile_payload.get("slave_imaging_date"),
            master_imaging_mode=profile_payload.get("master_imaging_mode"),
            slave_imaging_mode=profile_payload.get("slave_imaging_mode"),
            master_polarization=profile_payload.get("master_polarization"),
            slave_polarization=profile_payload.get("slave_polarization"),
            orbit_direction=profile_payload.get("orbit_direction"),
            time_baseline_days=profile_payload.get("time_baseline_days"),
            spatial_baseline_meters=profile_payload.get("spatial_baseline_meters"),
            grid_size_m=profile_payload.get("grid_size_m"),
            radar_wavelength=profile_payload.get("radar_wavelength"),
            orbit_clip_margin=profile_payload.get("orbit_clip_margin"),
            bbox_margin=profile_payload.get("bbox_margin"),
            coherence_threshold=profile_payload.get("coherence_threshold"),
            params_json=profile_payload.get("params"),
            metrics_json=profile_payload.get("metrics"),
        )

        has_warn = False
        has_error = False
        preview_role = str(canonical_payload.get("preview_asset_role") or "").strip() or "thumb"
        for asset_payload in assets_payload:
            relative_path = str(asset_payload.get("relative_path") or "").strip()
            if not relative_path:
                continue
            absolute_path = _resolve_relative_path(package_dir, relative_path)
            exists_flag = os.path.exists(absolute_path)
            try:
                file_size = os.path.getsize(absolute_path) if exists_flag else None
            except OSError:
                file_size = None
            asset = ResultAssetORM(
                asset_role=str(asset_payload.get("role") or "asset"),
                asset_name=str(asset_payload.get("asset_name") or os.path.basename(relative_path)),
                relative_path=relative_path,
                absolute_path=absolute_path,
                format=asset_payload.get("format"),
                media_type=asset_payload.get("media_type"),
                is_required=bool(asset_payload.get("is_required")),
                is_primary=bool(asset_payload.get("is_primary")),
                exists_flag=exists_flag,
                file_size=file_size,
                checksum_sha256=asset_payload.get("checksum_sha256"),
                band_count=asset_payload.get("band_count"),
                width=asset_payload.get("width"),
                height=asset_payload.get("height"),
                srid=asset_payload.get("srid"),
                nodata=asset_payload.get("nodata"),
            )
            product.assets.append(asset)
            if asset.is_primary:
                product.primary_asset_path = absolute_path
            if asset.asset_role == preview_role:
                product.preview_path = absolute_path
            if asset.is_required and not exists_flag:
                has_error = True
                product.issues.append(
                    ResultIssueORM(
                        issue_code="MISSING_REQUIRED_ASSET",
                        severity="ERROR",
                        status="OPEN",
                        scope="file",
                        message=f"required asset missing: {relative_path}",
                        repair_action="rebuild_asset",
                        repair_payload={"relative_path": relative_path},
                        asset=asset,
                    )
                )

        for issue_payload in manifest_issues:
            severity = str(issue_payload.get("severity") or "WARN").upper()
            if severity == "ERROR":
                has_error = True
            elif severity == "WARN":
                has_warn = True
            product.issues.append(
                ResultIssueORM(
                    issue_code=str(issue_payload.get("issue_code") or "MANIFEST_ISSUE"),
                    severity=severity,
                    status=str(issue_payload.get("status") or "OPEN"),
                    scope=str(issue_payload.get("scope") or "manifest"),
                    message=str(issue_payload.get("message") or "manifest issue"),
                    repair_action=issue_payload.get("repair_action"),
                    repair_payload=issue_payload.get("repair_payload"),
                )
            )

        if not product.preview_path:
            has_warn = True
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_PREVIEW",
                    severity="WARN",
                    status="OPEN",
                    scope="file",
                    message="preview thumb.webp is missing",
                    repair_action="rebuild_preview",
                )
            )

        if has_error:
            product.status = "QUARANTINED"
            product.health_status = "ERROR"
        elif has_warn:
            product.status = "PARTIAL"
            product.health_status = "WARN"
        return product

    async def rebuild_catalog(
        self,
        db: AsyncSession,
        *,
        publish_root: Optional[str] = None,
        full_rebuild: bool = True,
    ) -> Dict[str, Any]:
        root = self.get_publish_root(publish_root)
        state = await self._get_or_create_catalog_state(db, storage_root=root)
        state.status = "REBUILDING"
        state.last_message = "catalog rebuild in progress"
        state.needs_rebuild = False
        await db.commit()

        snapshot = await asyncio.to_thread(build_manifest_snapshot, root)
        manifest_paths = list(snapshot.manifest_paths)
        if full_rebuild:
            await db.execute(
                delete(ResultProductORM).where(
                    ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
                )
            )
            await db.commit()

        created = 0
        failed = 0
        issue_count = 0
        details: List[Dict[str, Any]] = []

        for manifest_path in manifest_paths:
            try:
                manifest = await asyncio.to_thread(self._load_manifest, manifest_path)
                product = self._build_rows_from_manifest(manifest_path, manifest)
                product_issue_count = len(product.issues)
                db.add(product)
                await db.flush()
                issue_count += product_issue_count
                created += 1
                details.append(
                    {
                        "manifest_path": manifest_path,
                        "product_id": product.product_id,
                        "status": product.status,
                    }
                )
            except Exception as exc:
                failed += 1
                details.append(
                    {
                        "manifest_path": manifest_path,
                        "status": "error",
                        "message": str(exc),
                    }
                )

        await db.commit()

        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
            )
        )
        db_count = int(db_count_result.scalar_one() or 0)
        state.manifest_count = snapshot.manifest_count
        state.manifest_fingerprint = snapshot.tree_fingerprint
        state.db_count = db_count
        state.issue_count = issue_count + failed
        state.needs_rebuild = False
        state.status = "READY" if failed == 0 else "WARN"
        final_issue_count = state.issue_count
        state.last_message = (
            f"catalog rebuild finished: manifests={snapshot.manifest_count}, "
            f"registered={created}, failed={failed}, issues={final_issue_count}"
        )
        now = _utcnow()
        state.last_full_rebuild_at = now
        state.last_incremental_scan_at = now
        state.updated_at = now
        await db.commit()

        compat_result = None
        compat_error = None
        try:
            from .dinsar_compat_service import dinsar_compat_service

            compat_result = await dinsar_compat_service.sync_from_catalog(db)
        except Exception as exc:
            await db.rollback()
            compat_error = str(exc)

        return {
            "publish_root": root,
            "manifest_count": snapshot.manifest_count,
            "manifest_fingerprint": snapshot.tree_fingerprint,
            "registered": created,
            "failed": failed,
            "issue_count": final_issue_count,
            "compat_sync": compat_result,
            "compat_error": compat_error,
            "details": details,
        }

    async def list_products(
        self,
        db: AsyncSession,
        *,
        limit: int = 100,
        offset: int = 0,
        engine_code: Optional[str] = None,
        status: Optional[str] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 100), 500))
        offset = max(0, int(offset or 0))

        stmt = select(ResultProductORM).where(
            ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
        )
        count_stmt = select(func.count(ResultProductORM.id)).where(
            ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
        )

        if engine_code:
            stmt = stmt.where(ResultProductORM.engine_code == engine_code)
            count_stmt = count_stmt.where(ResultProductORM.engine_code == engine_code)
        if status:
            stmt = stmt.where(ResultProductORM.status == status)
            count_stmt = count_stmt.where(ResultProductORM.status == status)
        if query:
            like_value = f"%{query.strip()}%"
            condition = or_(
                ResultProductORM.display_name.ilike(like_value),
                ResultProductORM.product_id.ilike(like_value),
                ResultProductORM.task_name.ilike(like_value),
                ResultProductORM.task_alias.ilike(like_value),
                ResultProductORM.pair_key.ilike(like_value),
                ResultProductORM.run_key.ilike(like_value),
            )
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)

        total_res = await db.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)
        result = await db.execute(
            stmt.order_by(
                ResultProductORM.published_at.desc().nullslast(),
                ResultProductORM.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        items = result.scalars().all()
        return {
            "items": [
                {
                    "id": item.id,
                    "product_id": item.product_id,
                    "display_name": item.display_name,
                    "task_name": item.task_name,
                    "task_alias": item.task_alias,
                    "pair_key": item.pair_key,
                    "pair_uid": item.pair_uid,
                    "run_key": item.run_key,
                    "network_run_id": item.network_run_id,
                    "network_edge_id": item.network_edge_id,
                    "policy_version": item.policy_version,
                    "selection_strategy": item.selection_strategy,
                    "profile_code": item.profile_code,
                    "engine_code": item.engine_code,
                    "package_schema": item.package_schema,
                    "processor_code": item.processor_code,
                    "runtime_id": item.runtime_id,
                    "status": item.status,
                    "health_status": item.health_status,
                    "preview_path": item.preview_path,
                    "primary_asset_path": item.primary_asset_path,
                    "min_lon": item.min_lon,
                    "min_lat": item.min_lat,
                    "max_lon": item.max_lon,
                    "max_lat": item.max_lat,
                    "published_at": item.published_at,
                    "ai_score": item.ai_score,
                    "user_label": item.user_label,
                }
                for item in items
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
        }

    async def get_product_detail(
        self,
        db: AsyncSession,
        *,
        product_db_id: int,
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            select(ResultProductORM).where(ResultProductORM.id == product_db_id)
        )
        product = result.scalar_one_or_none()
        if product is None:
            return None

        asset_result = await db.execute(
            select(ResultAssetORM)
            .where(ResultAssetORM.product_ref_id == product.id)
            .order_by(ResultAssetORM.asset_role.asc(), ResultAssetORM.id.asc())
        )
        issue_result = await db.execute(
            select(ResultIssueORM)
            .where(ResultIssueORM.product_ref_id == product.id)
            .order_by(ResultIssueORM.detected_at.desc(), ResultIssueORM.id.desc())
        )
        profile_result = await db.execute(
            select(DinsarProductProfileORM).where(
                DinsarProductProfileORM.product_ref_id == product.id
            )
        )
        profile = profile_result.scalar_one_or_none()
        pairing_network = await self._build_pairing_network_detail(db, product=product)

        return {
            "id": product.id,
            "product_id": product.product_id,
            "catalog_name": product.catalog_name,
            "product_type": product.product_type,
            "display_name": product.display_name,
            "task_name": product.task_name,
            "task_alias": product.task_alias,
            "pair_key": product.pair_key,
            "pair_uid": product.pair_uid,
            "run_key": product.run_key,
            "network_run_id": product.network_run_id,
            "network_edge_id": product.network_edge_id,
            "policy_version": product.policy_version,
            "selection_strategy": product.selection_strategy,
            "profile_code": product.profile_code,
            "engine_code": product.engine_code,
            "engine_version": product.engine_version,
            "package_schema": product.package_schema,
            "package_layout": product.package_layout,
            "processor_code": product.processor_code,
            "runtime_id": product.runtime_id,
            "status": product.status,
            "health_status": product.health_status,
            "publish_dir": product.publish_dir,
            "manifest_path": product.manifest_path,
            "source_primary_path": product.source_primary_path,
            "native_output_dir": product.native_output_dir,
            "preview_path": product.preview_path,
            "primary_asset_path": product.primary_asset_path,
            "summary_json": product.summary_json,
            "tags_json": product.tags_json,
            "ai_score": product.ai_score,
            "user_label": product.user_label,
            "min_lon": product.min_lon,
            "min_lat": product.min_lat,
            "max_lon": product.max_lon,
            "max_lat": product.max_lat,
            "coverage_polygon": product.coverage_polygon,
            "produced_at": product.produced_at,
            "published_at": product.published_at,
            "registered_at": product.registered_at,
            "updated_at": product.updated_at,
            "identity": {
                "task_alias": product.task_alias,
                "pair_key": product.pair_key,
                "run_key": product.run_key,
            },
            "pairing_trace": {
                "pair_uid": product.pair_uid,
                "network_run_id": product.network_run_id,
                "network_edge_id": product.network_edge_id,
                "policy_version": product.policy_version,
                "selection_strategy": product.selection_strategy,
            },
            "pairing_network": pairing_network,
            "run": ((product.summary_json or {}).get("run") if isinstance(product.summary_json, dict) else None),
            "profile": (
                {
                    "master_path": profile.master_path,
                    "slave_path": profile.slave_path,
                    "master_satellite": profile.master_satellite,
                    "slave_satellite": profile.slave_satellite,
                    "master_imaging_date": profile.master_imaging_date,
                    "slave_imaging_date": profile.slave_imaging_date,
                    "master_imaging_mode": profile.master_imaging_mode,
                    "slave_imaging_mode": profile.slave_imaging_mode,
                    "master_polarization": profile.master_polarization,
                    "slave_polarization": profile.slave_polarization,
                    "orbit_direction": profile.orbit_direction,
                    "time_baseline_days": profile.time_baseline_days,
                    "spatial_baseline_meters": profile.spatial_baseline_meters,
                    "grid_size_m": profile.grid_size_m,
                    "radar_wavelength": profile.radar_wavelength,
                    "orbit_clip_margin": profile.orbit_clip_margin,
                    "bbox_margin": profile.bbox_margin,
                    "coherence_threshold": profile.coherence_threshold,
                    "params_json": profile.params_json,
                    "metrics_json": profile.metrics_json,
                }
                if profile
                else None
            ),
            "assets": [
                {
                    "id": asset.id,
                    "asset_role": asset.asset_role,
                    "asset_name": asset.asset_name,
                    "relative_path": asset.relative_path,
                    "absolute_path": asset.absolute_path,
                    "format": asset.format,
                    "media_type": asset.media_type,
                    "is_required": asset.is_required,
                    "is_primary": asset.is_primary,
                    "exists_flag": asset.exists_flag,
                    "file_size": asset.file_size,
                    "checksum_sha256": asset.checksum_sha256,
                    "band_count": asset.band_count,
                    "width": asset.width,
                    "height": asset.height,
                    "srid": asset.srid,
                    "nodata": asset.nodata,
                }
                for asset in asset_result.scalars().all()
            ],
            "issues": [
                {
                    "id": issue.id,
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "status": issue.status,
                    "scope": issue.scope,
                    "message": issue.message,
                    "repair_action": issue.repair_action,
                    "repair_payload": issue.repair_payload,
                    "detected_at": issue.detected_at,
                    "resolved_at": issue.resolved_at,
                }
                for issue in issue_result.scalars().all()
            ],
        }

    async def get_catalog_status(
        self,
        db: AsyncSession,
        *,
        publish_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        root = self.get_publish_root(publish_root)
        state = await self._get_or_create_catalog_state(db, storage_root=root)
        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(
                ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
            )
        )
        db_count = int(db_count_result.scalar_one() or 0)
        payload = {
            "catalog_name": state.catalog_name,
            "product_family": state.product_family,
            "storage_root": state.storage_root,
            "status": state.status,
            "needs_rebuild": state.needs_rebuild,
            "manifest_count": state.manifest_count,
            "manifest_fingerprint": state.manifest_fingerprint,
            "db_count": db_count,
            "issue_count": state.issue_count,
            "last_message": state.last_message,
            "last_boot_check_at": state.last_boot_check_at,
            "last_full_rebuild_at": state.last_full_rebuild_at,
            "last_incremental_scan_at": state.last_incremental_scan_at,
        }
        await db.commit()
        return payload

    async def bootstrap_catalog_on_startup(self) -> Dict[str, Any]:
        return await self.bootstrap_catalog_on_startup_clean()

        from ..database import AsyncSessionLocal
        from .job_queue_service import job_queue_service
        from .task_service import task_service

        if AsyncSessionLocal is None:
            raise RuntimeError("Database session factory is not initialized.")

        async with AsyncSessionLocal() as db:
            root = self.get_publish_root()
            state = await self._get_or_create_catalog_state(db, storage_root=root)
            snapshot = await asyncio.to_thread(build_manifest_snapshot, root)
            db_count_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(
                    ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
                )
            )
            db_count = int(db_count_result.scalar_one() or 0)
            reconcile = evaluate_manifest_reconcile(
                manifest_count=snapshot.manifest_count,
                db_count=db_count,
                current_fingerprint=snapshot.tree_fingerprint,
                indexed_fingerprint=state.manifest_fingerprint,
            )
            needs_rebuild = bool(reconcile["needs_rebuild"])

            state.manifest_count = snapshot.manifest_count
            state.db_count = db_count
            state.needs_rebuild = needs_rebuild
            state.last_boot_check_at = _utcnow()
            state.status = "READY" if not needs_rebuild else "WARN"
            if not needs_rebuild:
                state.manifest_fingerprint = snapshot.tree_fingerprint
            state.last_message = (
                "catalog boot check complete"
                if not needs_rebuild
                else (
                    f"catalog rebuild required: manifests={snapshot.manifest_count}, "
                    f"db={db_count}, reasons={','.join(reconcile['reasons'])}"
                )
            )
            await db.commit()

            queued = False
            task_id = None
            compat_result = None
            compat_error = None
            if needs_rebuild and settings.RESULT_CATALOG_AUTO_REBUILD_ON_STARTUP:
                try:
                    task_id = await task_service.create_task(
                        TASK_TYPE_REBUILD_DINSAR_CATALOG,
                        "D-InSAR 结果目录重建",
                        params={"publish_root": root, "full_rebuild": True},
                        db=db,
                    )
                    await job_queue_service.create_job(
                        JOB_TYPE_REBUILD_DINSAR_CATALOG,
                        payload={"publish_root": root, "full_rebuild": True},
                        task_id=task_id,
                        db=db,
                    )
                    queued = True
                    await db.commit()
                except ValueError:
                    await db.rollback()
            elif not needs_rebuild:
                try:
                    from .dinsar_compat_service import dinsar_compat_service

                    compat_result = await dinsar_compat_service.sync_from_catalog(
                        db,
                        prune_missing=False,
                    )
                except Exception as exc:
                    await db.rollback()
                    compat_error = str(exc)

            return {
                "catalog_name": DINSAR_CATALOG_NAME,
                "storage_root": root,
                "manifest_count": snapshot.manifest_count,
                "current_manifest_fingerprint": snapshot.tree_fingerprint,
                "indexed_manifest_fingerprint": state.manifest_fingerprint,
                "db_count": db_count,
                "needs_rebuild": needs_rebuild,
                "reasons": reconcile["reasons"],
                "queued": queued,
                "task_id": task_id,
                "compat_sync": compat_result,
                "compat_error": compat_error,
            }

    async def bootstrap_catalog_on_startup_clean(self) -> Dict[str, Any]:
        from ..database import AsyncSessionLocal
        from .job_queue_service import job_queue_service
        from .task_service import task_service

        if AsyncSessionLocal is None:
            raise RuntimeError("Database session factory is not initialized.")

        async with AsyncSessionLocal() as db:
            root = self.get_publish_root()
            state = await self._get_or_create_catalog_state(db, storage_root=root)
            snapshot = await asyncio.to_thread(build_manifest_snapshot, root)
            db_count_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(
                    ResultProductORM.catalog_name == DINSAR_CATALOG_NAME
                )
            )
            db_count = int(db_count_result.scalar_one() or 0)
            reconcile = evaluate_manifest_reconcile(
                manifest_count=snapshot.manifest_count,
                db_count=db_count,
                current_fingerprint=snapshot.tree_fingerprint,
                indexed_fingerprint=state.manifest_fingerprint,
            )
            needs_rebuild = bool(reconcile["needs_rebuild"])

            state.manifest_count = snapshot.manifest_count
            state.db_count = db_count
            state.needs_rebuild = needs_rebuild
            state.last_boot_check_at = _utcnow()
            state.status = "READY" if not needs_rebuild else "WARN"
            if not needs_rebuild:
                state.manifest_fingerprint = snapshot.tree_fingerprint
            state.last_message = (
                "catalog boot check complete"
                if not needs_rebuild
                else (
                    f"catalog rebuild required: manifests={snapshot.manifest_count}, "
                    f"db={db_count}, reasons={','.join(reconcile['reasons'])}"
                )
            )
            await db.commit()

            queued = False
            task_id = None
            compat_result = None
            compat_error = None
            if needs_rebuild and settings.RESULT_CATALOG_AUTO_REBUILD_ON_STARTUP:
                try:
                    task_id = await task_service.create_task(
                        TASK_TYPE_REBUILD_DINSAR_CATALOG,
                        "D-InSAR 结果目录重建",
                        params={"publish_root": root, "full_rebuild": True},
                        db=db,
                    )
                    await job_queue_service.create_job(
                        JOB_TYPE_REBUILD_DINSAR_CATALOG,
                        payload={"publish_root": root, "full_rebuild": True},
                        task_id=task_id,
                        db=db,
                    )
                    queued = True
                    await db.commit()
                except ValueError:
                    await db.rollback()
            elif not needs_rebuild:
                try:
                    from .dinsar_compat_service import dinsar_compat_service

                    compat_result = await dinsar_compat_service.sync_from_catalog(
                        db,
                        prune_missing=False,
                    )
                except Exception as exc:
                    await db.rollback()
                    compat_error = str(exc)

            return {
                "catalog_name": DINSAR_CATALOG_NAME,
                "storage_root": root,
                "manifest_count": snapshot.manifest_count,
                "current_manifest_fingerprint": snapshot.tree_fingerprint,
                "indexed_manifest_fingerprint": state.manifest_fingerprint,
                "db_count": db_count,
                "needs_rebuild": needs_rebuild,
                "reasons": reconcile["reasons"],
                "queued": queued,
                "task_id": task_id,
                "compat_sync": compat_result,
                "compat_error": compat_error,
            }


result_catalog_service = ResultCatalogService()
