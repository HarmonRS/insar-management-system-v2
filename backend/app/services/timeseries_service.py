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
from pathlib import Path
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
    WorkflowStepORM,
)
from .psinsar_catalog_service import psinsar_catalog_service
from .product_packaging import upgrade_timeseries_package_manifest
from .task_service import task_service
from .workflow_service import workflow_service
from .wsl_service import run_wsl_command


CATALOG_NAME_PSINSAR = "psinsar"
JOB_TYPE_TIMESERIES_PREPARE = "TIMESERIES_PREPARE"
JOB_TYPE_TIMESERIES_STACK_PREP = "TIMESERIES_STACK_PREP"
JOB_TYPE_TIMESERIES_MATERIALIZE = "TIMESERIES_MATERIALIZE"
JOB_TYPE_TIMESERIES_RUN_ISCE2_STACK = "TIMESERIES_RUN_ISCE2_STACK"
JOB_TYPE_TIMESERIES_RUN_MINTPY_SBAS = "TIMESERIES_RUN_MINTPY_SBAS"
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

    def _scene_payload(self, items: List[PsTaskItemORM]) -> List[Dict[str, Any]]:
        payload: List[Dict[str, Any]] = []
        for item in items:
            payload.append(
                {
                    "item_id": item.id,
                    "file_path": item.file_path,
                    "satellite": item.satellite,
                    "imaging_date": item.imaging_date,
                    "polarization": item.polarization,
                    "has_orbit_data": bool(item.has_orbit_data),
                    "status": item.status,
                    "remark": item.remark,
                }
            )
        return payload

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
        }
        payload = upgrade_timeseries_package_manifest(
            payload,
            run_context={
                "run_id": run.run_id,
                "run_name": run.run_name,
                "batch_id": run.batch_id,
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

    def _workflow_steps(self, *, run_id: str, task_id: str) -> List[Dict[str, Any]]:
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

        items = await self._load_batch_items(normalized_batch_id, db)
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
        paths = self._derive_paths(run_id, stack_key=stack_key)
        selected_manifest_path = Path(paths["work_root_windows"]) / "input" / "selected_stack_manifest.json"
        task_name = f"SBAS timeseries run {run_name_text}"
        task_id: Optional[str] = None

        try:
            task_id = await task_service.create_task(
                TASK_TYPE_TIMESERIES_RUN,
                task_name,
                params={
                    "run_id": run_id,
                    "batch_id": normalized_batch_id,
                    "reference_date": effective_reference_date,
                },
                db=db,
            )

            run = PsTimeseriesRunORM(
                run_id=run_id,
                batch_id=normalized_batch_id,
                product_family="timeseries",
                run_name=run_name_text,
                catalog_name=CATALOG_NAME_PSINSAR,
                stack_key=stack_key,
                mode="sbas",
                engine_code="isce2",
                processor_code="isce2_stack_mintpy",
                runtime_id=settings.ISCE2_RUNTIME_ID or None,
                env_name=settings.TIMESERIES_ENV_NAME or None,
                wsl_distro=settings.TIMESERIES_WSL_DISTRO or None,
                status=STATUS_PENDING,
                task_id=task_id,
                direction=str(batch.direction or "").strip().upper() or None,
                stack_size=len(items),
                reference_date=effective_reference_date,
                water_mask_mode=normalized_water_mask_mode,
                dem_path_windows=str(settings.TIMESERIES_DEM_PATH or "").strip() or None,
                dem_path_wsl=_windows_path_to_wsl_mount(settings.TIMESERIES_DEM_PATH),
                orbit_pool_windows=str(settings.TIMESERIES_ORBIT_POOL_ISCE2 or "").strip() or None,
                orbit_pool_wsl=_windows_path_to_wsl_mount(settings.TIMESERIES_ORBIT_POOL_ISCE2),
                work_root_windows=paths["work_root_windows"],
                work_root_wsl=paths["work_root_wsl"],
                publish_dir_windows=paths["publish_dir_windows"],
                publish_dir_wsl=paths["publish_dir_wsl"],
                manifest_path_windows=str(selected_manifest_path),
                manifest_path_wsl=_windows_path_to_wsl_mount(str(selected_manifest_path)),
                params_json={
                    "batch_id": normalized_batch_id,
                    "requested_reference_date": _normalize_date(reference_date),
                    "effective_reference_date": effective_reference_date,
                    "water_mask_mode": normalized_water_mask_mode,
                    "notes": str(notes or "").strip() or None,
                    "stack_workflow": settings.TIMESERIES_STACK_WORKFLOW,
                    "group_key": stack_preview.get("group_key"),
                    "stack_key": stack_key,
                },
                summary_json={
                    "phase": "queued",
                    "workflow": settings.TIMESERIES_STACK_WORKFLOW,
                    "group_key": stack_preview.get("group_key"),
                    "stack_key": stack_key,
                    "stack_dates": stack_dates,
                    "task_name": task_name,
                },
                input_snapshot_json={
                    "batch_id": normalized_batch_id,
                    "batch_name": batch.name,
                    "direction": batch.direction,
                    "scene_count": len(items),
                    "group_key": stack_preview.get("group_key"),
                    "stack_key": stack_key,
                    "stack_dates": stack_dates,
                    "source_root_windows": stack_preview.get("source_root_windows"),
                    "items": self._scene_payload(items),
                },
                orbit_summary_json={
                    "stage": "queued",
                    "scene_count": len(items),
                    "item_has_orbit_data_count": sum(1 for item in items if item.has_orbit_data),
                    "orbit_pool_windows": str(settings.TIMESERIES_ORBIT_POOL_ISCE2 or "").strip() or None,
                },
                quality_summary_json={
                    "water_mask_mode": normalized_water_mask_mode,
                    "synthetic_water_mask_allowed": bool(settings.TIMESERIES_ALLOW_SYNTHETIC_WATER_MASK),
                    "notes": str(notes or "").strip() or None,
                },
                created_by=created_by,
            )
            db.add(run)
            await db.flush()

            workflow_run_id = await workflow_service.create_run(
                workflow_name="psinsar_sbas_full_chain",
                steps=self._workflow_steps(run_id=run_id, task_id=task_id),
                params={
                    "run_id": run_id,
                    "batch_id": normalized_batch_id,
                    "reference_date": effective_reference_date,
                    "workflow": settings.TIMESERIES_STACK_WORKFLOW,
                },
                tags={
                    "catalog_name": CATALOG_NAME_PSINSAR,
                    "product_family": "timeseries",
                    "processor_code": "isce2_stack_mintpy",
                    "batch_id": normalized_batch_id,
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
                "reference_date": run.reference_date,
                "stack_size": run.stack_size,
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

        selected_manifest = {
            **stack_payload,
            "run_id": run.run_id,
            "batch_id": run.batch_id,
            "run_name": run.run_name,
            "task_id": run.task_id,
            "catalog_name": run.catalog_name,
            "mode": run.mode,
            "engine_code": run.engine_code,
            "processor_code": run.processor_code,
            "reference_date": effective_reference_date,
            "stack_dates": stack_dates,
            "water_mask_mode": run.water_mask_mode,
            "notes": ((run.params_json or {}).get("notes") if isinstance(run.params_json, dict) else None),
            "requested_reference_date": (
                (run.params_json or {}).get("requested_reference_date")
                if isinstance(run.params_json, dict)
                else None
            ),
            "processing_workflow": settings.TIMESERIES_STACK_WORKFLOW,
        }

        selected_manifest_path = self._selected_manifest_path(run)
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
            "workflow": settings.TIMESERIES_STACK_WORKFLOW,
            "group_key": selected_manifest.get("group_key"),
            "stack_key": selected_manifest.get("stack_key"),
            "tile_key": selected_manifest.get("tile_key"),
            "reference_date": effective_reference_date,
            "stack_dates": stack_dates,
            "scene_count": len(stack_dates),
            "selected_manifest_path_windows": str(selected_manifest_path),
            "selected_manifest_path_wsl": _windows_path_to_wsl_mount(str(selected_manifest_path)),
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "scene_count": len(stack_dates),
            "reference_date": effective_reference_date,
            "manifest_path": str(selected_manifest_path),
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
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "publish_bundle_complete",
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
            },
        }
        run.quality_summary_json = {
            **(run.quality_summary_json or {}),
            "water_mask_mode": run.water_mask_mode,
            "phase": "catalog_registered",
        }
        await db.commit()
        await db.refresh(run)

        return {
            "run_id": run.run_id,
            "status": run.status,
            "product_id": registration.get("product_id"),
            "product_db_id": registration.get("product_db_id"),
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


timeseries_service = TimeseriesService()
