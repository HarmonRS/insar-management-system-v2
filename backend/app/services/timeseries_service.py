from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    PsTaskBatchORM,
    PsTaskItemORM,
    PsTimeseriesRunORM,
    RadarDataORM,
    ResultProductORM,
    TimeseriesStackPlanEdgeORM,
    TimeseriesStackPlanItemORM,
    TimeseriesStackPlanORM,
    WorkflowStepORM,
)
from .psinsar_catalog_service import psinsar_catalog_service
from .product_packaging import upgrade_timeseries_package_manifest
from .product_package_schema import normalize_package_manifest
from .sarscape_sbas_service import execute_template_workflow as execute_sarscape_sbas_template_workflow
from .sarscape_sbas_service import build_preflight_report as build_sarscape_sbas_preflight_report
from .sarscape_sbas_service import build_processor_manifest as build_sarscape_sbas_processor_manifest
from .sarscape_sbas_service import write_processor_manifest as write_sarscape_sbas_processor_manifest
from .task_service import task_service
from .workflow_service import workflow_service
from .wsl_service import check_wsl_environment, run_wsl_command


CATALOG_NAME_PSINSAR = "psinsar"
PREPARED_STACK_SCHEMA = "insar.prepared-sbas-stack/v1"
PREPARED_NETWORK_EDGES_SCHEMA = "insar.prepared-sbas-network-edges/v1"
PREPARED_STACK_MANIFEST_ROLE = "prepared_sbas_stack"
JOB_TYPE_TIMESERIES_PREPARE = "TIMESERIES_PREPARE"
JOB_TYPE_TIMESERIES_STACK_PREP = "TIMESERIES_STACK_PREP"
JOB_TYPE_TIMESERIES_MATERIALIZE = "TIMESERIES_MATERIALIZE"
JOB_TYPE_TIMESERIES_RUN_ISCE2_STACK = "TIMESERIES_RUN_ISCE2_STACK"
JOB_TYPE_TIMESERIES_RUN_MINTPY_SBAS = "TIMESERIES_RUN_MINTPY_SBAS"
JOB_TYPE_TIMESERIES_SARSCAPE_PREFLIGHT = "TIMESERIES_SARSCAPE_PREFLIGHT"
JOB_TYPE_TIMESERIES_RUN_SARSCAPE_SBAS = "TIMESERIES_RUN_SARSCAPE_SBAS"
JOB_TYPE_TIMESERIES_EXPORT_PUBLISH = "TIMESERIES_EXPORT_PUBLISH"
JOB_TYPE_TIMESERIES_REGISTER_PRODUCT = "TIMESERIES_REGISTER_PRODUCT"
TASK_TYPE_TIMESERIES_RUN = "TIMESERIES_RUN"

STATUS_PENDING = "PENDING"
STATUS_PREPARING = "PREPARING"
STATUS_PREPARED = "PREPARED"
STATUS_STACK_PREPARING = "STACK_PREPARING"
STATUS_STACK_PREPARED = "STACK_PREPARED"
STATUS_MATERIALIZING = "MATERIALIZING"
STATUS_MATERIALIZED = "MATERIALIZED"
STATUS_STACK_READY = "STACK_READY"
STATUS_STACK_RUNNING = "STACK_RUNNING"
STATUS_STACK_COMPLETED = "STACK_COMPLETED"
STATUS_MINTPY_RUNNING = "MINTPY_RUNNING"
STATUS_MINTPY_COMPLETED = "MINTPY_COMPLETED"
STATUS_EXPORTING = "EXPORTING"
STATUS_EXPORTED = "EXPORTED"
STATUS_REGISTERING = "REGISTERING"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_FAILED = "FAILED"

REFERENCE_STRATEGY_MIDDLE_BY_DATE = "middle_by_date"
STACK_PREP_WORKFLOW = "interferogram"
SCENE_TILE_RE = re.compile(r"_(E\d+\.\d+)_(N\d+\.\d+)_", re.IGNORECASE)
STACK_RUN_FILE_SEQUENCE = (
    "run_01_reference",
    "run_02_focus_split",
    "run_03_geo2rdr_coarseResamp",
    "run_04_refineSecondaryTiming",
    "run_05_invertMisreg",
    "run_06_fineResamp",
    "run_07_grid_baseline",
    "run_08_igram",
)
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")
TIMESERIES_STEP_STATUS_HINTS = {
    "prepare": STATUS_PENDING,
    "stack_prep_initial": STATUS_PREPARED,
    "materialize": STATUS_STACK_PREPARED,
    "stack_prep_refresh": STATUS_MATERIALIZED,
    "run_isce2_stack": STATUS_STACK_READY,
    "run_mintpy_sbas": STATUS_STACK_COMPLETED,
    "sarscape_processor_preflight": STATUS_PREPARED,
    "run_sarscape_sbas": STATUS_STACK_READY,
    "export_publish_bundle": STATUS_MINTPY_COMPLETED,
    "register_psinsar_product": STATUS_EXPORTED,
}
TIMESERIES_STEP_PROGRESS_HINTS = {
    "prepare": 5,
    "stack_prep_initial": 30,
    "materialize": 65,
    "stack_prep_refresh": 82,
    "run_isce2_stack": 88,
    "run_mintpy_sbas": 93,
    "sarscape_processor_preflight": 45,
    "run_sarscape_sbas": 90,
    "export_publish_bundle": 96,
    "register_psinsar_product": 99,
}
REQUIRED_TIMESERIES_ASSET_ROLES = (
    "timeseries_cube",
    "velocity_map",
    "velocity_geotiff",
    "temporal_coherence",
    "quality_mask",
    "preview_png",
)
REQUIRED_TIMESERIES_EXTRA_FILES = (
    "metadata/smallbaselineApp.cfg",
    "manifest.json",
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def _windows_path_to_wsl_mount(path: Optional[str]) -> Optional[str]:
    text = str(path or "").strip()
    if not text:
        return None
    drive, tail = os.path.splitdrive(os.path.normpath(text))
    if not drive:
        return text.replace("\\", "/")
    drive_letter = drive.rstrip(":").lower()
    normalized_tail = tail.replace("\\", "/")
    return f"/mnt/{drive_letter}/{normalized_tail}"


def _normalize_date(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return text
    return None


def _normalize_lookup_key(path: Optional[str]) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path or "").strip())))


def _stable_digest(*parts: Any, length: int = 10) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _sha256_json(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8", errors="ignore")
    return hashlib.sha256(encoded).hexdigest()


def _slug_fragment(value: Optional[str], *, default: str) -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip()).strip("._").lower()
    return text or default


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _tail_text(value: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _choose_scene_tiff(scene_dir: Path) -> Path:
    candidates = sorted(scene_dir.glob("*.tiff"))
    if not candidates:
        raise FileNotFoundError(f"No .tiff file found in scene directory: {scene_dir}")
    slc_candidates = [path for path in candidates if "_SLC_" in path.name]
    if len(slc_candidates) == 1:
        return slc_candidates[0]
    if len(candidates) == 1:
        return candidates[0]
    return candidates[0]


def _scene_meta_from_tiff(tiff_path: Path) -> Path:
    meta_path = Path(str(tiff_path).replace(".tiff", ".meta.xml"))
    if not meta_path.exists():
        raise FileNotFoundError(f"Missing meta XML for {tiff_path}: {meta_path}")
    return meta_path


def _derive_tile_key(folder_name: str, radar: Optional[RadarDataORM]) -> str:
    match = SCENE_TILE_RE.search(folder_name)
    if match:
        return f"{match.group(1).upper()}_{match.group(2).upper()}"

    lon = getattr(radar, "scene_center_lon", None)
    lat = getattr(radar, "scene_center_lat", None)
    if lon is not None and lat is not None:
        return f"E{float(lon):.1f}_N{float(lat):.1f}"

    return "MULTI_TILE"


def _build_group_key(
    *,
    satellite: Optional[str],
    imaging_mode: Optional[str],
    polarization: Optional[str],
    orbit_direction: Optional[str],
    tile_key: str,
) -> str:
    return "|".join(
        [
            str(satellite or ""),
            str(imaging_mode or ""),
            str(polarization or ""),
            str(orbit_direction or ""),
            str(tile_key or ""),
        ]
    )


def _build_stack_slug(
    *,
    satellite: Optional[str],
    imaging_mode: Optional[str],
    polarization: Optional[str],
    orbit_direction: Optional[str],
    tile_key: str,
    run_id: str,
) -> str:
    tile_fragment = (tile_key or "multi_tile").lower().replace(".", "p")
    return (
        f"{(satellite or 'lt1').lower()}_"
        f"{(imaging_mode or 'stack').lower()}_"
        f"{(polarization or 'unknown').lower()}_"
        f"{(orbit_direction or 'unknown').lower()}_"
        f"{tile_fragment}_{run_id[:8]}"
    )


def _build_stack_key(group_key: Optional[str]) -> str:
    parts = [str(item or "").strip() for item in str(group_key or "").split("|")]
    while len(parts) < 5:
        parts.append("")
    satellite, imaging_mode, polarization, orbit_direction, tile_key = parts[:5]
    return "_".join(
        [
            _slug_fragment(satellite, default="sat"),
            _slug_fragment(imaging_mode, default="mode"),
            _slug_fragment(polarization, default="pol"),
            _slug_fragment(orbit_direction, default="dir"),
            _slug_fragment(str(tile_key or "").replace(".", "p"), default="tile"),
            _stable_digest(group_key, length=10),
        ]
    )


def _compose_publish_dir(run_id: str, stack_key: Optional[str]) -> str:
    stack_fragment = _slug_fragment(stack_key, default="unsorted")
    return _normalize_path(os.path.join(settings.TIMESERIES_PRODUCT_DIR, stack_fragment, "runs", run_id))


def _common_source_root(paths: List[str]) -> Optional[str]:
    normalized = [_normalize_path(path) for path in paths if str(path or "").strip()]
    if not normalized:
        return None
    try:
        return os.path.commonpath(normalized)
    except ValueError:
        return os.path.dirname(normalized[0])


def _write_step_logs(logs_dir: Path, step_name: str, stdout: str, stderr: str) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{step_name}.stdout.log").write_text(stdout or "", encoding="utf-8")
    (logs_dir / f"{step_name}.stderr.log").write_text(stderr or "", encoding="utf-8")


def _probe_local_writable_dir(path: str) -> tuple[bool, str]:
    normalized = _normalize_path(path)
    probe_dir = Path(normalized)
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / f".timeseries_probe_{uuid.uuid4().hex}.tmp"
    try:
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        return True, normalized
    except Exception as exc:
        try:
            if probe_file.exists():
                probe_file.unlink()
        except Exception:
            pass
        return False, f"{normalized}: {exc}"


def _dem_sidecar_candidates(dem_path: str) -> List[str]:
    normalized = _normalize_path(dem_path)
    candidates = [
        normalized,
        normalized + ".xml",
    ]
    return list(dict.fromkeys(candidates))


class TimeseriesService:
    def _derive_paths(self, run_id: str, *, stack_key: Optional[str] = None) -> Dict[str, str]:
        work_root_windows = _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run_id))
        publish_dir_windows = _compose_publish_dir(run_id, stack_key)
        return {
            "work_root_windows": work_root_windows,
            "work_root_wsl": _windows_path_to_wsl_mount(work_root_windows) or "",
            "publish_dir_windows": publish_dir_windows,
            "publish_dir_wsl": _windows_path_to_wsl_mount(publish_dir_windows) or "",
        }

    def _sorted_items(self, items: List[PsTaskItemORM]) -> List[PsTaskItemORM]:
        return sorted(
            items,
            key=lambda item: (
                _normalize_date(item.imaging_date) or "99999999",
                str(item.file_path or ""),
            ),
        )

    def _choose_reference_date(
        self,
        stack_dates: List[str],
        requested: Optional[str],
    ) -> Optional[str]:
        requested_date = _normalize_date(requested)
        if requested_date and requested_date in stack_dates:
            return requested_date
        if not stack_dates:
            return None
        return stack_dates[len(stack_dates) // 2]

    def _normalize_processor_code(self, processor_code: Optional[str]) -> str:
        normalized = str(
            processor_code
            or getattr(settings, "TIMESERIES_DEFAULT_PROCESSOR_CODE", "")
            or "isce2_stack_mintpy"
        ).strip().lower()
        aliases = {
            "isce2": "isce2_stack_mintpy",
            "mintpy": "isce2_stack_mintpy",
            "isce2_stack": "isce2_stack_mintpy",
            "isce2_stack_mintpy": "isce2_stack_mintpy",
            "sarscape": "sarscape_sbas",
            "envi": "sarscape_sbas",
            "sarscape_sbas": "sarscape_sbas",
        }
        if normalized not in aliases:
            raise ValueError(f"Unsupported timeseries processor_code: {processor_code}")
        return aliases[normalized]

    def _normalize_execution_mode(self, execution_mode: Optional[str], processor_code: str) -> str:
        normalized = str(execution_mode or "").strip().lower()
        if not normalized:
            return "preflight_only" if processor_code == "sarscape_sbas" else "full"
        aliases = {
            "full": "full",
            "run": "full",
            "execute": "full",
            "preflight": "preflight_only",
            "preflight_only": "preflight_only",
            "plan": "preflight_only",
            "planning": "preflight_only",
        }
        if normalized not in aliases:
            raise ValueError(f"Unsupported timeseries execution_mode: {execution_mode}")
        return aliases[normalized]

    def _processor_runtime(self, processor_code: str) -> Dict[str, Optional[str]]:
        if processor_code == "sarscape_sbas":
            dem_path = str(settings.IDL_DINSAR_DEM_BASE_FILE or "").strip()
            orbit_pool = str(settings.ORBIT_POOL_ENVI or "").strip()
            return {
                "engine_code": "sarscape",
                "processor_code": "sarscape_sbas",
                "runtime_id": "envi_sarscape",
                "env_name": None,
                "wsl_distro": None,
                "workflow": "sarscape_sbas",
                "dem_path_windows": dem_path or None,
                "dem_path_wsl": None,
                "orbit_pool_windows": orbit_pool or None,
                "orbit_pool_wsl": None,
            }
        return {
            "engine_code": "isce2",
            "processor_code": "isce2_stack_mintpy",
            "runtime_id": settings.ISCE2_RUNTIME_ID or None,
            "env_name": settings.TIMESERIES_ENV_NAME or None,
            "wsl_distro": settings.TIMESERIES_WSL_DISTRO or None,
            "workflow": settings.TIMESERIES_STACK_WORKFLOW,
            "dem_path_windows": str(settings.TIMESERIES_DEM_PATH or "").strip() or None,
            "dem_path_wsl": _windows_path_to_wsl_mount(settings.TIMESERIES_DEM_PATH),
            "orbit_pool_windows": str(settings.TIMESERIES_ORBIT_POOL_ISCE2 or "").strip() or None,
            "orbit_pool_wsl": _windows_path_to_wsl_mount(settings.TIMESERIES_ORBIT_POOL_ISCE2),
        }

    def _scene_payload(self, items: List[PsTaskItemORM]) -> List[Dict[str, Any]]:
        payload: List[Dict[str, Any]] = []
        for item in items:
            remark_json: Optional[Dict[str, Any]] = None
            if str(item.remark or "").strip():
                try:
                    parsed = json.loads(str(item.remark))
                    if isinstance(parsed, dict):
                        remark_json = parsed
                except Exception:
                    remark_json = None
            payload.append(
                {
                    "item_id": item.id,
                    "plan_item_ref_id": item.plan_item_ref_id,
                    "file_path": item.file_path,
                    "satellite": item.satellite,
                    "imaging_date": item.imaging_date,
                    "polarization": item.polarization,
                    "has_orbit_data": bool(item.has_orbit_data),
                    "status": item.status,
                    "remark": item.remark,
                    "remark_json": remark_json,
                }
            )
        return payload

    def _extract_planning_context(self, items: List[PsTaskItemORM]) -> Optional[Dict[str, Any]]:
        scene_records: List[Dict[str, Any]] = []
        summary_payload: Optional[Dict[str, Any]] = None
        for item in items:
            remark_text = str(item.remark or "").strip()
            if not remark_text:
                continue
            try:
                parsed = json.loads(remark_text)
            except Exception:
                continue
            if not isinstance(parsed, dict):
                continue
            if summary_payload is None:
                summary_payload = {
                    key: value
                    for key, value in parsed.items()
                    if key not in {"scene_id", "scene_file_path", "scene_imaging_date", "scene_satellite"}
                }
            scene_records.append(
                {
                    "item_id": item.id,
                    "scene_id": parsed.get("scene_id"),
                    "file_path": parsed.get("scene_file_path") or item.file_path,
                    "imaging_date": parsed.get("scene_imaging_date") or item.imaging_date,
                    "satellite": parsed.get("scene_satellite") or item.satellite,
                }
            )
        if summary_payload is None:
            return None
        summary_payload["scenes"] = scene_records
        summary_payload["scene_count"] = len(scene_records) or int(summary_payload.get("scene_count") or 0)
        summary_payload["stack_dates"] = [
            str(item.get("imaging_date")).strip()
            for item in scene_records
            if str(item.get("imaging_date") or "").strip()
        ] or list(summary_payload.get("stack_dates") or [])
        return summary_payload

    def _build_plan_context(
        self,
        plan: TimeseriesStackPlanORM,
        plan_items: List[TimeseriesStackPlanItemORM],
        plan_edges: Optional[List[TimeseriesStackPlanEdgeORM]] = None,
    ) -> Dict[str, Any]:
        request_params = plan.request_params_json if isinstance(plan.request_params_json, dict) else {}
        ordered_items = sorted(
            plan_items,
            key=lambda item: (int(item.scene_rank or 0), int(item.id or 0)),
        )
        ordered_edges = sorted(
            list(plan_edges or []),
            key=lambda item: (int(item.edge_rank or 0), int(item.id or 0)),
        )
        return {
            "source": "timeseries_stack_plan",
            "plan_id": plan.plan_id,
            "strategy": plan.strategy,
            "direction": plan.direction,
            "scene_count": int(plan.scene_count or len(ordered_items)),
            "stack_key": plan.stack_key,
            "group_key": plan.group_key,
            "request_hash": plan.request_hash,
            "aoi_summary": plan.aoi_summary_json if isinstance(plan.aoi_summary_json, dict) else None,
            "initial_overlap_threshold": request_params.get("initial_overlap_threshold"),
            "final_overlap_threshold": request_params.get("final_overlap_threshold"),
            "time_baseline_min": request_params.get("time_baseline_min"),
            "time_baseline_max": request_params.get("time_baseline_max"),
            "spatial_baseline_max_meters": request_params.get("spatial_baseline_max_meters"),
            "network_overlap_threshold": request_params.get("network_overlap_threshold"),
            "num_connections": request_params.get("num_connections"),
            "network_edge_count": len(ordered_edges),
            "stack_dates": [
                str(item.imaging_date).strip()
                for item in ordered_items
                if str(item.imaging_date or "").strip()
            ],
            "scenes": [
                {
                    "plan_item_id": item.id,
                    "scene_id": item.radar_data_ref_id,
                    "scene_rank": item.scene_rank,
                    "file_path": item.file_path,
                    "imaging_date": item.imaging_date,
                    "satellite": item.satellite,
                    "imaging_mode": item.imaging_mode,
                    "polarization": item.polarization,
                    "selection_meta": item.selection_meta_json if isinstance(item.selection_meta_json, dict) else None,
                }
                for item in ordered_items
            ],
            "network_edges": [
                {
                    "edge_id": item.id,
                    "edge_rank": item.edge_rank,
                    "master_plan_item_ref_id": item.master_plan_item_ref_id,
                    "slave_plan_item_ref_id": item.slave_plan_item_ref_id,
                    "metric_cache_ref_id": item.metric_cache_ref_id,
                    "master_scene_ref_id": item.master_scene_ref_id,
                    "slave_scene_ref_id": item.slave_scene_ref_id,
                    "master_imaging_date": item.master_imaging_date,
                    "slave_imaging_date": item.slave_imaging_date,
                    "temporal_baseline_days": item.temporal_baseline_days,
                    "spatial_baseline_meters": item.spatial_baseline_meters,
                    "perpendicular_baseline_meters": item.perpendicular_baseline_meters,
                    "scene_overlap_ratio": item.scene_overlap_ratio,
                    "pair_aoi_overlap_ratio": item.pair_aoi_overlap_ratio,
                    "selection_reason": item.selection_reason,
                    "selection_score": item.selection_score,
                    "enabled": bool(item.enabled),
                    "selection_meta": item.selection_meta_json if isinstance(item.selection_meta_json, dict) else None,
                }
                for item in ordered_edges
            ],
        }

    async def _load_stack_plan_context(
        self,
        db: AsyncSession,
        plan_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        normalized_plan_id = str(plan_id or "").strip()
        if not normalized_plan_id:
            return None
        plan_result = await db.execute(
            select(TimeseriesStackPlanORM).where(TimeseriesStackPlanORM.plan_id == normalized_plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            return {
                "source": "timeseries_stack_plan",
                "plan_id": normalized_plan_id,
                "status": "missing",
            }
        items_result = await db.execute(
            select(TimeseriesStackPlanItemORM)
            .where(TimeseriesStackPlanItemORM.plan_ref_id == plan.id)
            .order_by(TimeseriesStackPlanItemORM.scene_rank.asc(), TimeseriesStackPlanItemORM.id.asc())
        )
        edges_result = await db.execute(
            select(TimeseriesStackPlanEdgeORM)
            .where(TimeseriesStackPlanEdgeORM.plan_ref_id == plan.id)
            .order_by(TimeseriesStackPlanEdgeORM.edge_rank.asc(), TimeseriesStackPlanEdgeORM.id.asc())
        )
        return self._build_plan_context(
            plan,
            items_result.scalars().all(),
            edges_result.scalars().all(),
        )

    async def _load_run(self, run_id: str, db: AsyncSession) -> PsTimeseriesRunORM:
        result = await db.execute(
            select(PsTimeseriesRunORM).where(PsTimeseriesRunORM.run_id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise ValueError(f"ps_timeseries_run not found: {run_id}")
        return run

    async def _load_batch_items(self, batch_id: str, db: AsyncSession) -> List[PsTaskItemORM]:
        items_result = await db.execute(
            select(PsTaskItemORM)
            .where(PsTaskItemORM.batch_id == batch_id)
            .order_by(PsTaskItemORM.id.asc())
        )
        items = self._sorted_items(items_result.scalars().all())
        if len(items) < 3:
            raise ValueError("SBAS requires at least 3 scenes.")
        return items

    async def _resolve_stack_scene_records(
        self,
        *,
        items: List[PsTaskItemORM],
        batch_direction: Optional[str],
        run_id: str,
        work_root_windows: str,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        file_paths = [str(item.file_path or "").strip() for item in items if str(item.file_path or "").strip()]
        radar_by_key: Dict[str, RadarDataORM] = {}
        if file_paths:
            radar_result = await db.execute(
                select(RadarDataORM).where(RadarDataORM.file_path.in_(file_paths))
            )
            for radar in radar_result.scalars().all():
                radar_by_key[_normalize_lookup_key(radar.file_path)] = radar

        scene_payloads: List[Dict[str, Any]] = []
        tile_keys: List[str] = []
        source_dirs: List[str] = []

        for item in items:
            scene_dir = Path(_normalize_path(item.file_path))
            if not scene_dir.exists() or not scene_dir.is_dir():
                raise FileNotFoundError(f"PS scene directory not found: {scene_dir}")

            tiff_path = _choose_scene_tiff(scene_dir)
            meta_path = _scene_meta_from_tiff(tiff_path)
            radar = radar_by_key.get(_normalize_lookup_key(scene_dir))

            satellite = str(
                getattr(radar, "satellite", None)
                or item.satellite
                or scene_dir.name.split("_", 1)[0]
            ).strip()
            imaging_date = str(
                getattr(radar, "imaging_date", None)
                or item.imaging_date
                or ""
            ).strip()
            imaging_mode = str(getattr(radar, "imaging_mode", None) or "").strip() or None
            polarization = str(
                getattr(radar, "polarization", None)
                or item.polarization
                or ""
            ).strip() or None
            orbit_direction = str(
                getattr(radar, "orbit_direction", None)
                or batch_direction
                or ""
            ).strip().upper() or None
            tile_key = _derive_tile_key(scene_dir.name, radar)
            group_key = _build_group_key(
                satellite=satellite,
                imaging_mode=imaging_mode,
                polarization=polarization,
                orbit_direction=orbit_direction,
                tile_key=tile_key,
            )

            tile_keys.append(tile_key)
            source_dirs.append(str(scene_dir))
            scene_payloads.append(
                {
                    "task_item_id": item.id,
                    "plan_item_ref_id": item.plan_item_ref_id,
                    "folder_name": scene_dir.name,
                    "folder_path": str(scene_dir),
                    "folder_path_wsl": _windows_path_to_wsl_mount(str(scene_dir)),
                    "tiff_path": str(tiff_path),
                    "tiff_path_wsl": _windows_path_to_wsl_mount(str(tiff_path)),
                    "meta_path": str(meta_path),
                    "meta_path_wsl": _windows_path_to_wsl_mount(str(meta_path)),
                    "file_size_bytes": int(tiff_path.stat().st_size),
                    "satellite": satellite,
                    "imaging_date": imaging_date,
                    "imaging_mode": imaging_mode,
                    "polarization": polarization,
                    "orbit_direction": orbit_direction,
                    "satellite_mode": getattr(radar, "satellite_mode", None),
                    "receiving_station": getattr(radar, "receiving_station", None),
                    "orbit_circle": getattr(radar, "orbit_circle", None),
                    "scene_center_lon": getattr(radar, "scene_center_lon", None),
                    "scene_center_lat": getattr(radar, "scene_center_lat", None),
                    "acquisition_time_utc": getattr(radar, "acquisition_time_utc", None),
                    "product_type": getattr(radar, "product_type", None),
                    "product_level": getattr(radar, "product_level", None),
                    "product_unique_id": getattr(radar, "product_unique_id", None),
                    "tile_key": tile_key,
                    "group_key": group_key,
                    "orbit_txt_expected_name": f"{satellite}_GpsData_GAS_C_{imaging_date}.txt",
                }
            )

        scene_payloads.sort(key=lambda item: item.get("imaging_date") or "")
        first = scene_payloads[0]
        unique_tile_keys = sorted({key for key in tile_keys if key})
        manifest_tile_key = unique_tile_keys[0] if len(unique_tile_keys) == 1 else "MULTI_TILE"
        manifest_group_key = _build_group_key(
            satellite=first.get("satellite"),
            imaging_mode=first.get("imaging_mode"),
            polarization=first.get("polarization"),
            orbit_direction=first.get("orbit_direction"),
            tile_key=manifest_tile_key,
        )
        stack_key = _build_stack_key(manifest_group_key)
        slug = _build_stack_slug(
            satellite=first.get("satellite"),
            imaging_mode=first.get("imaging_mode"),
            polarization=first.get("polarization"),
            orbit_direction=first.get("orbit_direction"),
            tile_key=manifest_tile_key,
            run_id=run_id,
        )
        source_root = _common_source_root(source_dirs)

        return {
            "source_root_windows": source_root,
            "source_root_wsl": _windows_path_to_wsl_mount(source_root) if source_root else None,
            "group_key": manifest_group_key,
            "stack_key": stack_key,
            "tile_key": manifest_tile_key,
            "scene_count": len(scene_payloads),
            "reference_strategy": REFERENCE_STRATEGY_MIDDLE_BY_DATE,
            "stack_group": {
                "satellite": first.get("satellite"),
                "imaging_mode": first.get("imaging_mode"),
                "polarization": first.get("polarization"),
                "orbit_direction": first.get("orbit_direction"),
                "receiving_stations": sorted(
                    {
                        item.get("receiving_station")
                        for item in scene_payloads
                        if item.get("receiving_station")
                    }
                ),
            },
            "slug": slug,
            "proposed_scratch_windows": work_root_windows,
            "proposed_scratch_wsl": _windows_path_to_wsl_mount(work_root_windows),
            "proposed_layout": {
                "stack_input_manifest": f"{_windows_path_to_wsl_mount(work_root_windows)}/stack_input_manifest.json",
                "slc_dir": f"{_windows_path_to_wsl_mount(work_root_windows)}/SLC",
                "orbits_dir": f"{_windows_path_to_wsl_mount(work_root_windows)}/orbits",
                "logs_dir": f"{_windows_path_to_wsl_mount(work_root_windows)}/logs",
            },
            "stack_prep_assessment": {
                "current_scene_layout": "per_scene_folder_with_tiff_meta_rpc",
                "official_stripmapStack_expected_layout": "SLC/YYYYMMDD/YYYYMMDD.raw or YYYYMMDD.slc",
                "direct_compatibility": "bridged_by_system_prepare",
                "lt1_adapter_required_likely": True,
                "notes": [
                    "System prepare converts the PS batch into the experiment-compatible LT-1 stack selection manifest.",
                    "The next stack-prep step reuses the validated experiment adapter script.",
                    "The source of truth remains the original raw scene folders rather than any ENVI preprocessed derivative.",
                ],
            },
            "scenes": scene_payloads,
        }

    def _selected_manifest_path(self, run: PsTimeseriesRunORM) -> Path:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        return work_root / "input" / "selected_stack_manifest.json"

    def _selected_network_edges_path(self, run: PsTimeseriesRunORM) -> Path:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        return work_root / "input" / "selected_network_edges.json"

    def _generated_stack_manifest_path(self, run: PsTimeseriesRunORM) -> Path:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        return work_root / "stack_input_manifest.json"

    def _step_logs_dir(self, run: PsTimeseriesRunORM) -> Path:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        return work_root / "logs"

    def _prepare_workdirs(self, run: PsTimeseriesRunORM) -> None:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        for path in (
            work_root,
            work_root / "input",
            work_root / "logs",
            work_root / "notes",
            work_root / "inputs",
            work_root / "stack_work",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def _selected_manifest_payload(self, run: PsTimeseriesRunORM) -> Dict[str, Any]:
        manifest_path = self._selected_manifest_path(run)
        if not manifest_path.exists():
            raise FileNotFoundError(f"Selected stack manifest not found: {manifest_path}")
        return _read_json(manifest_path)

    def _dem_validation_status(
        self,
        dem_path: Optional[str],
        *,
        processor_code: Optional[str],
    ) -> Dict[str, Any]:
        normalized = _normalize_path(dem_path) if str(dem_path or "").strip() else None
        base_exists = bool(
            normalized
            and (Path(normalized).is_file() or Path(normalized).is_dir())
        )
        auxiliary_paths: List[str] = []
        if normalized and str(processor_code or "").strip() == "sarscape_sbas":
            base = Path(normalized)
            suffix = base.suffix.lower()
            if suffix == ".sml":
                auxiliary_paths = [str(base), str(base.with_suffix(".hdr"))]
            elif suffix == ".hdr":
                auxiliary_paths = [str(base.with_suffix(".sml")), str(base)]
            else:
                auxiliary_paths = [normalized + ".sml", normalized + ".hdr"]
        elif normalized:
            auxiliary_paths = _dem_sidecar_candidates(normalized)

        auxiliary = [
            {
                "path": path,
                "exists": bool(Path(path).is_file() or Path(path).is_dir()),
            }
            for path in auxiliary_paths
        ]
        auxiliary_ok = bool(auxiliary) and all(bool(item.get("exists")) for item in auxiliary)
        return {
            "path": normalized,
            "exists": base_exists,
            "auxiliary": auxiliary,
            "auxiliary_ok": auxiliary_ok,
            "ok": bool(base_exists or auxiliary_ok),
        }

    def _build_prepared_stack_validation(
        self,
        stack_manifest: Dict[str, Any],
        *,
        manifest_path: Optional[Path] = None,
        expected_run_id: Optional[str] = None,
        expected_processor_code: Optional[str] = None,
        require_network_edges: bool = False,
        require_dem: bool = False,
        dem_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        scenes = stack_manifest.get("scenes") if isinstance(stack_manifest.get("scenes"), list) else []
        network_edges = (
            stack_manifest.get("network_edges")
            if isinstance(stack_manifest.get("network_edges"), list)
            else []
        )
        artifacts = stack_manifest.get("artifacts") if isinstance(stack_manifest.get("artifacts"), dict) else {}
        artifact_edges_path = str(
            artifacts.get("selected_network_edges_path_windows")
            or stack_manifest.get("selected_network_edges_path_windows")
            or ""
        ).strip()
        artifact_edges_exists = bool(
            artifact_edges_path
            and Path(_normalize_path(artifact_edges_path)).is_file()
        )
        stack_dates = [
            normalized
            for normalized in (_normalize_date(item) for item in (stack_manifest.get("stack_dates") or []))
            if normalized
        ]
        scene_dates = [
            normalized
            for normalized in (_normalize_date(scene.get("imaging_date")) for scene in scenes if isinstance(scene, dict))
            if normalized
        ]
        duplicate_dates = sorted({date for date in scene_dates if scene_dates.count(date) > 1})

        missing_scene_paths: List[Dict[str, Any]] = []
        zero_size_files: List[Dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                missing_scene_paths.append(
                    {
                        "scene_index": index,
                        "role": "scene",
                        "path": None,
                        "reason": "scene payload is not an object",
                    }
                )
                continue
            for role, key, must_be_dir in (
                ("folder", "folder_path", True),
                ("tiff", "tiff_path", False),
                ("meta", "meta_path", False),
            ):
                raw_path = str(scene.get(key) or "").strip()
                if not raw_path:
                    missing_scene_paths.append(
                        {
                            "scene_index": index,
                            "imaging_date": scene.get("imaging_date"),
                            "role": role,
                            "path": None,
                            "reason": f"missing {key}",
                        }
                    )
                    continue
                path = Path(_normalize_path(raw_path))
                exists = path.is_dir() if must_be_dir else path.is_file()
                if not exists:
                    missing_scene_paths.append(
                        {
                            "scene_index": index,
                            "imaging_date": scene.get("imaging_date"),
                            "role": role,
                            "path": str(path),
                            "reason": "not found",
                        }
                    )
                elif not must_be_dir:
                    try:
                        if path.stat().st_size <= 0:
                            zero_size_files.append(
                                {
                                    "scene_index": index,
                                    "imaging_date": scene.get("imaging_date"),
                                    "role": role,
                                    "path": str(path),
                                }
                            )
                    except OSError:
                        zero_size_files.append(
                            {
                                "scene_index": index,
                                "imaging_date": scene.get("imaging_date"),
                                "role": role,
                                "path": str(path),
                            }
                        )

        scene_date_set = set(scene_dates)
        edge_date_issues: List[Dict[str, Any]] = []
        for index, edge in enumerate(network_edges):
            if not isinstance(edge, dict):
                continue
            master_date = _normalize_date(edge.get("master_imaging_date"))
            slave_date = _normalize_date(edge.get("slave_imaging_date"))
            missing_dates = [
                date
                for date in (master_date, slave_date)
                if date and date not in scene_date_set
            ]
            if missing_dates:
                edge_date_issues.append(
                    {
                        "edge_index": index,
                        "edge_id": edge.get("edge_id"),
                        "missing_dates": missing_dates,
                    }
                )

        processor_code = str(stack_manifest.get("processor_code") or "").strip()
        dem_status = self._dem_validation_status(
            dem_path or stack_manifest.get("dem_path_windows"),
            processor_code=expected_processor_code or processor_code,
        )

        blockers: List[str] = []
        warnings: List[str] = []
        if stack_manifest.get("prepared_stack_schema") != PREPARED_STACK_SCHEMA:
            blockers.append(
                f"Stack manifest is not a prepared SBAS stack ({PREPARED_STACK_SCHEMA})."
            )
        if stack_manifest.get("manifest_role") != PREPARED_STACK_MANIFEST_ROLE:
            blockers.append("Stack manifest role is not prepared_sbas_stack.")
        if expected_run_id and str(stack_manifest.get("run_id") or "").strip() != str(expected_run_id):
            blockers.append("Prepared stack run_id does not match the requested run.")
        if expected_processor_code and processor_code != str(expected_processor_code):
            blockers.append(
                f"Prepared stack processor_code mismatch: expected {expected_processor_code}, got {processor_code or '<empty>'}."
            )
        if len(scenes) < 3:
            blockers.append("Prepared SBAS stack requires at least 3 scenes.")
        if int(stack_manifest.get("scene_count") or 0) != len(scenes):
            blockers.append("Prepared stack scene_count does not match scenes length.")
        if len(scene_dates) != len(scenes):
            blockers.append("Prepared stack scenes must all have valid YYYYMMDD imaging_date values.")
        if stack_dates and stack_dates != scene_dates:
            blockers.append("Prepared stack stack_dates do not match the resolved scene dates.")
        if duplicate_dates:
            blockers.append("Prepared stack has duplicate scene dates: " + ", ".join(duplicate_dates))
        if missing_scene_paths:
            blockers.append(f"Prepared stack has missing scene inputs: {len(missing_scene_paths)}.")
        if zero_size_files:
            blockers.append(f"Prepared stack has zero-size scene files: {len(zero_size_files)}.")
        if int(stack_manifest.get("network_edge_count") or 0) != len(network_edges):
            blockers.append("Prepared stack network_edge_count does not match network_edges length.")
        if require_network_edges and not network_edges:
            blockers.append("Prepared SARscape SBAS stack requires selected network_edges.")
        if require_network_edges and not artifact_edges_exists:
            blockers.append("Prepared selected_network_edges.json artifact is missing.")
        if edge_date_issues:
            blockers.append(
                "Prepared stack network_edges reference dates outside the selected scene stack."
            )
        if require_dem and not dem_status.get("ok"):
            blockers.append("Prepared stack DEM dependency is missing.")
        if (not require_network_edges) and not network_edges:
            warnings.append("Prepared stack has no network_edges; graph audit is unavailable.")

        return {
            "schema": "insar.prepared-sbas-stack-validation/v1",
            "ok": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "manifest_path_windows": str(manifest_path) if manifest_path else None,
            "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
            "scene_count": len(scenes),
            "stack_dates": scene_dates,
            "network_edge_count": len(network_edges),
            "require_network_edges": require_network_edges,
            "require_dem": require_dem,
            "missing_scene_paths": missing_scene_paths,
            "zero_size_files": zero_size_files,
            "duplicate_stack_dates": duplicate_dates,
            "edge_date_issue_count": len(edge_date_issues),
            "edge_date_issues": edge_date_issues[:20],
            "artifact_status": {
                "selected_network_edges_path_windows": artifact_edges_path or None,
                "selected_network_edges_exists": artifact_edges_exists,
            },
            "dem_status": dem_status,
            "input_policy": {
                "production_input": "prepared_stack_manifest",
                "catalog_scan_allowed_after_prepare": False,
                "scene_selection_frozen": True,
            },
        }

    def _require_prepared_stack_manifest(
        self,
        stack_manifest: Dict[str, Any],
        *,
        manifest_path: Path,
        run: PsTimeseriesRunORM,
        require_network_edges: bool = True,
        require_dem: bool = True,
    ) -> Dict[str, Any]:
        validation = self._build_prepared_stack_validation(
            stack_manifest,
            manifest_path=manifest_path,
            expected_run_id=run.run_id,
            expected_processor_code=str(run.processor_code or "").strip() or None,
            require_network_edges=require_network_edges,
            require_dem=require_dem,
            dem_path=run.dem_path_windows,
        )
        if not validation.get("ok"):
            blockers = "; ".join(str(item) for item in (validation.get("blockers") or [])[:8])
            raise ValueError(
                "Prepared SBAS stack validation failed: "
                + (blockers or "unknown validation blocker")
            )
        return validation

    def _generated_stack_manifest_payload(self, run: PsTimeseriesRunORM) -> Dict[str, Any]:
        manifest_path = self._generated_stack_manifest_path(run)
        if not manifest_path.exists():
            raise FileNotFoundError(f"Generated stack_input_manifest.json not found: {manifest_path}")
        return _read_json(manifest_path)

    def _stack_work_dir(self, run: PsTimeseriesRunORM, report: Optional[Dict[str, Any]] = None) -> Path:
        workspace = (report or {}).get("workspace") or {}
        path = str(workspace.get("stack_work_dir_windows") or "").strip()
        if path:
            return Path(_normalize_path(path))
        return Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id))) / "stack_work"

    def _stack_work_dir_wsl(self, run: PsTimeseriesRunORM, report: Optional[Dict[str, Any]] = None) -> str:
        workspace = (report or {}).get("workspace") or {}
        path = str(workspace.get("stack_work_dir_wsl") or "").strip()
        if path:
            return path
        normalized = _windows_path_to_wsl_mount(str(self._stack_work_dir(run, report)))
        if not normalized:
            raise ValueError("Unable to resolve stack_work path to WSL.")
        return normalized

    def _mintpy_work_dir(self, run: PsTimeseriesRunORM, report: Optional[Dict[str, Any]] = None) -> Path:
        return self._stack_work_dir(run, report) / f"mintpy_sbas_{run.run_id[:8]}"

    def _mintpy_cfg_path(self, run: PsTimeseriesRunORM) -> Path:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        return work_root / "input" / "smallbaselineApp.cfg"

    def _publish_manifest_path(self, run: PsTimeseriesRunORM) -> Path:
        publish_dir = Path(
            run.publish_dir_windows
            or _compose_publish_dir(run.run_id, run.stack_key)
        )
        return publish_dir / "manifest.json"

    def _sarscape_processor_manifest_path(self, run: PsTimeseriesRunORM) -> Path:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        return work_root / "input" / "sarscape_sbas_processor_manifest.json"

    def _sarscape_execution_report_path(self, run: PsTimeseriesRunORM) -> Path:
        work_root = Path(run.work_root_windows or _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run.run_id)))
        return work_root / "sarscape_sbas" / "sarscape_sbas_execution_report.json"

    def _effective_env_name(self, run: PsTimeseriesRunORM) -> str:
        env_name = str(run.env_name or settings.TIMESERIES_ENV_NAME or "").strip()
        if not env_name:
            raise ValueError("TIMESERIES_ENV_NAME is not configured.")
        return env_name

    def _effective_python_wsl(self, run: PsTimeseriesRunORM) -> str:
        python_wsl = str(settings.TIMESERIES_PYTHON or "").strip()
        if python_wsl:
            return python_wsl
        env_name = self._effective_env_name(run)
        return f"/home/administrator/miniconda3/envs/{env_name}/bin/python"

    def _effective_wsl_distro(self, run: PsTimeseriesRunORM) -> str:
        distro = str(run.wsl_distro or settings.TIMESERIES_WSL_DISTRO or "").strip()
        if not distro:
            raise ValueError("TIMESERIES_WSL_DISTRO is not configured.")
        return distro

    def _runtime_stack_share_wsl(self, python_wsl: str) -> str:
        text = str(python_wsl or "").strip()
        if not text:
            raise ValueError("TIMESERIES_PYTHON is not configured.")
        python_path = PurePosixPath(text)
        env_root = python_path.parent.parent
        return str(env_root / "share" / "isce2")

    def _retry_status_for_step(self, step_id: str) -> str:
        return TIMESERIES_STEP_STATUS_HINTS.get(str(step_id or "").strip(), STATUS_PENDING)

    def _retry_progress_for_step(self, step_id: str) -> int:
        return int(TIMESERIES_STEP_PROGRESS_HINTS.get(str(step_id or "").strip(), 1))

    async def get_preflight_report(
        self,
        *,
        batch_id: str,
        reference_date: Optional[str] = None,
        water_mask_mode: str = "synthetic_fallback",
        db: AsyncSession,
    ) -> Dict[str, Any]:
        checks: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []

        def add_check(
            name: str,
            ok: bool,
            detail: str,
            *,
            severity: str = "error",
            skipped: bool = False,
        ) -> None:
            checks.append(
                {
                    "name": name,
                    "ok": bool(ok),
                    "detail": detail,
                    "severity": severity,
                    "skipped": bool(skipped),
                }
            )
            if skipped or ok:
                return
            if severity == "warn":
                warnings.append(f"{name}: {detail}")
            else:
                errors.append(f"{name}: {detail}")

        normalized_batch_id = str(batch_id or "").strip()
        normalized_reference_date = _normalize_date(reference_date)
        normalized_water_mask_mode = (
            str(water_mask_mode or "synthetic_fallback").strip() or "synthetic_fallback"
        )

        add_check(
            "TIMESERIES_ENABLED",
            bool(settings.TIMESERIES_ENABLED),
            "enabled" if settings.TIMESERIES_ENABLED else "TIMESERIES_ENABLED is false.",
        )
        if not normalized_batch_id:
            add_check("batch_id", False, "batch_id is required.")
            return {
                "overall_ok": False,
                "batch_id": normalized_batch_id,
                "checks": checks,
                "errors": errors,
                "warnings": warnings,
            }

        batch_result = await db.execute(
            select(PsTaskBatchORM).where(PsTaskBatchORM.batch_id == normalized_batch_id)
        )
        batch = batch_result.scalar_one_or_none()
        add_check(
            "batch_exists",
            batch is not None,
            f"batch_id={normalized_batch_id}" if batch is not None else "PS batch not found.",
        )
        if batch is None:
            return {
                "overall_ok": False,
                "batch_id": normalized_batch_id,
                "checks": checks,
                "errors": errors,
                "warnings": warnings,
            }

        items_result = await db.execute(
            select(PsTaskItemORM)
            .where(PsTaskItemORM.batch_id == normalized_batch_id)
            .order_by(PsTaskItemORM.id.asc())
        )
        items = self._sorted_items(items_result.scalars().all())
        add_check(
            "scene_count",
            len(items) >= 3,
            f"{len(items)} scenes found; SBAS requires at least 3 scenes.",
        )

        valid_dates: List[str] = []
        invalid_date_items: List[str] = []
        scene_dir_errors = 0
        tiff_errors = 0
        meta_errors = 0
        total_bytes = 0

        for item in items:
            normalized_date = _normalize_date(item.imaging_date)
            if normalized_date:
                valid_dates.append(normalized_date)
            else:
                invalid_date_items.append(str(item.file_path or item.id))

            scene_dir = Path(_normalize_path(item.file_path))
            if not scene_dir.exists() or not scene_dir.is_dir():
                scene_dir_errors += 1
                continue

            try:
                tiff_path = _choose_scene_tiff(scene_dir)
                total_bytes += int(tiff_path.stat().st_size)
            except Exception:
                tiff_errors += 1
                continue

            try:
                _scene_meta_from_tiff(tiff_path)
            except Exception:
                meta_errors += 1

        add_check(
            "scene_dates",
            len(invalid_date_items) == 0 and len(valid_dates) == len(items),
            (
                "all scene dates are valid."
                if len(invalid_date_items) == 0 and len(valid_dates) == len(items)
                else f"invalid scene dates: {len(invalid_date_items)}"
            ),
        )
        duplicate_dates = sorted({item for item in valid_dates if valid_dates.count(item) > 1})
        add_check(
            "unique_dates",
            len(duplicate_dates) == 0,
            "all scene dates are unique." if len(duplicate_dates) == 0 else f"duplicate dates: {', '.join(duplicate_dates)}",
        )
        add_check(
            "scene_directories",
            scene_dir_errors == 0,
            "all scene directories are present." if scene_dir_errors == 0 else f"missing scene directories: {scene_dir_errors}",
        )
        add_check(
            "scene_tiff_files",
            tiff_errors == 0,
            "all scene tiff files are present." if tiff_errors == 0 else f"scene tiff errors: {tiff_errors}",
        )
        add_check(
            "scene_meta_files",
            meta_errors == 0,
            "all scene meta xml files are present." if meta_errors == 0 else f"scene meta xml errors: {meta_errors}",
        )

        if normalized_reference_date and normalized_reference_date not in valid_dates:
            add_check(
                "reference_date",
                False,
                f"requested reference date {normalized_reference_date} is not in the stack; system will fall back to the middle date.",
                severity="warn",
            )
        else:
            add_check(
                "reference_date",
                True,
                normalized_reference_date or "no explicit reference date requested; middle date will be used.",
            )

        effective_reference_date = self._choose_reference_date(valid_dates, normalized_reference_date)
        add_check(
            "effective_reference_date",
            bool(effective_reference_date),
            effective_reference_date or "unable to determine a valid reference date.",
        )

        synthetic_allowed = bool(settings.TIMESERIES_ALLOW_SYNTHETIC_WATER_MASK)
        water_mask_ok = not (
            normalized_water_mask_mode == "synthetic_fallback" and not synthetic_allowed
        )
        add_check(
            "water_mask_mode",
            water_mask_ok,
            (
                f"{normalized_water_mask_mode} accepted."
                if water_mask_ok
                else "synthetic_fallback is disabled by configuration."
            ),
        )

        dem_path = str(settings.TIMESERIES_DEM_PATH or "").strip()
        add_check(
            "dem_path",
            bool(dem_path) and os.path.isfile(_normalize_path(dem_path)),
            dem_path or "TIMESERIES_DEM_PATH is not configured.",
        )
        orbit_pool = str(settings.TIMESERIES_ORBIT_POOL_ISCE2 or "").strip()
        add_check(
            "orbit_pool",
            bool(orbit_pool) and os.path.isdir(_normalize_path(orbit_pool)),
            orbit_pool or "TIMESERIES_ORBIT_POOL_ISCE2 is not configured.",
        )

        stack_preview: Optional[Dict[str, Any]] = None
        if not errors:
            try:
                stack_preview = await self._resolve_stack_scene_records(
                    items=items,
                    batch_direction=batch.direction,
                    run_id="preflight",
                    work_root_windows=_normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, "_preflight")),
                    db=db,
                )
                add_check(
                    "stack_identity",
                    True,
                    (
                        f"stack_key={stack_preview.get('stack_key')} "
                        f"group_key={stack_preview.get('group_key')}"
                    ),
                )
            except Exception as exc:
                add_check("stack_identity", False, str(exc))

        batch_status = str(batch.status or "").strip()
        if batch_status and batch_status.upper() != "COMPLETED":
            add_check(
                "batch_status",
                False,
                f"batch status is {batch_status}; production can still run but the batch is not marked COMPLETED.",
                severity="warn",
            )
        else:
            add_check("batch_status", True, batch_status or "COMPLETED")

        return {
            "overall_ok": len(errors) == 0,
            "batch_id": normalized_batch_id,
            "batch_name": batch.name,
            "batch_status": batch.status,
            "plan_id": batch.plan_id,
            "plan_strategy": batch.plan_strategy,
            "reference_date_requested": normalized_reference_date,
            "reference_date_effective": effective_reference_date,
            "water_mask_mode": normalized_water_mask_mode,
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "scene_count": len(items),
                "valid_date_count": len(valid_dates),
                "invalid_date_count": len(invalid_date_items),
                "duplicate_date_count": len(duplicate_dates),
                "item_has_orbit_data_count": sum(1 for item in items if item.has_orbit_data),
                "total_scene_bytes": total_bytes,
                "stack_dates": sorted(valid_dates),
                "group_key": (stack_preview or {}).get("group_key"),
                "stack_key": (stack_preview or {}).get("stack_key"),
                "tile_key": (stack_preview or {}).get("tile_key"),
                "source_root_windows": (stack_preview or {}).get("source_root_windows"),
                "plan_id": batch.plan_id,
                "plan_strategy": batch.plan_strategy,
            },
        }

    async def get_runtime_report(
        self,
        *,
        distro: Optional[str] = None,
        smoke_test: bool = False,
    ) -> Dict[str, Any]:
        effective_distro = str(distro or settings.TIMESERIES_WSL_DISTRO or "").strip()
        if not effective_distro:
            raise ValueError("TIMESERIES_WSL_DISTRO is not configured.")

        env_name = str(settings.TIMESERIES_ENV_NAME or "").strip()
        python_wsl = str(settings.TIMESERIES_PYTHON or "").strip()
        if not python_wsl and env_name:
            python_wsl = f"/home/administrator/miniconda3/envs/{env_name}/bin/python"
        if not python_wsl:
            raise ValueError("TIMESERIES_PYTHON is not configured.")

        stack_share_wsl = self._runtime_stack_share_wsl(python_wsl)
        stack_script_wsl = str(PurePosixPath(stack_share_wsl) / "stripmapStack" / "stackStripMap.py")
        prepare_dem_script_wsl = _windows_path_to_wsl_mount(settings.TIMESERIES_PREPARE_DEM_SCRIPT) or ""
        report = await asyncio.to_thread(
            check_wsl_environment,
            distro=effective_distro,
            python_cmd=python_wsl,
            stripmap_app_path=stack_script_wsl,
            pipeline_script_path=prepare_dem_script_wsl,
            dem_path_win=settings.TIMESERIES_DEM_PATH,
            orbit_dir_win=settings.TIMESERIES_ORBIT_POOL_ISCE2,
            output_dir_win=settings.TIMESERIES_PRODUCT_DIR,
            smoke_test=smoke_test,
        )
        payload = report.to_dict()
        checks = list(payload.get("checks") or [])

        script_checks = [
            ("TIMESERIES_STACK_PREP_SCRIPT", settings.TIMESERIES_STACK_PREP_SCRIPT),
            ("TIMESERIES_MATERIALIZE_SCRIPT", settings.TIMESERIES_MATERIALIZE_SCRIPT),
            ("TIMESERIES_PREPARE_DEM_SCRIPT", settings.TIMESERIES_PREPARE_DEM_SCRIPT),
            ("TIMESERIES_STACK_RUNNER_SCRIPT", settings.TIMESERIES_STACK_RUNNER_SCRIPT),
            ("TIMESERIES_MINTPY_SBAS_SCRIPT", settings.TIMESERIES_MINTPY_SBAS_SCRIPT),
            ("TIMESERIES_EXPORT_PUBLISH_SCRIPT", settings.TIMESERIES_EXPORT_PUBLISH_SCRIPT),
        ]
        extra_ok = True
        for label, script_path in script_checks:
            normalized = _normalize_path(script_path) if str(script_path or "").strip() else ""
            exists_flag = bool(normalized) and os.path.isfile(normalized)
            detail = normalized or "not configured"
            checks.append(
                {
                    "name": label,
                    "ok": exists_flag,
                    "detail": detail,
                    "skipped": not bool(normalized),
                }
            )
            if normalized and not exists_flag:
                extra_ok = False

        local_dir_checks = [
            ("TIMESERIES_WORK_ROOT", str(settings.TIMESERIES_WORK_ROOT or "").strip()),
            ("TIMESERIES_PRODUCT_DIR", str(settings.TIMESERIES_PRODUCT_DIR or "").strip()),
        ]
        for label, dir_path in local_dir_checks:
            normalized = _normalize_path(dir_path) if dir_path else ""
            exists_flag = bool(normalized) and os.path.isdir(normalized)
            checks.append(
                {
                    "name": f"{label} exists",
                    "ok": exists_flag,
                    "detail": normalized or "not configured",
                    "skipped": not bool(normalized),
                }
            )
            if normalized and not exists_flag:
                extra_ok = False
                continue
            if normalized:
                writable_ok, writable_detail = _probe_local_writable_dir(normalized)
                checks.append(
                    {
                        "name": f"{label} writable",
                        "ok": writable_ok,
                        "detail": writable_detail,
                        "skipped": False,
                    }
                )
                if not writable_ok:
                    extra_ok = False

        work_root_wsl = _windows_path_to_wsl_mount(str(settings.TIMESERIES_WORK_ROOT or "").strip()) or ""
        if work_root_wsl:
            rc, _, stderr = await asyncio.to_thread(
                run_wsl_command,
                f"mkdir -p {shlex.quote(work_root_wsl)} && test -w {shlex.quote(work_root_wsl)}",
                effective_distro,
                20,
                None,
            )
            work_root_wsl_ok = rc == 0
            checks.append(
                {
                    "name": "TIMESERIES_WORK_ROOT writable in WSL",
                    "ok": work_root_wsl_ok,
                    "detail": work_root_wsl if work_root_wsl_ok else (stderr or work_root_wsl),
                    "skipped": False,
                }
            )
            if not work_root_wsl_ok:
                extra_ok = False

        dem_path_windows = str(settings.TIMESERIES_DEM_PATH or "").strip()
        if dem_path_windows:
            sidecar_candidates = _dem_sidecar_candidates(dem_path_windows)
            missing_sidecars = [path for path in sidecar_candidates if not os.path.exists(path)]
            checks.append(
                {
                    "name": "DEM sidecars",
                    "ok": len(missing_sidecars) == 0,
                    "detail": (
                        "all expected local DEM sidecars exist."
                        if len(missing_sidecars) == 0
                        else "missing: " + ", ".join(missing_sidecars[:5])
                    ),
                    "skipped": False,
                }
            )
            if missing_sidecars:
                extra_ok = False

        mintpy_command = f"{shlex.quote(python_wsl)} -c \"import mintpy; print('mintpy_ok')\""
        rc, stdout, stderr = await asyncio.to_thread(
            run_wsl_command,
            mintpy_command,
            effective_distro,
            30,
            None,
        )
        mintpy_ok = rc == 0 and "mintpy_ok" in stdout
        checks.append(
            {
                "name": "mintpy import",
                "ok": mintpy_ok,
                "detail": stdout or stderr,
                "skipped": False,
            }
        )
        if not mintpy_ok:
            extra_ok = False

        payload["checks"] = checks
        payload["env_name"] = env_name
        payload["python_wsl"] = python_wsl
        payload["stack_script_wsl"] = stack_script_wsl
        payload["overall_ok"] = bool(payload.get("overall_ok")) and extra_ok
        if payload["overall_ok"]:
            payload["message"] = payload.get("message") or "Timeseries WSL runtime is ready."
        else:
            failed = [item.get("name") for item in checks if not item.get("ok") and not item.get("skipped")]
            if failed:
                payload["message"] = "Timeseries WSL runtime has issues: " + ", ".join(failed)
        return payload

    async def get_sarscape_sbas_preflight_report(
        self,
        *,
        batch_id: str,
        reference_date: Optional[str] = None,
        include_task_discovery: bool = True,
        discovery_timeout_seconds: int = 120,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Build the SARscape SBAS stack/processor contract without executing SARscape."""
        normalized_batch_id = str(batch_id or "").strip()
        if not normalized_batch_id:
            raise ValueError("batch_id is required.")

        batch_result = await db.execute(
            select(PsTaskBatchORM).where(PsTaskBatchORM.batch_id == normalized_batch_id)
        )
        batch = batch_result.scalar_one_or_none()
        if batch is None:
            raise ValueError(f"PS batch not found: {normalized_batch_id}")

        items = await self._load_batch_items(normalized_batch_id, db)
        remark_planning_context = self._extract_planning_context(items)
        stack_plan_context = await self._load_stack_plan_context(db, batch.plan_id)
        planning_context = remark_planning_context
        if stack_plan_context:
            planning_context = {
                **stack_plan_context,
                **(remark_planning_context or {}),
            }
            planning_context["source"] = stack_plan_context.get("source")
            planning_context["plan_id"] = stack_plan_context.get("plan_id")
            planning_context["strategy"] = (
                stack_plan_context.get("strategy")
                or planning_context.get("strategy")
            )
            planning_context["scene_count"] = (
                stack_plan_context.get("scene_count")
                or planning_context.get("scene_count")
            )
            planning_context["stack_key"] = (
                stack_plan_context.get("stack_key")
                or planning_context.get("stack_key")
            )
            planning_context["group_key"] = (
                stack_plan_context.get("group_key")
                or planning_context.get("group_key")
            )
            if not planning_context.get("scenes"):
                planning_context["scenes"] = stack_plan_context.get("scenes") or []
            if not planning_context.get("network_edges"):
                planning_context["network_edges"] = stack_plan_context.get("network_edges") or []

        stack_dates = [
            normalized
            for normalized in (_normalize_date(item.imaging_date) for item in items)
            if normalized
        ]
        if len(stack_dates) != len(items):
            raise ValueError("Every PS item must have a valid YYYYMMDD imaging_date.")

        effective_reference_date = self._choose_reference_date(stack_dates, reference_date)
        if not effective_reference_date:
            raise ValueError("Unable to determine a reference date for this PS batch.")

        preview_id = f"sarscape_sbas_preflight_{normalized_batch_id[:8]}"
        preview_work_root = _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, "_sarscape_sbas_preflight"))
        stack_preview = await self._resolve_stack_scene_records(
            items=items,
            batch_direction=batch.direction,
            run_id=preview_id,
            work_root_windows=preview_work_root,
            db=db,
        )
        network_edges = (
            planning_context.get("network_edges")
            if isinstance((planning_context or {}).get("network_edges"), list)
            else []
        )
        selection_params = {
            key: planning_context.get(key)
            for key in (
                "initial_overlap_threshold",
                "final_overlap_threshold",
                "time_baseline_min",
                "time_baseline_max",
                "spatial_baseline_max_meters",
                "network_overlap_threshold",
                "num_connections",
            )
            if (planning_context or {}).get(key) is not None
        }

        stack_manifest = {
            **stack_preview,
            "schema": "insar.timeseries-stack/v1",
            "preview_id": preview_id,
            "batch_id": normalized_batch_id,
            "plan_id": batch.plan_id or ((planning_context or {}).get("plan_id")),
            "plan_strategy": batch.plan_strategy or ((planning_context or {}).get("strategy")),
            "catalog_name": CATALOG_NAME_PSINSAR,
            "mode": "sbas",
            "engine_code": "sarscape",
            "processor_code": "sarscape_sbas",
            "reference_date": effective_reference_date,
            "stack_dates": stack_dates,
            "selection_params": selection_params,
            "network_edges": network_edges,
            "network_edge_count": len(network_edges),
            "planning_context_summary": {
                "source": (planning_context or {}).get("source"),
                "plan_id": (planning_context or {}).get("plan_id"),
                "strategy": (planning_context or {}).get("strategy"),
                "scene_count": (planning_context or {}).get("scene_count"),
                "stack_key": (planning_context or {}).get("stack_key"),
                "group_key": (planning_context or {}).get("group_key"),
                "network_edge_count": (planning_context or {}).get("network_edge_count", len(network_edges)),
            } if planning_context else None,
            "processing_workflow": "sarscape_sbas",
        }

        preflight = await asyncio.to_thread(
            build_sarscape_sbas_preflight_report,
            stack_manifest,
            include_task_discovery=include_task_discovery,
            discovery_timeout_seconds=max(10, int(discovery_timeout_seconds or 120)),
        )
        processor_manifest = build_sarscape_sbas_processor_manifest(
            stack_manifest,
            discovery_report=preflight.get("task_discovery") if isinstance(preflight, dict) else None,
        )

        return {
            "schema": "insar.sarscape-sbas-preview/v1",
            "batch_id": normalized_batch_id,
            "batch_name": batch.name,
            "plan_id": stack_manifest.get("plan_id"),
            "plan_strategy": stack_manifest.get("plan_strategy"),
            "reference_date": effective_reference_date,
            "scene_count": len(stack_dates),
            "network_edge_count": len(network_edges),
            "ready_for_pipeline_design": bool(preflight.get("ready_for_pipeline_design")),
            "ready_for_execution": bool(preflight.get("ready_for_execution")),
            "blockers": preflight.get("blockers") or [],
            "environment": preflight.get("environment"),
            "task_discovery": preflight.get("task_discovery"),
            "stack_manifest": stack_manifest,
            "processor_manifest": processor_manifest,
        }

    async def _run_wsl_step(
        self,
        run: PsTimeseriesRunORM,
        *,
        step_name: str,
        command: str,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        distro = self._effective_wsl_distro(run)
        timeout = max(60, int(timeout_seconds or settings.TIMESERIES_WSL_STEP_TIMEOUT_SECONDS))
        rc, stdout, stderr = await asyncio.to_thread(
            run_wsl_command,
            command,
            distro,
            timeout,
            None,
        )
        logs_dir = self._step_logs_dir(run)
        _write_step_logs(logs_dir, step_name, stdout, stderr)
        stdout_log = logs_dir / f"{step_name}.stdout.log"
        stderr_log = logs_dir / f"{step_name}.stderr.log"
        if rc != 0:
            raise RuntimeError(f"{step_name} failed (exit={rc}): {_tail_text(stderr or stdout)}")
        return {
            "step_name": step_name,
            "command": command,
            "stdout_log_windows": str(stdout_log),
            "stderr_log_windows": str(stderr_log),
            "stdout_log_wsl": _windows_path_to_wsl_mount(str(stdout_log)),
            "stderr_log_wsl": _windows_path_to_wsl_mount(str(stderr_log)),
            "stdout_tail": _tail_text(stdout, limit=1200),
            "stderr_tail": _tail_text(stderr, limit=1200),
        }

    def _write_mintpy_config(
        self,
        run: PsTimeseriesRunORM,
        *,
        report: Dict[str, Any],
    ) -> Path:
        reference_date = str(report.get("reference_date") or run.reference_date or "").strip()
        if not reference_date:
            raise ValueError("Reference date is missing for MintPy configuration.")

        stack_work_dir_wsl = self._stack_work_dir_wsl(run, report)
        cfg_path = self._mintpy_cfg_path(run)
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Auto-generated by backend/app/services/timeseries_service.py",
            "mintpy.compute.cluster   = none",
            "mintpy.compute.numWorker = 4",
            "mintpy.compute.maxMemory = 8.0",
            "",
            "mintpy.load.processor      = isce",
            "mintpy.load.autoPath       = no",
            "mintpy.load.updateMode     = yes",
            "mintpy.load.compression    = lzf",
            f"mintpy.load.metaFile       = {stack_work_dir_wsl}/merged/SLC/{reference_date}/referenceShelve/data.dat",
            f"mintpy.load.baselineDir    = {stack_work_dir_wsl}/baselines",
            f"mintpy.load.unwFile        = {stack_work_dir_wsl}/Igrams/*/filt*_snaphu.unw",
            f"mintpy.load.corFile        = {stack_work_dir_wsl}/Igrams/*/filt_*.cor",
            f"mintpy.load.connCompFile   = {stack_work_dir_wsl}/Igrams/*/filt*_snaphu.unw.conncomp",
            "mintpy.load.intFile        = None",
            f"mintpy.load.demFile        = {stack_work_dir_wsl}/geom_reference/hgt.rdr",
            f"mintpy.load.lookupYFile    = {stack_work_dir_wsl}/geom_reference/lat.rdr",
            f"mintpy.load.lookupXFile    = {stack_work_dir_wsl}/geom_reference/lon.rdr",
            f"mintpy.load.incAngleFile   = {stack_work_dir_wsl}/geom_reference/los.rdr",
            f"mintpy.load.azAngleFile    = {stack_work_dir_wsl}/geom_reference/los.rdr",
            f"mintpy.load.shadowMaskFile = {stack_work_dir_wsl}/geom_reference/shadowMask.rdr",
            f"mintpy.load.waterMaskFile  = {stack_work_dir_wsl}/geom_reference/waterMask.rdr",
            "",
            "mintpy.network.coherenceBased = no",
            "mintpy.network.areaRatioBased = no",
            "",
            "mintpy.reference.maskFile = maskAllValid.h5",
            "",
            "mintpy.unwrapError.method = no",
            "",
            "mintpy.networkInversion.weightFunc    = no",
            "mintpy.networkInversion.maskDataset   = no",
            "mintpy.networkInversion.minRedundancy = 1.0",
            "mintpy.networkInversion.waterMaskFile = maskAllValid.h5",
            "",
            "mintpy.solidEarthTides          = no",
            "mintpy.ionosphericDelay.method  = no",
            "mintpy.troposphericDelay.method = no",
            "mintpy.deramp                   = no",
            "mintpy.topographicResidual      = no",
            "",
            f"mintpy.reference.date = {reference_date}",
            "mintpy.geocode        = no",
            "mintpy.save.kmz       = no",
            "mintpy.save.hdfEos5   = no",
            "mintpy.plot           = no",
            "",
        ]
        cfg_path.write_text("\n".join(lines), encoding="utf-8")
        return cfg_path

    def _augment_publish_manifest(
        self,
        run: PsTimeseriesRunORM,
        *,
        manifest_path: Path,
        report: Dict[str, Any],
        mintpy_cfg_path: Path,
        mintpy_work_dir: Path,
    ) -> Dict[str, Any]:
        if not manifest_path.exists():
            raise FileNotFoundError(f"Publish manifest not found: {manifest_path}")

        payload = _read_json(manifest_path)
        now_text = _utcnow().replace(microsecond=0).isoformat() + "Z"
        source_summary = {
            "plan_id": run.plan_id,
            "plan_strategy": run.plan_strategy,
            "selected_manifest_path_windows": str(self._selected_manifest_path(run)),
            "selected_manifest_path_wsl": _windows_path_to_wsl_mount(str(self._selected_manifest_path(run))),
            "generated_stack_manifest_path_windows": str(self._generated_stack_manifest_path(run)),
            "generated_stack_manifest_path_wsl": _windows_path_to_wsl_mount(str(self._generated_stack_manifest_path(run))),
            "mintpy_config_path_windows": str(mintpy_cfg_path),
            "mintpy_config_path_wsl": _windows_path_to_wsl_mount(str(mintpy_cfg_path)),
            "mintpy_work_dir_windows": str(mintpy_work_dir),
            "mintpy_work_dir_wsl": _windows_path_to_wsl_mount(str(mintpy_work_dir)),
            "publish_dir_windows": str(run.publish_dir_windows or ""),
            "publish_dir_wsl": _windows_path_to_wsl_mount(str(run.publish_dir_windows or "")),
            "planning_context": (
                (run.params_json or {}).get("planning_context")
                if isinstance(run.params_json, dict)
                else None
            ),
        }
        payload = upgrade_timeseries_package_manifest(
            payload,
            run_context={
                "run_id": run.run_id,
                "run_name": run.run_name,
                "batch_id": run.batch_id,
                "plan_id": run.plan_id,
                "plan_strategy": run.plan_strategy,
                "task_id": run.task_id,
                "workflow_run_id": run.workflow_run_id,
                "mode": run.mode,
                "engine_code": run.engine_code,
                "processor_code": run.processor_code,
                "runtime_id": run.runtime_id,
                "stack_key": run.stack_key or payload.get("stack_key") or report.get("stack_key"),
                "group_key": payload.get("group_key") or report.get("group_key"),
                "reference_date": payload.get("reference_date") or run.reference_date,
                "stack_dates": (
                    payload.get("stack_dates")
                    or (run.summary_json or {}).get("stack_dates")
                    or [
                        normalized
                        for normalized in (
                            _normalize_date(scene.get("date"))
                            for scene in (report.get("scenes") or [])
                        )
                        if normalized
                    ]
                ),
                "published_at": payload.get("published_at") or now_text,
                "produced_at": payload.get("produced_at") or now_text,
                "publish_dir": run.publish_dir_windows,
                "native_output_dir": str(mintpy_work_dir),
                "runtime": {
                    "runtime_id": run.runtime_id,
                    "env_name": self._effective_env_name(run),
                    "wsl_distro": self._effective_wsl_distro(run),
                    "water_mask_mode": run.water_mask_mode,
                },
            },
            source_summary=source_summary,
        )
        _write_json(manifest_path, payload)
        return payload

    def _validate_publish_bundle(self, manifest_path: Path) -> Dict[str, Any]:
        if not manifest_path.exists():
            raise FileNotFoundError(f"Publish manifest not found: {manifest_path}")

        normalized = normalize_package_manifest(_read_json(manifest_path))
        package_dir = manifest_path.parent
        assets = list(normalized.get("assets") or [])
        asset_by_role: Dict[str, List[Dict[str, Any]]] = {}
        for asset in assets:
            role = str(asset.get("role") or "").strip()
            if not role:
                continue
            asset_by_role.setdefault(role, []).append(asset)

        missing_roles: List[str] = []
        missing_files: List[str] = []
        zero_size_files: List[str] = []
        for role in REQUIRED_TIMESERIES_ASSET_ROLES:
            role_assets = asset_by_role.get(role) or []
            if not role_assets:
                missing_roles.append(role)
                continue
            relative_path = str(role_assets[0].get("relative_path") or "").strip()
            absolute_path = package_dir / relative_path
            if not absolute_path.is_file():
                missing_files.append(relative_path)
                continue
            try:
                if absolute_path.stat().st_size <= 0:
                    zero_size_files.append(relative_path)
            except OSError:
                zero_size_files.append(relative_path)

        missing_extra_files: List[str] = []
        for relative_path in REQUIRED_TIMESERIES_EXTRA_FILES:
            if not (package_dir / relative_path).is_file():
                missing_extra_files.append(relative_path)

        temporal = normalized.get("temporal") or {}
        stack_dates = [
            str(item).strip()
            for item in (temporal.get("stack_dates") or normalized.get("stack_dates") or [])
            if str(item).strip()
        ]
        reference_date = str(
            temporal.get("reference_date") or normalized.get("reference_date") or ""
        ).strip()
        canonical = normalized.get("canonical") or {}
        primary_role = str(canonical.get("primary_asset_role") or "").strip()
        preview_role = str(canonical.get("preview_asset_role") or "").strip()

        issues: List[str] = []
        if not reference_date:
            issues.append("reference_date_missing")
        if not stack_dates:
            issues.append("stack_dates_missing")
        if not primary_role:
            issues.append("primary_asset_role_missing")
        if not preview_role:
            issues.append("preview_asset_role_missing")

        ok = not (
            missing_roles
            or missing_files
            or zero_size_files
            or missing_extra_files
            or issues
        )
        return {
            "ok": ok,
            "schema_version": normalized.get("schema_version"),
            "catalog_name": normalized.get("catalog_name"),
            "product_family": normalized.get("product_family"),
            "stack_size": len(stack_dates),
            "reference_date": reference_date or None,
            "primary_asset_role": primary_role or None,
            "preview_asset_role": preview_role or None,
            "asset_roles": sorted(asset_by_role.keys()),
            "missing_roles": missing_roles,
            "missing_files": missing_files,
            "zero_size_files": zero_size_files,
            "missing_extra_files": missing_extra_files,
            "issues": issues,
        }

    def _workflow_steps(
        self,
        *,
        run_id: str,
        task_id: str,
        processor_code: str = "isce2_stack_mintpy",
        execution_mode: str = "full",
    ) -> List[Dict[str, Any]]:
        if processor_code == "sarscape_sbas":
            steps: List[Dict[str, Any]] = [
                {
                    "step_id": "prepare",
                    "step_name": "Prepare stack selection manifest",
                    "job_type": JOB_TYPE_TIMESERIES_PREPARE,
                    "payload": {"run_id": run_id},
                    "task_id": task_id,
                },
                {
                    "step_id": "sarscape_processor_preflight",
                    "step_name": "Build SARscape SBAS processor manifest",
                    "job_type": JOB_TYPE_TIMESERIES_SARSCAPE_PREFLIGHT,
                    "payload": {"run_id": run_id},
                    "task_id": task_id,
                    "depends_on": ["prepare"],
                },
            ]
            if execution_mode == "full":
                steps.append(
                    {
                        "step_id": "run_sarscape_sbas",
                        "step_name": "Run SARscape SBAS pipeline",
                        "job_type": JOB_TYPE_TIMESERIES_RUN_SARSCAPE_SBAS,
                        "payload": {"run_id": run_id},
                        "task_id": task_id,
                        "depends_on": ["sarscape_processor_preflight"],
                    }
                )
            return steps

        if execution_mode == "preflight_only":
            return [
                {
                    "step_id": "prepare",
                    "step_name": "Prepare stack selection manifest",
                    "job_type": JOB_TYPE_TIMESERIES_PREPARE,
                    "payload": {"run_id": run_id},
                    "task_id": task_id,
                },
            ]

        return [
            {
                "step_id": "prepare",
                "step_name": "Prepare stack selection manifest",
                "job_type": JOB_TYPE_TIMESERIES_PREPARE,
                "payload": {"run_id": run_id},
                "task_id": task_id,
            },
            {
                "step_id": "stack_prep_initial",
                "step_name": "Build LT-1 stack prep workspace",
                "job_type": JOB_TYPE_TIMESERIES_STACK_PREP,
                "payload": {"run_id": run_id, "refresh": False},
                "task_id": task_id,
                "depends_on": ["prepare"],
            },
            {
                "step_id": "materialize",
                "step_name": "Materialize LT-1 SLC scenes",
                "job_type": JOB_TYPE_TIMESERIES_MATERIALIZE,
                "payload": {"run_id": run_id, "force": False},
                "task_id": task_id,
                "depends_on": ["stack_prep_initial"],
            },
            {
                "step_id": "stack_prep_refresh",
                "step_name": "Refresh stack readiness after materialization",
                "job_type": JOB_TYPE_TIMESERIES_STACK_PREP,
                "payload": {"run_id": run_id, "refresh": True},
                "task_id": task_id,
                "depends_on": ["materialize"],
            },
            {
                "step_id": "run_isce2_stack",
                "step_name": "Run ISCE2 stripmap stack workflow",
                "job_type": JOB_TYPE_TIMESERIES_RUN_ISCE2_STACK,
                "payload": {"run_id": run_id},
                "task_id": task_id,
                "depends_on": ["stack_prep_refresh"],
            },
            {
                "step_id": "run_mintpy_sbas",
                "step_name": "Run MintPy SBAS inversion",
                "job_type": JOB_TYPE_TIMESERIES_RUN_MINTPY_SBAS,
                "payload": {"run_id": run_id},
                "task_id": task_id,
                "depends_on": ["run_isce2_stack"],
            },
            {
                "step_id": "export_publish_bundle",
                "step_name": "Export PS-InSAR publish bundle",
                "job_type": JOB_TYPE_TIMESERIES_EXPORT_PUBLISH,
                "payload": {"run_id": run_id},
                "task_id": task_id,
                "depends_on": ["run_mintpy_sbas"],
            },
            {
                "step_id": "register_psinsar_product",
                "step_name": "Register PS-InSAR product",
                "job_type": JOB_TYPE_TIMESERIES_REGISTER_PRODUCT,
                "payload": {"run_id": run_id},
                "task_id": task_id,
                "depends_on": ["export_publish_bundle"],
            },
        ]

    def _serialize_run(self, run: PsTimeseriesRunORM) -> Dict[str, Any]:
        return {
            "run_id": run.run_id,
            "batch_id": run.batch_id,
            "plan_id": run.plan_id,
            "plan_strategy": run.plan_strategy,
            "product_family": run.product_family,
            "run_name": run.run_name,
            "catalog_name": run.catalog_name,
            "stack_key": run.stack_key,
            "mode": run.mode,
            "engine_code": run.engine_code,
            "processor_code": run.processor_code,
            "runtime_id": run.runtime_id,
            "env_name": run.env_name,
            "wsl_distro": run.wsl_distro,
            "status": run.status,
            "task_id": run.task_id,
            "workflow_run_id": run.workflow_run_id,
            "direction": run.direction,
            "stack_size": run.stack_size,
            "reference_date": run.reference_date,
            "water_mask_mode": run.water_mask_mode,
            "dem_path_windows": run.dem_path_windows,
            "dem_path_wsl": run.dem_path_wsl,
            "orbit_pool_windows": run.orbit_pool_windows,
            "orbit_pool_wsl": run.orbit_pool_wsl,
            "work_root_windows": run.work_root_windows,
            "work_root_wsl": run.work_root_wsl,
            "publish_dir_windows": run.publish_dir_windows,
            "publish_dir_wsl": run.publish_dir_wsl,
            "manifest_path_windows": run.manifest_path_windows,
            "manifest_path_wsl": run.manifest_path_wsl,
            "params_json": run.params_json,
            "summary_json": run.summary_json,
            "input_snapshot_json": run.input_snapshot_json,
            "orbit_summary_json": run.orbit_summary_json,
            "quality_summary_json": run.quality_summary_json,
            "error_message": run.error_message,
            "created_by": run.created_by,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
        }

    def _serialize_workflow_step(self, step: WorkflowStepORM) -> Dict[str, Any]:
        return {
            "step_id": step.step_id,
            "step_name": step.step_name,
            "status": step.status,
            "depends_on": step.depends_on or [],
            "params": step.params or {},
            "outputs": step.outputs or {},
            "error": step.error,
            "created_at": step.created_at,
            "updated_at": step.updated_at,
            "started_at": step.started_at,
            "ended_at": step.ended_at,
        }

    def _materialization_only_blockers(self, blockers: List[str]) -> bool:
        if not blockers:
            return False
        allowed_prefixes = (
            "Materialized .slc/.slc.xml are missing",
            "ISCE data shelve is missing",
        )
        return all(str(item or "").startswith(allowed_prefixes) for item in blockers)

    def _summarize_materialization(self, report: Dict[str, Any]) -> Dict[str, Any]:
        results = report.get("results") or []
        status_counts: Dict[str, int] = {}
        bytes_written = 0
        dates: List[str] = []
        for item in results:
            status = str(item.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
            try:
                bytes_written += int(item.get("bytes_written") or 0)
            except (TypeError, ValueError):
                pass
            normalized = _normalize_date(item.get("date"))
            if normalized:
                dates.append(normalized)
        dates.sort()
        return {
            "generated_at_utc": report.get("generated_at_utc"),
            "scene_count": len(results),
            "status_counts": status_counts,
            "dates": dates,
            "bytes_written_total": bytes_written,
        }

    async def create_run(
        self,
        *,
        batch_id: str,
        run_name: Optional[str] = None,
        reference_date: Optional[str] = None,
        water_mask_mode: str = "synthetic_fallback",
        processor_code: Optional[str] = None,
        execution_mode: Optional[str] = None,
        notes: Optional[str] = None,
        created_by: Optional[str] = None,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        if not settings.TIMESERIES_ENABLED:
            raise ValueError("TIMESERIES_ENABLED is false.")

        normalized_batch_id = str(batch_id or "").strip()
        if not normalized_batch_id:
            raise ValueError("batch_id is required.")

        batch_result = await db.execute(
            select(PsTaskBatchORM).where(PsTaskBatchORM.batch_id == normalized_batch_id)
        )
        batch = batch_result.scalar_one_or_none()
        if batch is None:
            raise ValueError(f"PS batch not found: {normalized_batch_id}")

        normalized_processor_code = self._normalize_processor_code(processor_code)
        normalized_execution_mode = self._normalize_execution_mode(
            execution_mode,
            normalized_processor_code,
        )

        if normalized_processor_code == "sarscape_sbas":
            preflight = await self.get_sarscape_sbas_preflight_report(
                batch_id=normalized_batch_id,
                reference_date=reference_date,
                include_task_discovery=True,
                discovery_timeout_seconds=int(settings.SARSCAPE_SBAS_DISCOVERY_TIMEOUT_SECONDS or 120),
                db=db,
            )
            if not preflight.get("ready_for_pipeline_design"):
                problem_text = "; ".join(str(item) for item in (preflight.get("blockers") or [])[:8]) or "unknown SARscape preflight failure"
                raise ValueError("SARscape SBAS preflight failed: " + problem_text)
            if normalized_execution_mode == "full" and not preflight.get("ready_for_execution"):
                problem_text = "; ".join(str(item) for item in (preflight.get("blockers") or [])[:8]) or "SARscape execution is not ready"
                raise ValueError("SARscape SBAS execution is not ready: " + problem_text)
        else:
            preflight = await self.get_preflight_report(
                batch_id=normalized_batch_id,
                reference_date=reference_date,
                water_mask_mode=water_mask_mode,
                db=db,
            )
            if not preflight.get("overall_ok"):
                problem_text = "; ".join(str(item) for item in (preflight.get("errors") or [])[:8]) or "unknown preflight failure"
                raise ValueError("Timeseries preflight failed: " + problem_text)

        items = await self._load_batch_items(normalized_batch_id, db)
        remark_planning_context = self._extract_planning_context(items)
        stack_plan_context = await self._load_stack_plan_context(db, batch.plan_id)
        planning_context = remark_planning_context
        if stack_plan_context:
            planning_context = {
                **stack_plan_context,
                **(remark_planning_context or {}),
            }
            planning_context["source"] = stack_plan_context.get("source")
            planning_context["plan_id"] = stack_plan_context.get("plan_id")
            planning_context["strategy"] = (
                stack_plan_context.get("strategy")
                or planning_context.get("strategy")
            )
            planning_context["scene_count"] = (
                stack_plan_context.get("scene_count")
                or planning_context.get("scene_count")
            )
            planning_context["stack_key"] = (
                stack_plan_context.get("stack_key")
                or planning_context.get("stack_key")
            )
            planning_context["group_key"] = (
                stack_plan_context.get("group_key")
                or planning_context.get("group_key")
            )
            if not planning_context.get("scenes"):
                planning_context["scenes"] = stack_plan_context.get("scenes") or []
            if not planning_context.get("network_edges"):
                planning_context["network_edges"] = stack_plan_context.get("network_edges") or []
        resolved_plan_id = str(
            batch.plan_id
            or ((planning_context or {}).get("plan_id"))
            or ""
        ).strip() or None
        resolved_plan_strategy = str(
            batch.plan_strategy
            or ((planning_context or {}).get("strategy"))
            or ""
        ).strip() or None
        stack_dates = [
            normalized
            for normalized in (_normalize_date(item.imaging_date) for item in items)
            if normalized
        ]
        if len(stack_dates) != len(items):
            raise ValueError("Every PS item must have a valid YYYYMMDD imaging_date.")

        effective_reference_date = self._choose_reference_date(stack_dates, reference_date)
        if not effective_reference_date:
            raise ValueError("Unable to determine a reference date for this PS batch.")

        normalized_water_mask_mode = (
            str(water_mask_mode or "synthetic_fallback").strip() or "synthetic_fallback"
        )
        if (
            normalized_water_mask_mode == "synthetic_fallback"
            and not settings.TIMESERIES_ALLOW_SYNTHETIC_WATER_MASK
        ):
            raise ValueError("synthetic_fallback water mask mode is disabled by configuration.")

        run_id = str(uuid.uuid4())
        run_name_text = str(run_name or "").strip() or f"SBAS_{normalized_batch_id}_{run_id[:8]}"
        work_root_windows = _normalize_path(os.path.join(settings.TIMESERIES_WORK_ROOT, run_id))
        stack_preview = await self._resolve_stack_scene_records(
            items=items,
            batch_direction=batch.direction,
            run_id=run_id,
            work_root_windows=work_root_windows,
            db=db,
        )
        stack_key = str(stack_preview.get("stack_key") or "").strip() or _build_stack_key(
            stack_preview.get("group_key")
        )
        runtime = self._processor_runtime(normalized_processor_code)
        paths = self._derive_paths(run_id, stack_key=stack_key)
        selected_manifest_path = Path(paths["work_root_windows"]) / "input" / "selected_stack_manifest.json"
        task_name = f"SBAS timeseries run {run_name_text} [{normalized_processor_code}]"
        if normalized_processor_code == "sarscape_sbas":
            queued_preflight_summary = {
                "ready_for_pipeline_design": bool(preflight.get("ready_for_pipeline_design")),
                "ready_for_execution": bool(preflight.get("ready_for_execution")),
                "blocker_count": len(preflight.get("blockers") or []),
                "effective_reference_date": effective_reference_date,
            }
        else:
            queued_preflight_summary = {
                "overall_ok": bool(preflight.get("overall_ok")),
                "warning_count": len(preflight.get("warnings") or []),
                "effective_reference_date": preflight.get("reference_date_effective"),
            }
        task_id: Optional[str] = None

        try:
            task_id = await task_service.create_task(
                TASK_TYPE_TIMESERIES_RUN,
                task_name,
                params={
                    "run_id": run_id,
                    "batch_id": normalized_batch_id,
                    "plan_id": resolved_plan_id,
                    "reference_date": effective_reference_date,
                    "processor_code": normalized_processor_code,
                    "execution_mode": normalized_execution_mode,
                },
                db=db,
            )

            run = PsTimeseriesRunORM(
                run_id=run_id,
                batch_id=normalized_batch_id,
                plan_id=resolved_plan_id,
                plan_strategy=resolved_plan_strategy,
                product_family="timeseries",
                run_name=run_name_text,
                catalog_name=CATALOG_NAME_PSINSAR,
                stack_key=stack_key,
                mode="sbas",
                engine_code=str(runtime["engine_code"] or ""),
                processor_code=str(runtime["processor_code"] or ""),
                runtime_id=runtime["runtime_id"],
                env_name=runtime["env_name"],
                wsl_distro=runtime["wsl_distro"],
                status=STATUS_PENDING,
                task_id=task_id,
                direction=str(batch.direction or "").strip().upper() or None,
                stack_size=len(items),
                reference_date=effective_reference_date,
                water_mask_mode=normalized_water_mask_mode,
                dem_path_windows=runtime["dem_path_windows"],
                dem_path_wsl=runtime["dem_path_wsl"],
                orbit_pool_windows=runtime["orbit_pool_windows"],
                orbit_pool_wsl=runtime["orbit_pool_wsl"],
                work_root_windows=paths["work_root_windows"],
                work_root_wsl=paths["work_root_wsl"],
                publish_dir_windows=paths["publish_dir_windows"],
                publish_dir_wsl=paths["publish_dir_wsl"],
                manifest_path_windows=str(selected_manifest_path),
                manifest_path_wsl=_windows_path_to_wsl_mount(str(selected_manifest_path)),
                params_json={
                    "batch_id": normalized_batch_id,
                    "plan_id": resolved_plan_id,
                    "plan_strategy": resolved_plan_strategy,
                    "requested_reference_date": _normalize_date(reference_date),
                    "effective_reference_date": effective_reference_date,
                    "water_mask_mode": normalized_water_mask_mode,
                    "processor_code": normalized_processor_code,
                    "execution_mode": normalized_execution_mode,
                    "notes": str(notes or "").strip() or None,
                    "stack_workflow": runtime["workflow"],
                    "group_key": stack_preview.get("group_key"),
                    "stack_key": stack_key,
                    "planning_context": planning_context,
                    "preflight": preflight,
                },
                summary_json={
                    "phase": "queued",
                    "workflow": runtime["workflow"],
                    "processor_code": normalized_processor_code,
                    "execution_mode": normalized_execution_mode,
                    "plan_id": resolved_plan_id,
                    "plan_strategy": resolved_plan_strategy,
                    "group_key": stack_preview.get("group_key"),
                    "stack_key": stack_key,
                    "stack_dates": stack_dates,
                    "task_name": task_name,
                    "planning_context": {
                        "source": (planning_context or {}).get("source"),
                        "plan_id": (planning_context or {}).get("plan_id"),
                        "strategy": (planning_context or {}).get("strategy"),
                        "scene_count": (planning_context or {}).get("scene_count"),
                    } if planning_context else None,
                    "preflight": queued_preflight_summary,
                },
                input_snapshot_json={
                    "batch_id": normalized_batch_id,
                    "batch_name": batch.name,
                    "direction": batch.direction,
                    "plan_id": resolved_plan_id,
                    "plan_strategy": resolved_plan_strategy,
                    "scene_count": len(items),
                    "group_key": stack_preview.get("group_key"),
                    "stack_key": stack_key,
                    "stack_dates": stack_dates,
                    "source_root_windows": stack_preview.get("source_root_windows"),
                    "planning_context": planning_context,
                    "processor_code": normalized_processor_code,
                    "execution_mode": normalized_execution_mode,
                    "items": self._scene_payload(items),
                },
                orbit_summary_json={
                    "stage": "queued",
                    "scene_count": len(items),
                    "item_has_orbit_data_count": sum(1 for item in items if item.has_orbit_data),
                    "orbit_pool_windows": runtime["orbit_pool_windows"],
                },
                quality_summary_json={
                    "plan_id": resolved_plan_id,
                    "plan_strategy": resolved_plan_strategy,
                    "processor_code": normalized_processor_code,
                    "execution_mode": normalized_execution_mode,
                    "water_mask_mode": normalized_water_mask_mode,
                    "synthetic_water_mask_allowed": bool(settings.TIMESERIES_ALLOW_SYNTHETIC_WATER_MASK),
                    "notes": str(notes or "").strip() or None,
                    "planning_context": planning_context,
                    "preflight": preflight,
                },
                created_by=created_by,
            )
            db.add(run)
            await db.flush()

            workflow_name = (
                "psinsar_sarscape_sbas_chain"
                if normalized_processor_code == "sarscape_sbas"
                else "psinsar_sbas_full_chain"
            )
            workflow_run_id = await workflow_service.create_run(
                workflow_name=workflow_name,
                steps=self._workflow_steps(
                    run_id=run_id,
                    task_id=task_id,
                    processor_code=normalized_processor_code,
                    execution_mode=normalized_execution_mode,
                ),
                params={
                    "run_id": run_id,
                    "batch_id": normalized_batch_id,
                    "plan_id": resolved_plan_id,
                    "reference_date": effective_reference_date,
                    "workflow": runtime["workflow"],
                    "processor_code": normalized_processor_code,
                    "execution_mode": normalized_execution_mode,
                },
                tags={
                    "catalog_name": CATALOG_NAME_PSINSAR,
                    "product_family": "timeseries",
                    "processor_code": normalized_processor_code,
                    "engine_code": runtime["engine_code"],
                    "execution_mode": normalized_execution_mode,
                    "batch_id": normalized_batch_id,
                    "plan_id": resolved_plan_id,
                    "stack_key": stack_key,
                },
                created_by=created_by,
                db=db,
            )
            run.workflow_run_id = workflow_run_id
            await db.commit()
            await db.refresh(run)

            return {
                "run_id": run.run_id,
                "task_id": run.task_id,
                "workflow_run_id": run.workflow_run_id,
                "status": run.status,
                "plan_id": run.plan_id,
                "reference_date": run.reference_date,
                "stack_size": run.stack_size,
                "processor_code": run.processor_code,
                "execution_mode": normalized_execution_mode,
            }
        except Exception as exc:
            await db.rollback()
            if task_id:
                try:
                    await task_service.update_task(
                        task_id,
                        status="FAILED",
                        message=f"Failed to create SBAS run: {exc}",
                    )
                except Exception:
                    pass
            raise

    async def prepare_run(self, run_id: str, *, db: AsyncSession) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        items = await self._load_batch_items(run.batch_id, db)
        self._prepare_workdirs(run)

        run.status = STATUS_PREPARING
        run.error_message = None
        if run.started_at is None:
            run.started_at = _utcnow()

        stack_payload = await self._resolve_stack_scene_records(
            items=items,
            batch_direction=run.direction,
            run_id=run.run_id,
            work_root_windows=run.work_root_windows or "",
            db=db,
        )
        stack_dates = [
            normalized
            for normalized in (
                _normalize_date(scene.get("imaging_date"))
                for scene in (stack_payload.get("scenes") or [])
            )
            if normalized
        ]
        if len(stack_dates) != len(stack_payload.get("scenes") or []):
            raise ValueError("Resolved stack scenes must all have valid YYYYMMDD dates.")

        effective_reference_date = self._choose_reference_date(stack_dates, run.reference_date)
        if not effective_reference_date:
            raise ValueError("Unable to determine reference date during prepare.")

        run_params = run.params_json if isinstance(run.params_json, dict) else {}
        planning_context = run_params.get("planning_context") if isinstance(run_params.get("planning_context"), dict) else {}
        network_edges = (
            planning_context.get("network_edges")
            if isinstance(planning_context.get("network_edges"), list)
            else []
        )
        selection_params = {
            key: planning_context.get(key)
            for key in (
                "initial_overlap_threshold",
                "final_overlap_threshold",
                "time_baseline_min",
                "time_baseline_max",
                "spatial_baseline_max_meters",
                "network_overlap_threshold",
                "num_connections",
            )
            if planning_context.get(key) is not None
        }

        selected_manifest_path = self._selected_manifest_path(run)
        selected_network_edges_path = self._selected_network_edges_path(run)
        prepared_at_utc = _utcnow().replace(microsecond=0).isoformat() + "Z"
        prepared_stack_id = "pss_" + _stable_digest(
            run.run_id,
            run.batch_id,
            run.plan_id,
            ",".join(stack_dates),
            len(network_edges),
            length=16,
        )
        candidate_pool_source = {
            "source": planning_context.get("source") or "ps_task_batch",
            "plan_id": run.plan_id or planning_context.get("plan_id"),
            "batch_id": run.batch_id,
            "strategy": run.plan_strategy or planning_context.get("strategy"),
            "candidate_scene_count": planning_context.get("scene_count", len(items)),
            "selected_scene_count": len(stack_dates),
            "candidate_network_edge_count": planning_context.get("network_edge_count", len(network_edges)),
            "selected_network_edge_count": len(network_edges),
            "stack_key": planning_context.get("stack_key") or stack_payload.get("stack_key"),
            "group_key": planning_context.get("group_key") or stack_payload.get("group_key"),
        }
        artifact_paths = {
            "selected_stack_manifest_path_windows": str(selected_manifest_path),
            "selected_stack_manifest_path_wsl": _windows_path_to_wsl_mount(str(selected_manifest_path)),
            "selected_network_edges_path_windows": str(selected_network_edges_path),
            "selected_network_edges_path_wsl": _windows_path_to_wsl_mount(str(selected_network_edges_path)),
        }
        selected_network_edges_doc = {
            "schema": PREPARED_NETWORK_EDGES_SCHEMA,
            "prepared_stack_id": prepared_stack_id,
            "run_id": run.run_id,
            "batch_id": run.batch_id,
            "plan_id": run.plan_id,
            "graph_role": "system_selected_planning_audit_graph",
            "graph_policy": (
                "For SARscape wf_sbas, these edges define the system planning/audit graph. "
                "SARscape may rebuild the executable graph internally."
            ),
            "network_edge_count": len(network_edges),
            "network_edges": network_edges,
            "created_at_utc": prepared_at_utc,
        }
        _write_json(selected_network_edges_path, selected_network_edges_doc)

        selected_manifest = {
            **stack_payload,
            "schema": "insar.timeseries-stack/v1",
            "prepared_stack_schema": PREPARED_STACK_SCHEMA,
            "manifest_role": PREPARED_STACK_MANIFEST_ROLE,
            "prepared_stack_id": prepared_stack_id,
            "prepared_at_utc": prepared_at_utc,
            "source_plan_id": run.plan_id,
            "source_batch_id": run.batch_id,
            "candidate_pool_source": candidate_pool_source,
            "run_id": run.run_id,
            "batch_id": run.batch_id,
            "plan_id": run.plan_id,
            "plan_strategy": run.plan_strategy,
            "run_name": run.run_name,
            "task_id": run.task_id,
            "catalog_name": run.catalog_name,
            "mode": run.mode,
            "engine_code": run.engine_code,
            "processor_code": run.processor_code,
            "reference_date": effective_reference_date,
            "stack_dates": stack_dates,
            "water_mask_mode": run.water_mask_mode,
            "selection_params": selection_params,
            "network_edges": network_edges,
            "network_edge_count": len(network_edges),
            "artifacts": artifact_paths,
            "production_contract": {
                "input_policy": "prepared_stack_only",
                "catalog_scan_allowed_after_prepare": False,
                "scene_selection_frozen": True,
                "network_edges_role": "planning_audit_graph",
                "sarscape_wf_sbas_graph_policy": (
                    "wf_sbas accepts the prepared scene stack; system network_edges "
                    "are retained for audit/comparison until explicit graph injection is verified."
                ),
            },
            "planning_context_summary": {
                "source": planning_context.get("source"),
                "plan_id": planning_context.get("plan_id"),
                "strategy": planning_context.get("strategy"),
                "scene_count": planning_context.get("scene_count"),
                "stack_key": planning_context.get("stack_key"),
                "group_key": planning_context.get("group_key"),
                "network_edge_count": planning_context.get("network_edge_count", len(network_edges)),
            } if planning_context else None,
            "notes": run_params.get("notes"),
            "requested_reference_date": run_params.get("requested_reference_date"),
            "processing_workflow": run_params.get("stack_workflow") or settings.TIMESERIES_STACK_WORKFLOW,
        }

        require_sarscape_inputs = str(run.processor_code or "").strip() == "sarscape_sbas"
        validation = self._build_prepared_stack_validation(
            selected_manifest,
            manifest_path=selected_manifest_path,
            expected_run_id=run.run_id,
            expected_processor_code=str(run.processor_code or "").strip() or None,
            require_network_edges=require_sarscape_inputs,
            require_dem=require_sarscape_inputs,
            dem_path=run.dem_path_windows,
        )
        if require_sarscape_inputs and not validation.get("ok"):
            blockers = "; ".join(str(item) for item in (validation.get("blockers") or [])[:8])
            raise ValueError(
                "Prepared SARscape SBAS stack validation failed: "
                + (blockers or "unknown validation blocker")
            )
        selected_manifest["prepared_stack_validation"] = validation
        selected_manifest["manifest_checksum"] = _sha256_json(
            {
                key: value
                for key, value in selected_manifest.items()
                if key not in {"manifest_checksum"}
            }
        )
        _write_json(selected_manifest_path, selected_manifest)

        run.status = STATUS_PREPARED
        run.product_family = run.product_family or "timeseries"
        run.stack_key = str(selected_manifest.get("stack_key") or run.stack_key or "").strip() or run.stack_key
        run.reference_date = effective_reference_date
        run.stack_size = len(stack_dates)
        if run.stack_key:
            run.publish_dir_windows = _compose_publish_dir(run.run_id, run.stack_key)
            run.publish_dir_wsl = _windows_path_to_wsl_mount(run.publish_dir_windows)
        run.manifest_path_windows = str(selected_manifest_path)
        run.manifest_path_wsl = _windows_path_to_wsl_mount(str(selected_manifest_path))
        run.input_snapshot_json = {
            "batch_id": run.batch_id,
            "direction": run.direction,
            "scene_count": len(stack_dates),
            "stack_dates": stack_dates,
            "group_key": selected_manifest.get("group_key"),
            "stack_key": selected_manifest.get("stack_key"),
            "tile_key": selected_manifest.get("tile_key"),
            "source_root_windows": selected_manifest.get("source_root_windows"),
            "selected_manifest_path_windows": str(selected_manifest_path),
            "selected_network_edges_path_windows": str(selected_network_edges_path),
            "prepared_stack_schema": PREPARED_STACK_SCHEMA,
            "prepared_stack_id": prepared_stack_id,
            "prepared_stack_validation": validation,
            "network_edge_count": len(network_edges),
            "network_edges": network_edges,
            "items": selected_manifest.get("scenes") or [],
        }
        run.orbit_summary_json = {
            "stage": "prepare",
            "scene_count": len(stack_dates),
            "item_has_orbit_data_count": sum(1 for item in items if item.has_orbit_data),
            "orbit_pool_windows": run.orbit_pool_windows,
            "source_root_windows": selected_manifest.get("source_root_windows"),
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "prepare_complete",
        }
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "prepared",
            "workflow": selected_manifest.get("processing_workflow") or settings.TIMESERIES_STACK_WORKFLOW,
            "group_key": selected_manifest.get("group_key"),
            "stack_key": selected_manifest.get("stack_key"),
            "tile_key": selected_manifest.get("tile_key"),
            "reference_date": effective_reference_date,
            "stack_dates": stack_dates,
            "scene_count": len(stack_dates),
            "prepared_stack_schema": PREPARED_STACK_SCHEMA,
            "prepared_stack_id": prepared_stack_id,
            "prepared_stack_validation": {
                "ok": bool(validation.get("ok")),
                "blockers": validation.get("blockers") or [],
                "warnings": validation.get("warnings") or [],
                "network_edge_count": validation.get("network_edge_count"),
            },
            "selected_manifest_path_windows": str(selected_manifest_path),
            "selected_manifest_path_wsl": _windows_path_to_wsl_mount(str(selected_manifest_path)),
            "selected_network_edges_path_windows": str(selected_network_edges_path),
            "selected_network_edges_path_wsl": _windows_path_to_wsl_mount(str(selected_network_edges_path)),
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "scene_count": len(stack_dates),
            "reference_date": effective_reference_date,
            "manifest_path": str(selected_manifest_path),
            "selected_network_edges_path": str(selected_network_edges_path),
            "prepared_stack_id": prepared_stack_id,
            "prepared_stack_validation_ok": bool(validation.get("ok")),
            "group_key": selected_manifest.get("group_key"),
            "tile_key": selected_manifest.get("tile_key"),
            "stack_dates": stack_dates,
        }

    async def build_stack_prep(
        self,
        run_id: str,
        *,
        refresh: bool = False,
        use_configured_dem: bool = True,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        self._prepare_workdirs(run)

        selected_manifest_path = self._selected_manifest_path(run)
        if not selected_manifest_path.exists():
            raise FileNotFoundError(f"Selected stack manifest not found: {selected_manifest_path}")

        script_path = Path(str(settings.TIMESERIES_STACK_PREP_SCRIPT or "").strip())
        if not script_path.exists():
            raise FileNotFoundError(f"Stack-prep script not found: {script_path}")

        step_name = "stack_prep_refresh" if refresh else "stack_prep_initial"
        run.status = STATUS_STACK_PREPARING
        run.error_message = None

        argv = [
            sys.executable,
            str(script_path),
            "--manifest-path",
            str(selected_manifest_path),
            "--scratch-root",
            str(run.work_root_windows or ""),
            "--workflow",
            str(settings.TIMESERIES_STACK_WORKFLOW or STACK_PREP_WORKFLOW),
        ]
        if str(run.orbit_pool_windows or "").strip():
            argv.extend(["--orbit-pool", str(run.orbit_pool_windows)])
        if use_configured_dem and str(run.dem_path_windows or "").strip():
            argv.extend(["--dem-path", str(run.dem_path_windows)])

        timeout_seconds = max(60, int(settings.TIMESERIES_WSL_STEP_TIMEOUT_SECONDS))
        logs_dir = self._step_logs_dir(run)

        def _run_local() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )

        try:
            completed = await asyncio.to_thread(_run_local)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            _write_step_logs(logs_dir, step_name, stdout, stderr)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"stack-prep script failed (exit={completed.returncode}): "
                    f"{_tail_text(stderr or stdout)}"
                )
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            _write_step_logs(logs_dir, step_name, stdout, stderr)
            raise RuntimeError(f"stack-prep script timed out after {timeout_seconds}s") from exc

        generated_manifest_path = self._generated_stack_manifest_path(run)
        if not generated_manifest_path.exists():
            raise FileNotFoundError(f"Generated stack_input_manifest.json not found: {generated_manifest_path}")

        report = _read_json(generated_manifest_path)
        blockers = [str(item) for item in (report.get("readiness", {}).get("blocking_reasons") or [])]
        ready = bool(report.get("readiness", {}).get("ready_for_stackStripMap_nofocus"))
        materialization_only = self._materialization_only_blockers(blockers)

        if refresh and not ready:
            raise ValueError("Stack refresh did not reach ready state: " + "; ".join(blockers or ["unknown blocker"]))
        if (not refresh) and blockers and (not ready) and (not materialization_only):
            raise ValueError("Stack-prep unresolved blockers: " + "; ".join(blockers))

        orbit_mode_counts: Dict[str, int] = {}
        stack_dates: List[str] = []
        for scene in report.get("scenes") or []:
            mode = str(scene.get("orbit_resolution_mode") or "unknown")
            orbit_mode_counts[mode] = orbit_mode_counts.get(mode, 0) + 1
            date_text = _normalize_date(scene.get("date"))
            if date_text:
                stack_dates.append(date_text)
        stack_dates.sort()

        stdout_log = logs_dir / f"{step_name}.stdout.log"
        stderr_log = logs_dir / f"{step_name}.stderr.log"
        step_summary = {
            "refresh": refresh,
            "ready_for_stackStripMap_nofocus": ready,
            "blocking_reasons": blockers,
            "workspace": report.get("workspace") or {},
            "stack_command": report.get("stack_command") or {},
            "stdout_log_windows": str(stdout_log),
            "stderr_log_windows": str(stderr_log),
            "stdout_log_wsl": _windows_path_to_wsl_mount(str(stdout_log)),
            "stderr_log_wsl": _windows_path_to_wsl_mount(str(stderr_log)),
        }

        run.manifest_path_windows = str(generated_manifest_path)
        run.manifest_path_wsl = _windows_path_to_wsl_mount(str(generated_manifest_path))
        run.stack_key = str(report.get("stack_key") or run.stack_key or "").strip() or run.stack_key
        if run.stack_key:
            run.publish_dir_windows = _compose_publish_dir(run.run_id, run.stack_key)
            run.publish_dir_wsl = _windows_path_to_wsl_mount(run.publish_dir_windows)
        run.reference_date = str(report.get("reference_date") or run.reference_date or "").strip() or run.reference_date
        run.stack_size = int(report.get("scene_count") or len(stack_dates) or run.stack_size or 0)
        run.orbit_summary_json = {
            "stage": "stack_prep_refresh" if refresh else "stack_prep_initial",
            "scene_count": len(report.get("scenes") or []),
            "all_orbits_resolved": bool(report.get("readiness", {}).get("all_orbits_resolved")),
            "orbit_mode_counts": orbit_mode_counts,
            "orbit_pool_windows": (report.get("resolved_dependencies") or {}).get("orbit_pool_windows"),
            "dem_path_windows": (report.get("resolved_dependencies") or {}).get("dem_path_windows"),
            "blocking_reasons": blockers,
        }
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "stack_ready" if (refresh and ready) else "stack_prepared",
            "workflow": report.get("processing_workflow") or settings.TIMESERIES_STACK_WORKFLOW,
            "group_key": report.get("group_key"),
            "stack_key": report.get("stack_key"),
            "tile_key": report.get("tile_key"),
            "reference_date": report.get("reference_date"),
            "stack_dates": stack_dates,
            "scene_count": int(report.get("scene_count") or len(stack_dates)),
            "selected_manifest_path_windows": str(selected_manifest_path),
            "generated_stack_manifest_path_windows": str(generated_manifest_path),
            "generated_stack_manifest_path_wsl": _windows_path_to_wsl_mount(str(generated_manifest_path)),
            "stack_prep": step_summary,
        }
        run.status = STATUS_STACK_READY if (refresh and ready) else STATUS_STACK_PREPARED

        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "ready": ready,
            "blockers": blockers,
            "manifest_path": str(generated_manifest_path),
            "stack_dates": stack_dates,
            "scene_count": int(report.get("scene_count") or len(stack_dates)),
        }

    async def build_sarscape_processor_preflight(
        self,
        run_id: str,
        *,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        self._prepare_workdirs(run)
        if str(run.processor_code or "").strip() != "sarscape_sbas":
            raise ValueError(f"Run is not a SARscape SBAS run: {run.processor_code}")

        selected_manifest_path = self._selected_manifest_path(run)
        if not selected_manifest_path.exists():
            raise FileNotFoundError(f"Selected stack manifest not found: {selected_manifest_path}")

        stack_manifest = _read_json(selected_manifest_path)
        prepared_validation = self._require_prepared_stack_manifest(
            stack_manifest,
            manifest_path=selected_manifest_path,
            run=run,
            require_network_edges=True,
            require_dem=True,
        )
        discovery_timeout = int(settings.SARSCAPE_SBAS_DISCOVERY_TIMEOUT_SECONDS or 120)
        preflight = await asyncio.to_thread(
            build_sarscape_sbas_preflight_report,
            stack_manifest,
            include_task_discovery=True,
            discovery_timeout_seconds=max(10, discovery_timeout),
        )
        processor_manifest = preflight.get("processor_manifest") or build_sarscape_sbas_processor_manifest(
            stack_manifest,
            discovery_report=preflight.get("task_discovery") if isinstance(preflight, dict) else None,
        )
        processor_manifest_path = self._sarscape_processor_manifest_path(run)
        write_sarscape_sbas_processor_manifest(processor_manifest_path, processor_manifest)
        run_params = run.params_json if isinstance(run.params_json, dict) else {}
        execution_mode = str(run_params.get("execution_mode") or "").strip()

        run.status = STATUS_STACK_READY if preflight.get("ready_for_execution") else STATUS_PREPARED
        if execution_mode == "preflight_only":
            run.ended_at = _utcnow()
        run.error_message = None
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "sarscape_preflight_complete",
            "workflow": "sarscape_sbas",
            "sarscape_sbas": {
                "ready_for_pipeline_design": bool(preflight.get("ready_for_pipeline_design")),
                "ready_for_execution": bool(preflight.get("ready_for_execution")),
                "blockers": preflight.get("blockers") or [],
                "processor_manifest_path_windows": str(processor_manifest_path),
                "task_count": len((processor_manifest or {}).get("task_sequence") or []),
                "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
                "prepared_stack_validation": {
                    "ok": bool(prepared_validation.get("ok")),
                    "warnings": prepared_validation.get("warnings") or [],
                    "network_edge_count": prepared_validation.get("network_edge_count"),
                },
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "phase": "sarscape_preflight_complete",
            "sarscape_sbas": {
                "ready_for_pipeline_design": bool(preflight.get("ready_for_pipeline_design")),
                "ready_for_execution": bool(preflight.get("ready_for_execution")),
                "blockers": preflight.get("blockers") or [],
                "parameter_template": (processor_manifest or {}).get("parameter_template"),
                "network_summary": (processor_manifest or {}).get("network_summary"),
                "prepared_stack_validation": prepared_validation,
            },
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "execution_mode": execution_mode,
            "ready_for_pipeline_design": bool(preflight.get("ready_for_pipeline_design")),
            "ready_for_execution": bool(preflight.get("ready_for_execution")),
            "blockers": preflight.get("blockers") or [],
            "processor_manifest_path": str(processor_manifest_path),
            "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
            "prepared_stack_validation_ok": bool(prepared_validation.get("ok")),
            "task_count": len((processor_manifest or {}).get("task_sequence") or []),
        }

    async def run_sarscape_sbas(
        self,
        run_id: str,
        *,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        self._prepare_workdirs(run)
        if str(run.processor_code or "").strip() != "sarscape_sbas":
            raise ValueError(f"Run is not a SARscape SBAS run: {run.processor_code}")

        selected_manifest_path = self._selected_manifest_path(run)
        if not selected_manifest_path.exists():
            raise FileNotFoundError(f"Selected stack manifest not found: {selected_manifest_path}")

        stack_manifest = _read_json(selected_manifest_path)
        prepared_validation = self._require_prepared_stack_manifest(
            stack_manifest,
            manifest_path=selected_manifest_path,
            run=run,
            require_network_edges=True,
            require_dem=True,
        )
        run.status = STATUS_STACK_RUNNING
        run.error_message = None
        await db.commit()
        await db.refresh(run)

        timeout_seconds = int(settings.SARSCAPE_SBAS_STEP_TIMEOUT_SECONDS or settings.ENVI_PER_TASK_TIMEOUT or 21600)
        execution_report = await asyncio.to_thread(
            execute_sarscape_sbas_template_workflow,
            stack_manifest,
            work_root=str(run.work_root_windows or ""),
            selected_manifest_path=str(selected_manifest_path),
            timeout_seconds=timeout_seconds,
        )
        report_path = self._sarscape_execution_report_path(run)
        _write_json(report_path, execution_report)

        run.status = STATUS_STACK_COMPLETED
        run.ended_at = _utcnow()
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "sarscape_sbas_completed",
            "workflow": "sarscape_sbas",
            "sarscape_sbas": {
                **((run.summary_json or {}).get("sarscape_sbas") or {}),
                "execution_report_path_windows": str(report_path),
                "output_root_windows": execution_report.get("output_root"),
                "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
                "selected_network_edges_path_windows": execution_report.get("selected_network_edges_path"),
                "task_count": execution_report.get("task_count"),
                "executed_tasks": execution_report.get("executed_tasks") or [],
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "phase": "sarscape_sbas_complete",
            "sarscape_sbas": {
                **((run.quality_summary_json or {}).get("sarscape_sbas") or {}),
                "execution_report_path_windows": str(report_path),
                "prepared_stack_validation": prepared_validation,
                "task_count": execution_report.get("task_count"),
            },
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "output_root": execution_report.get("output_root"),
            "report_path": str(report_path),
            "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
            "task_count": execution_report.get("task_count"),
        }

    async def materialize_run(
        self,
        run_id: str,
        *,
        force: bool = False,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        self._prepare_workdirs(run)

        generated_manifest_path = self._generated_stack_manifest_path(run)
        if not generated_manifest_path.exists():
            raise FileNotFoundError(f"stack_input_manifest.json not found: {generated_manifest_path}")

        run.status = STATUS_MATERIALIZING
        run.error_message = None

        materialize_script_wsl = _windows_path_to_wsl_mount(settings.TIMESERIES_MATERIALIZE_SCRIPT)
        stack_manifest_wsl = _windows_path_to_wsl_mount(str(generated_manifest_path))
        python_wsl = self._effective_python_wsl(run)
        distro = self._effective_wsl_distro(run)
        if not python_wsl:
            raise ValueError("TIMESERIES_PYTHON is not configured.")
        if not materialize_script_wsl:
            raise ValueError("TIMESERIES_MATERIALIZE_SCRIPT could not be resolved to a WSL path.")
        if not stack_manifest_wsl:
            raise ValueError("Generated stack manifest could not be resolved to a WSL path.")

        command_parts = [
            shlex.quote(python_wsl),
            shlex.quote(materialize_script_wsl),
            "--stack-manifest",
            shlex.quote(stack_manifest_wsl),
        ]
        if force:
            command_parts.append("--force")
        command = " ".join(command_parts)

        timeout_seconds = max(60, int(settings.TIMESERIES_WSL_STEP_TIMEOUT_SECONDS))
        logs_dir = self._step_logs_dir(run)
        rc, stdout, stderr = await asyncio.to_thread(
            run_wsl_command,
            command,
            distro,
            timeout_seconds,
            None,
        )
        _write_step_logs(logs_dir, "materialize", stdout, stderr)
        if rc != 0:
            raise RuntimeError(f"materialize script failed (exit={rc}): {_tail_text(stderr or stdout)}")

        summary_path = generated_manifest_path.parent / "materialization_summary.json"
        if not summary_path.exists():
            raise FileNotFoundError(f"materialization_summary.json not found: {summary_path}")

        report = _read_json(summary_path)
        materialization_summary = self._summarize_materialization(report)
        materialization_summary["report_path_windows"] = str(summary_path)
        materialization_summary["report_path_wsl"] = _windows_path_to_wsl_mount(str(summary_path))

        run.status = STATUS_MATERIALIZED
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "materialized",
            "materialization": materialization_summary,
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "materialization_complete",
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "scene_count": materialization_summary["scene_count"],
            "status_counts": materialization_summary["status_counts"],
            "summary_path": str(summary_path),
        }

    async def run_isce2_stack(
        self,
        run_id: str,
        *,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        self._prepare_workdirs(run)

        report = self._generated_stack_manifest_payload(run)
        generated_manifest_path = self._generated_stack_manifest_path(run)
        stack_manifest_wsl = _windows_path_to_wsl_mount(str(generated_manifest_path))
        prepare_dem_script_wsl = _windows_path_to_wsl_mount(settings.TIMESERIES_PREPARE_DEM_SCRIPT)
        if not stack_manifest_wsl:
            raise ValueError("Generated stack manifest could not be resolved to a WSL path.")
        if not prepare_dem_script_wsl:
            raise ValueError("TIMESERIES_PREPARE_DEM_SCRIPT could not be resolved to a WSL path.")

        run.status = STATUS_STACK_RUNNING
        run.error_message = None
        python_wsl = self._effective_python_wsl(run)
        env_name = self._effective_env_name(run)
        scratch_root_wsl = _windows_path_to_wsl_mount(str(Path(run.work_root_windows or "")))
        if not scratch_root_wsl:
            raise ValueError("Run work root could not be resolved to a WSL path.")

        prepare_dem_command = " ".join(
            [
                shlex.quote(python_wsl),
                shlex.quote(prepare_dem_script_wsl),
                "--stack-manifest",
                shlex.quote(stack_manifest_wsl),
            ]
        )
        prepare_dem_result = await self._run_wsl_step(
            run,
            step_name="isce2_prepare_local_dem",
            command=prepare_dem_command,
        )

        await self.build_stack_prep(
            run.run_id,
            refresh=True,
            use_configured_dem=False,
            db=db,
        )
        run = await self._load_run(run_id, db)
        report = self._generated_stack_manifest_payload(run)
        run.status = STATUS_STACK_RUNNING
        run.error_message = None
        await db.commit()
        await db.refresh(run)

        stack_command_argv = list((report.get("stack_command") or {}).get("argv") or [])
        if not stack_command_argv:
            raise ValueError("stack_input_manifest.json is missing stack_command.argv")
        if "-n" in stack_command_argv:
            env_index = stack_command_argv.index("-n") + 1
            if env_index < len(stack_command_argv):
                stack_command_argv[env_index] = env_name
        stack_generate_command = " ".join(shlex.quote(str(item)) for item in stack_command_argv)
        stack_generate_result = await self._run_wsl_step(
            run,
            step_name="isce2_generate_runfiles",
            command=stack_generate_command,
        )

        stack_work_dir = self._stack_work_dir(run, report)
        run_files_dir = stack_work_dir / "run_files"
        if not run_files_dir.exists():
            raise FileNotFoundError(f"Generated run_files directory not found: {run_files_dir}")
        for run_file_name in STACK_RUN_FILE_SEQUENCE:
            if not (run_files_dir / run_file_name).exists():
                raise FileNotFoundError(f"Expected run file not found: {run_files_dir / run_file_name}")

        runner_script_wsl = _windows_path_to_wsl_mount(settings.TIMESERIES_STACK_RUNNER_SCRIPT)
        if not runner_script_wsl:
            raise ValueError("TIMESERIES_STACK_RUNNER_SCRIPT could not be resolved to a WSL path.")

        allow_synthetic_watermask = (
            "1"
            if (
                bool(settings.TIMESERIES_ALLOW_SYNTHETIC_WATER_MASK)
                and str(run.water_mask_mode or "").strip().lower() == "synthetic_fallback"
            )
            else "0"
        )
        run_step_results: List[Dict[str, Any]] = []
        for run_file_name in STACK_RUN_FILE_SEQUENCE:
            step_result = await self._run_wsl_step(
                run,
                step_name=f"isce2_{run_file_name}",
                command=" ".join(
                    [
                        "env",
                        f"CONDA_ENV={shlex.quote(env_name)}",
                        f"ALLOW_SYNTHETIC_WATERMASK={allow_synthetic_watermask}",
                        "bash",
                        shlex.quote(runner_script_wsl),
                        shlex.quote(scratch_root_wsl),
                        shlex.quote(run_file_name),
                    ]
                ),
            )
            run_step_results.append(step_result)

        required_dirs = {
            "geom_reference": stack_work_dir / "geom_reference",
            "baselines": stack_work_dir / "baselines",
            "igrams": stack_work_dir / "Igrams",
        }
        missing_outputs = [name for name, path in required_dirs.items() if not path.exists()]
        if missing_outputs:
            raise FileNotFoundError(
                "ISCE2 stack finished but expected outputs are missing: " + ", ".join(missing_outputs)
            )

        local_dem_report_path = Path(run.work_root_windows or "") / "inputs" / "dem" / "stack_dem_window_report.json"
        run.status = STATUS_STACK_COMPLETED
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "stack_completed",
            "stack_execution": {
                "prepare_local_dem": prepare_dem_result,
                "generate_runfiles": stack_generate_result,
                "run_steps": run_step_results,
                "stack_work_dir_windows": str(stack_work_dir),
                "stack_work_dir_wsl": self._stack_work_dir_wsl(run, report),
                "run_files_dir_windows": str(run_files_dir),
                "run_sequence": list(STACK_RUN_FILE_SEQUENCE),
                "local_dem_report_path_windows": str(local_dem_report_path) if local_dem_report_path.exists() else None,
                "local_dem_report_path_wsl": (
                    _windows_path_to_wsl_mount(str(local_dem_report_path))
                    if local_dem_report_path.exists()
                    else None
                ),
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "isce2_stack_complete",
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "stack_work_dir": str(stack_work_dir),
            "run_sequence": list(STACK_RUN_FILE_SEQUENCE),
        }

    async def run_mintpy_sbas(
        self,
        run_id: str,
        *,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        self._prepare_workdirs(run)

        report = self._generated_stack_manifest_payload(run)
        cfg_path = self._write_mintpy_config(run, report=report)
        cfg_path_wsl = _windows_path_to_wsl_mount(str(cfg_path))
        mintpy_work_dir = self._mintpy_work_dir(run, report)
        mintpy_work_dir.mkdir(parents=True, exist_ok=True)
        mintpy_work_dir_wsl = _windows_path_to_wsl_mount(str(mintpy_work_dir))
        mintpy_script_wsl = _windows_path_to_wsl_mount(settings.TIMESERIES_MINTPY_SBAS_SCRIPT)
        if not cfg_path_wsl:
            raise ValueError("MintPy config path could not be resolved to a WSL path.")
        if not mintpy_work_dir_wsl:
            raise ValueError("MintPy work directory could not be resolved to a WSL path.")
        if not mintpy_script_wsl:
            raise ValueError("TIMESERIES_MINTPY_SBAS_SCRIPT could not be resolved to a WSL path.")

        run.status = STATUS_MINTPY_RUNNING
        run.error_message = None
        await db.commit()
        await db.refresh(run)
        mintpy_result = await self._run_wsl_step(
            run,
            step_name="mintpy_sbas",
            command=" ".join(
                [
                    "env",
                    f"MINTPY_ENV={shlex.quote(self._effective_env_name(run))}",
                    "bash",
                    shlex.quote(mintpy_script_wsl),
                    shlex.quote(cfg_path_wsl),
                    shlex.quote(mintpy_work_dir_wsl),
                ]
            ),
            timeout_seconds=max(
                int(settings.TIMESERIES_WSL_STEP_TIMEOUT_SECONDS),
                int(settings.TIMESERIES_WSL_STEP_TIMEOUT_SECONDS) * 2,
            ),
        )

        expected_outputs = [
            mintpy_work_dir / "timeseries.h5",
            mintpy_work_dir / "velocity.h5",
            mintpy_work_dir / "temporalCoherence.h5",
            mintpy_work_dir / "maskTempCoh.h5",
            mintpy_work_dir / "maskAllValid.h5",
        ]
        missing_outputs = [str(path) for path in expected_outputs if not path.exists()]
        if missing_outputs:
            raise FileNotFoundError(
                "MintPy SBAS finished but required outputs are missing: " + "; ".join(missing_outputs)
            )

        run.status = STATUS_MINTPY_COMPLETED
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "mintpy_completed",
            "mintpy": {
                "config_path_windows": str(cfg_path),
                "config_path_wsl": cfg_path_wsl,
                "work_dir_windows": str(mintpy_work_dir),
                "work_dir_wsl": mintpy_work_dir_wsl,
                "outputs": [str(path) for path in expected_outputs],
                "runner": mintpy_result,
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "mintpy_complete",
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "mintpy_work_dir": str(mintpy_work_dir),
            "config_path": str(cfg_path),
        }

    async def export_publish_bundle(
        self,
        run_id: str,
        *,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        self._prepare_workdirs(run)

        report = self._generated_stack_manifest_payload(run)
        mintpy_cfg_path = self._mintpy_cfg_path(run)
        mintpy_work_dir = self._mintpy_work_dir(run, report)
        publish_dir = Path(
            run.publish_dir_windows
            or self._derive_paths(run.run_id)["publish_dir_windows"]
        )
        publish_dir.mkdir(parents=True, exist_ok=True)
        mintpy_work_dir_wsl = _windows_path_to_wsl_mount(str(mintpy_work_dir))
        publish_dir_wsl = _windows_path_to_wsl_mount(str(publish_dir))
        export_script_wsl = _windows_path_to_wsl_mount(settings.TIMESERIES_EXPORT_PUBLISH_SCRIPT)
        if not mintpy_work_dir_wsl:
            raise ValueError("MintPy work directory could not be resolved to a WSL path.")
        if not publish_dir_wsl:
            raise ValueError("Publish directory could not be resolved to a WSL path.")
        if not export_script_wsl:
            raise ValueError("TIMESERIES_EXPORT_PUBLISH_SCRIPT could not be resolved to a WSL path.")

        run.status = STATUS_EXPORTING
        run.error_message = None
        await db.commit()
        await db.refresh(run)
        command_parts = [
            "env",
            f"MINTPY_ENV={shlex.quote(self._effective_env_name(run))}",
            "bash",
            shlex.quote(export_script_wsl),
            shlex.quote(mintpy_work_dir_wsl),
            shlex.quote(publish_dir_wsl),
        ]
        group_key = str(report.get("group_key") or "").strip()
        if group_key:
            command_parts.append(shlex.quote(group_key))
        export_result = await self._run_wsl_step(
            run,
            step_name="export_publish_bundle",
            command=" ".join(command_parts),
            timeout_seconds=max(
                int(settings.TIMESERIES_WSL_STEP_TIMEOUT_SECONDS),
                int(settings.TIMESERIES_WSL_STEP_TIMEOUT_SECONDS) * 2,
            ),
        )

        publish_manifest_path = self._publish_manifest_path(run)
        publish_manifest = self._augment_publish_manifest(
            run,
            manifest_path=publish_manifest_path,
            report=report,
            mintpy_cfg_path=mintpy_cfg_path,
            mintpy_work_dir=mintpy_work_dir,
        )
        publish_validation = self._validate_publish_bundle(publish_manifest_path)
        if not publish_validation.get("ok"):
            problems = []
            for key in (
                "missing_roles",
                "missing_files",
                "zero_size_files",
                "missing_extra_files",
                "issues",
            ):
                values = [str(item) for item in (publish_validation.get(key) or []) if str(item)]
                if values:
                    problems.append(f"{key}={','.join(values)}")
            raise ValueError("Publish bundle validation failed: " + "; ".join(problems or ["unknown"]))

        run.status = STATUS_EXPORTED
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "exported",
            "publish": {
                "publish_dir_windows": str(publish_dir),
                "publish_dir_wsl": publish_dir_wsl,
                "manifest_path_windows": str(publish_manifest_path),
                "manifest_path_wsl": _windows_path_to_wsl_mount(str(publish_manifest_path)),
                "stack_key": publish_manifest.get("stack_key"),
                "group_key": publish_manifest.get("group_key"),
                "export_runner": export_result,
                "validation": publish_validation,
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "publish_bundle_complete",
            "publish_validation": publish_validation,
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "publish_dir": str(publish_dir),
            "manifest_path": str(publish_manifest_path),
        }

    async def register_psinsar_product(
        self,
        run_id: str,
        *,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        publish_manifest_path = self._publish_manifest_path(run)
        if not publish_manifest_path.exists():
            raise FileNotFoundError(f"Publish manifest not found: {publish_manifest_path}")

        run.status = STATUS_REGISTERING
        run.error_message = None
        await db.commit()
        await db.refresh(run)
        publish_validation = self._validate_publish_bundle(publish_manifest_path)
        if not publish_validation.get("ok"):
            raise ValueError("Managed publish bundle is not valid enough for catalog registration.")
        registration = await psinsar_catalog_service.register_manifest(
            db,
            manifest_path=str(publish_manifest_path),
        )
        run.status = STATUS_PUBLISHED
        run.ended_at = _utcnow()
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "published",
            "publish": {
                **((run.summary_json or {}).get("publish") or {}),
                "manifest_path_windows": str(publish_manifest_path),
                "manifest_path_wsl": _windows_path_to_wsl_mount(str(publish_manifest_path)),
                "product_id": registration.get("product_id"),
                "product_db_id": registration.get("product_db_id"),
                "product_status": registration.get("status"),
                "health_status": registration.get("health_status"),
                "validation": publish_validation,
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "catalog_registered",
            "publish_validation": publish_validation,
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "product_id": registration.get("product_id"),
            "product_db_id": registration.get("product_db_id"),
        }

    async def retry_step(
        self,
        run_id: str,
        *,
        step_id: str,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        workflow_run_id = str(run.workflow_run_id or "").strip()
        target_step_id = str(step_id or "").strip()
        if not workflow_run_id:
            raise ValueError(f"Timeseries run has no workflow_run_id: {run_id}")
        if not target_step_id:
            raise ValueError("step_id is required.")

        retry_result = await workflow_service.retry_step(
            workflow_run_id,
            target_step_id,
            db=db,
        )

        run.status = self._retry_status_for_step(target_step_id)
        run.error_message = None
        run.ended_at = None
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "retry_queued",
            "retry": {
                "step_id": target_step_id,
                "queued_at": _utcnow().replace(microsecond=0).isoformat() + "Z",
                "reset_steps": retry_result.get("reset_steps") or [],
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "phase": "retry_queued",
        }
        if run.task_id:
            await task_service.update_task(
                run.task_id,
                status="RUNNING",
                progress=self._retry_progress_for_step(target_step_id),
                message=f"Retry queued from workflow step: {target_step_id}",
                db=db,
            )

        await db.commit()
        await db.refresh(run)
        return {
            "run_id": run.run_id,
            "workflow_run_id": workflow_run_id,
            "step_id": target_step_id,
            "status": run.status,
            "reset_steps": retry_result.get("reset_steps") or [],
        }

    async def mark_run_failed(self, run_id: str, error: str, *, db: AsyncSession) -> Dict[str, Any]:
        run = await self._load_run(run_id, db)
        run.status = STATUS_FAILED
        run.error_message = _tail_text(error or "Unknown SBAS run failure.")
        run.ended_at = _utcnow()
        run.summary_json = {
            **(run.summary_json or {}),
            "phase": "failed",
            "last_error": run.error_message,
        }
        await db.commit()
        await db.refresh(run)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "error_message": run.error_message,
        }

    async def list_runs(
        self,
        db: AsyncSession,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        safe_limit = min(200, max(1, int(limit or 50)))
        safe_offset = max(0, int(offset or 0))

        total_result = await db.execute(select(func.count()).select_from(PsTimeseriesRunORM))
        total = int(total_result.scalar() or 0)

        runs_result = await db.execute(
            select(PsTimeseriesRunORM)
            .order_by(PsTimeseriesRunORM.created_at.desc(), PsTimeseriesRunORM.id.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        items = runs_result.scalars().all()
        return {
            "items": [self._serialize_run(item) for item in items],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(items) < total,
        }

    async def get_run_detail(
        self,
        db: AsyncSession,
        *,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            select(PsTimeseriesRunORM).where(PsTimeseriesRunORM.run_id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return None

        workflow_payload: Optional[Dict[str, Any]] = None
        if str(run.workflow_run_id or "").strip():
            steps_result = await db.execute(
                select(WorkflowStepORM)
                .where(WorkflowStepORM.run_id == run.workflow_run_id)
                .order_by(WorkflowStepORM.id.asc())
            )
            steps = steps_result.scalars().all()
            workflow_payload = {
                "run_id": run.workflow_run_id,
                "steps": [self._serialize_workflow_step(item) for item in steps],
            }

        product_result = await db.execute(
            select(ResultProductORM)
            .where(
                ResultProductORM.catalog_name == CATALOG_NAME_PSINSAR,
                ResultProductORM.run_key == run.run_id,
            )
            .order_by(ResultProductORM.published_at.desc().nullslast(), ResultProductORM.id.desc())
        )
        product = product_result.scalars().first()
        product_payload: Optional[Dict[str, Any]] = None
        if product is not None:
            summary = product.summary_json or {}
            product_payload = {
                "id": product.id,
                "product_id": product.product_id,
                "display_name": product.display_name,
                "run_key": product.run_key,
                "plan_id": summary.get("plan_id"),
                "plan_strategy": summary.get("plan_strategy"),
                "package_schema": product.package_schema,
                "processor_code": product.processor_code,
                "runtime_id": product.runtime_id,
                "status": product.status,
                "health_status": product.health_status,
                "publish_dir": product.publish_dir,
                "manifest_path": product.manifest_path,
                "native_output_dir": product.native_output_dir,
                "preview_path": product.preview_path,
                "primary_asset_path": product.primary_asset_path,
                "reference_date": summary.get("reference_date"),
                "stack_dates": summary.get("stack_dates") or [],
                "stack_size": summary.get("stack_size"),
                "published_at": product.published_at,
                "registered_at": product.registered_at,
            }

        return {
            "run": self._serialize_run(run),
            "workflow": workflow_payload,
            "product": product_payload,
        }

    async def get_prepared_stack_summary(
        self,
        db: AsyncSession,
        *,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            select(PsTimeseriesRunORM).where(PsTimeseriesRunORM.run_id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return None

        selected_manifest_path = self._selected_manifest_path(run)
        selected_edges_path = self._selected_network_edges_path(run)
        processor_manifest_path = self._sarscape_processor_manifest_path(run)
        summary_json = run.summary_json if isinstance(run.summary_json, dict) else {}
        quality_json = run.quality_summary_json if isinstance(run.quality_summary_json, dict) else {}
        sarscape_summary = summary_json.get("sarscape_sbas") if isinstance(summary_json.get("sarscape_sbas"), dict) else {}
        sarscape_quality = quality_json.get("sarscape_sbas") if isinstance(quality_json.get("sarscape_sbas"), dict) else {}

        payload: Dict[str, Any] = {
            "schema": "insar.prepared-sbas-stack-summary/v1",
            "run_id": run.run_id,
            "status": run.status,
            "processor_code": run.processor_code,
            "engine_code": run.engine_code,
            "manifest_path_windows": str(selected_manifest_path),
            "manifest_path_wsl": _windows_path_to_wsl_mount(str(selected_manifest_path)),
            "manifest_exists": selected_manifest_path.is_file(),
            "selected_network_edges_path_windows": str(selected_edges_path),
            "selected_network_edges_path_wsl": _windows_path_to_wsl_mount(str(selected_edges_path)),
            "selected_network_edges_exists": selected_edges_path.is_file(),
            "processor_manifest_path_windows": str(processor_manifest_path),
            "processor_manifest_path_wsl": _windows_path_to_wsl_mount(str(processor_manifest_path)),
            "processor_manifest_exists": processor_manifest_path.is_file(),
            "prepared": False,
            "ready_for_execution": False,
            "blockers": [],
            "warnings": [],
            "state": "not_prepared",
        }

        stack_manifest: Dict[str, Any] = {}
        if selected_manifest_path.is_file():
            try:
                stack_manifest = _read_json(selected_manifest_path)
            except Exception as exc:
                payload.update(
                    {
                        "state": "manifest_unreadable",
                        "blockers": [f"Prepared stack manifest cannot be read: {exc}"],
                    }
                )
                return payload

            validation = self._build_prepared_stack_validation(
                stack_manifest,
                manifest_path=selected_manifest_path,
                expected_run_id=run.run_id,
                expected_processor_code=str(run.processor_code or "").strip() or None,
                require_network_edges=str(run.processor_code or "").strip() == "sarscape_sbas",
                require_dem=str(run.processor_code or "").strip() == "sarscape_sbas",
                dem_path=run.dem_path_windows,
            )
            artifacts = stack_manifest.get("artifacts") if isinstance(stack_manifest.get("artifacts"), dict) else {}
            production_contract = (
                stack_manifest.get("production_contract")
                if isinstance(stack_manifest.get("production_contract"), dict)
                else {}
            )
            candidate_pool_source = (
                stack_manifest.get("candidate_pool_source")
                if isinstance(stack_manifest.get("candidate_pool_source"), dict)
                else {}
            )
            payload.update(
                {
                    "prepared": validation.get("ok"),
                    "state": "prepared" if validation.get("ok") else "prepared_invalid",
                    "prepared_stack_schema": stack_manifest.get("prepared_stack_schema"),
                    "manifest_role": stack_manifest.get("manifest_role"),
                    "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
                    "prepared_at_utc": stack_manifest.get("prepared_at_utc"),
                    "manifest_checksum": stack_manifest.get("manifest_checksum"),
                    "source_plan_id": stack_manifest.get("source_plan_id") or stack_manifest.get("plan_id"),
                    "source_batch_id": stack_manifest.get("source_batch_id") or stack_manifest.get("batch_id"),
                    "plan_strategy": stack_manifest.get("plan_strategy"),
                    "reference_date": stack_manifest.get("reference_date"),
                    "scene_count": len(stack_manifest.get("scenes") or []),
                    "stack_dates": stack_manifest.get("stack_dates") or [],
                    "network_edge_count": len(stack_manifest.get("network_edges") or []),
                    "selection_params": stack_manifest.get("selection_params") or {},
                    "candidate_pool_source": candidate_pool_source,
                    "production_contract": production_contract,
                    "artifacts": artifacts,
                    "validation": validation,
                    "blockers": validation.get("blockers") or [],
                    "warnings": validation.get("warnings") or [],
                }
            )

        processor_manifest: Dict[str, Any] = {}
        if processor_manifest_path.is_file():
            try:
                processor_manifest = _read_json(processor_manifest_path)
            except Exception as exc:
                payload["processor_manifest_error"] = str(exc)
            else:
                payload["processor_manifest"] = {
                    "schema": processor_manifest.get("schema"),
                    "created_at_utc": processor_manifest.get("created_at_utc"),
                    "execution_enabled": processor_manifest.get("execution_enabled"),
                    "ready_for_pipeline_design": processor_manifest.get("ready_for_pipeline_design"),
                    "ready_for_execution": processor_manifest.get("ready_for_execution"),
                    "execution_strategy": processor_manifest.get("execution_strategy"),
                    "blockers": processor_manifest.get("blockers") or [],
                    "network_summary": processor_manifest.get("network_summary") or {},
                    "parameter_template": processor_manifest.get("parameter_template") or {},
                    "task_count": len(processor_manifest.get("task_sequence") or []),
                }
                payload["ready_for_execution"] = bool(processor_manifest.get("ready_for_execution"))
                if payload.get("prepared"):
                    payload["state"] = (
                        "ready_for_execution"
                        if processor_manifest.get("ready_for_execution")
                        else "processor_blocked"
                    )
                payload["blockers"] = processor_manifest.get("blockers") or payload.get("blockers") or []

        if sarscape_summary or sarscape_quality:
            payload["sarscape_status"] = {
                "ready_for_pipeline_design": sarscape_summary.get("ready_for_pipeline_design"),
                "ready_for_execution": sarscape_summary.get("ready_for_execution"),
                "blockers": sarscape_summary.get("blockers") or sarscape_quality.get("blockers") or [],
                "processor_manifest_path_windows": sarscape_summary.get("processor_manifest_path_windows"),
                "execution_report_path_windows": sarscape_summary.get("execution_report_path_windows"),
                "task_count": sarscape_summary.get("task_count"),
            }

        return payload


timeseries_service = TimeseriesService()
