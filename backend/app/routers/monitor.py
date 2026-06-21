from __future__ import annotations

import os
import shutil
from typing import Any, Iterable, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..config import read_int_env, settings, split_env_paths
from ..database import get_db
from ..models import AuthUserORM, SystemTaskORM, TaskLogORM
from ..scheduler import MONITOR_CONFIG
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from .dependencies import _require_admin

router = APIRouter()
MONITOR_ALLOWED_TARGETS = {"radar", "orbit", "dinsar", "gf3"}
MONITOR_SCAN_TASK_TYPES = {
    "SCAN_ASSET_INVENTORY",
    "AUDIT_SOURCE_ARCHIVE_INTEGRITY",
    "SCAN_DATA",
    "SCAN_DINSAR",
    "GF3_SARSCAPE_SYNC",
    "GF3_QUICKLOOK_WEBP",
}
MONITOR_TERMINAL_TASK_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
MONITOR_LOG_DEFAULT_LIMIT = read_int_env(
    "MONITOR_LOG_DEFAULT_LIMIT",
    50,
    minimum=1,
    maximum=2000,
)
MONITOR_LOG_MAX_LIMIT = read_int_env(
    "MONITOR_LOG_MAX_LIMIT",
    200,
    minimum=1,
    maximum=5000,
)
MONITOR_LOG_MAX_OFFSET = read_int_env(
    "MONITOR_LOG_MAX_OFFSET",
    500000,
    minimum=0,
    maximum=20000000,
)


def _path_kind(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith("\\\\"):
        return "unc"
    drive, _tail = os.path.splitdrive(os.path.normpath(text))
    if drive:
        return "windows"
    if text.startswith("/mnt/"):
        return "wsl_mount"
    if text.startswith("/"):
        return "posix"
    return "relative"


def _nearest_existing_path(path: str) -> str:
    candidate = os.path.normpath(str(path or "").strip())
    while candidate and not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if not parent or parent == candidate:
            break
        candidate = parent
    return candidate


def _storage_status(label: str, path: str, role: str) -> dict[str, Any]:
    text = str(path or "").strip()
    item: dict[str, Any] = {
        "label": label,
        "role": role,
        "path": text,
        "path_kind": _path_kind(text),
        "exists": bool(text and os.path.exists(text)),
        "probe_path": "",
        "total_gb": None,
        "used_gb": None,
        "free_gb": None,
        "free_ratio": None,
        "status": "missing" if text else "empty",
        "message": "",
    }
    if not text:
        item["message"] = "Path is not configured."
        return item
    if item["path_kind"] == "unc":
        item["status"] = "blocked"
        item["message"] = "UNC paths are not allowed for active local production."
        return item

    probe = _nearest_existing_path(text)
    if not probe or not os.path.exists(probe):
        item["message"] = "No existing parent path found."
        return item
    item["probe_path"] = probe
    try:
        total, used, free = shutil.disk_usage(probe)
    except OSError as exc:
        item["status"] = "error"
        item["message"] = str(exc)
        return item

    gb = 1024 ** 3
    free_ratio = float(free) / float(total or 1)
    item.update(
        {
            "total_gb": round(total / gb, 2),
            "used_gb": round(used / gb, 2),
            "free_gb": round(free / gb, 2),
            "free_ratio": round(free_ratio, 4),
            "status": "ok",
            "message": "",
        }
    )
    if not item["exists"]:
        item["status"] = "missing"
        item["message"] = f"Path does not exist; disk usage probed from {probe}."
    elif free_ratio < 0.10:
        item["status"] = "critical"
        item["message"] = "Free space is below 10%."
    elif free_ratio < 0.20:
        item["status"] = "warning"
        item["message"] = "Free space is below 20%."
    return item


def _add_storage_roots(rows: list[dict[str, str]], label: str, paths: Iterable[str], role: str) -> None:
    for path in paths:
        text = str(path or "").strip()
        if text:
            rows.append({"label": label, "path": text, "role": role})


def _storage_group_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    path = str(item.get("path") or "").strip()
    path_kind = str(item.get("path_kind") or "")
    if path_kind == "windows":
        base = str(item.get("probe_path") or path).strip()
        drive, _tail = os.path.splitdrive(os.path.normpath(base))
        if drive:
            drive = drive.upper()
            volume_path = f"{drive}\\"
            return f"windows:{drive}", volume_path, f"本机磁盘 {volume_path}"
    if path_kind == "unc":
        key = os.path.normcase(os.path.normpath(path))
        return f"unc:{key}", path, "UNC path"
    if path_kind in {"posix", "wsl_mount"}:
        base = str(item.get("probe_path") or path or "/").strip()
        root = "/mnt/" + base.split("/")[2] if base.startswith("/mnt/") and len(base.split("/")) > 2 else "/"
        return f"{path_kind}:{root}", root, root
    key = os.path.normcase(os.path.normpath(path))
    return f"path:{key}", path, str(item.get("label") or item.get("role") or path or "Storage")


def _capacity_status(free_ratio: Any) -> str:
    if free_ratio is None:
        return "missing"
    try:
        ratio = float(free_ratio)
    except (TypeError, ValueError):
        return "missing"
    if ratio < 0.10:
        return "critical"
    if ratio < 0.20:
        return "warning"
    return "ok"


def _group_storage_statuses(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for item in items:
        group_key, volume_path, volume_label = _storage_group_identity(item)
        group = groups.get(group_key)
        if group is None:
            group = {
                "label": volume_label,
                "role": "storage_volume",
                "path": volume_path,
                "path_kind": item.get("path_kind"),
                "exists": False,
                "probe_path": item.get("probe_path") or "",
                "total_gb": None,
                "used_gb": None,
                "free_gb": None,
                "free_ratio": None,
                "status": "missing",
                "message": "",
                "paths": [],
                "configured_path_count": 0,
                "existing_path_count": 0,
                "missing_path_count": 0,
                "blocked_path_count": 0,
                "error_path_count": 0,
            }
            groups[group_key] = group
            order.append(group_key)

        group["configured_path_count"] += 1
        if item.get("exists"):
            group["existing_path_count"] += 1
        if item.get("status") == "missing":
            group["missing_path_count"] += 1
        elif item.get("status") == "blocked":
            group["blocked_path_count"] += 1
        elif item.get("status") == "error":
            group["error_path_count"] += 1

        group["paths"].append(
            {
                "label": item.get("label"),
                "role": item.get("role"),
                "path": item.get("path"),
                "exists": item.get("exists"),
                "status": item.get("status"),
                "message": item.get("message"),
            }
        )

        if item.get("total_gb") is not None:
            group["exists"] = True
            group["probe_path"] = item.get("probe_path") or group["probe_path"]
            group["total_gb"] = item.get("total_gb")
            group["used_gb"] = item.get("used_gb")
            group["free_gb"] = item.get("free_gb")
            group["free_ratio"] = item.get("free_ratio")

    result: list[dict[str, Any]] = []
    for key in order:
        group = groups[key]
        status = _capacity_status(group.get("free_ratio"))
        if group["blocked_path_count"]:
            status = "blocked"
        elif group["error_path_count"]:
            status = "error"
        elif group["missing_path_count"] and status == "ok":
            status = "partial"
        group["status"] = status

        messages = [f"{group['configured_path_count']} 个配置路径"]
        if group["missing_path_count"]:
            messages.append(f"{group['missing_path_count']} 个路径缺失")
        if group["blocked_path_count"]:
            messages.append(f"{group['blocked_path_count']} 个 UNC 被禁用")
        if group["error_path_count"]:
            messages.append(f"{group['error_path_count']} 个探测失败")
        if status == "critical":
            messages.append("剩余空间低于 10%")
        elif status == "warning" and not group["missing_path_count"]:
            messages.append("剩余空间低于 20%")
        group["message"] = "；".join(messages) + "。"
        result.append(group)
    return result


def _collect_storage_roots() -> list[dict[str, Any]]:
    rows: list[dict[str, str]] = []
    _add_storage_roots(rows, "Task_Pool", [settings.TASK_POOL_ROOT], "task_pool")
    _add_storage_roots(rows, "D-InSAR Task_Pool", [settings.DINSAR_TASK_POOL_ROOT], "dinsar_task_pool")
    _add_storage_roots(rows, "SBAS Task_Pool", [settings.SBAS_TASK_POOL_ROOT], "sbas_task_pool")
    _add_storage_roots(rows, "Data distribution root", [settings.DATA_DISTRIBUTION_ROOT], "data_distribution")
    source_paths = split_env_paths(settings.SOURCE_PRODUCT_DIRS)
    source_path_keys = {os.path.normcase(os.path.normpath(path)) for path in source_paths}
    s1_storage_paths = [
        path
        for path in split_env_paths(settings.SENTINEL1_STORAGE_DIRS)
        if os.path.normcase(os.path.normpath(path)) not in source_path_keys
    ]
    _add_storage_roots(rows, "LT/S1 local source pools", source_paths, "source_local")
    _add_storage_roots(rows, "Sentinel-1 local source pool", s1_storage_paths, "source_local")
    _add_storage_roots(rows, "Orbit source pool", split_env_paths(settings.ORBIT_SOURCE_DIRS), "orbit_source")
    _add_storage_roots(rows, "LT-1 radar scan pool", split_env_paths(settings.MONITOR_RADAR_DIRS), "lt1_storage")
    _add_storage_roots(rows, "D-InSAR product root", [settings.DINSAR_PRODUCT_DIR], "dinsar_product")
    _add_storage_roots(rows, "GF3 native _geo", split_env_paths(settings.GF3_SARSCAPE_NATIVE_DIRS), "gf3_native")
    _add_storage_roots(rows, "GF3 standard/index", split_env_paths(settings.GF3_STORAGE_DIRS), "gf3_storage")
    _add_storage_roots(rows, "GF3 task/runtime pool", [settings.GF3_TASK_POOL_ROOT], "gf3_task_pool")
    _add_storage_roots(rows, "GF3 SARscape runtime", [settings.GF3_SARSCAPE_RUNTIME_DIR], "gf3_runtime")
    _add_storage_roots(rows, "Result publish root", [settings.RESULT_PUBLISH_ROOT], "result_publish")
    _add_storage_roots(rows, "SBAS work root", [settings.GAMMA_SBAS_WORK_ROOT], "sbas_work")
    _add_storage_roots(rows, "SBAS product root", [settings.GAMMA_SBAS_PRODUCT_ROOT], "sbas_product")
    _add_storage_roots(rows, "SAR analysis ready", [settings.SAR_ANALYSIS_READY_ROOT], "analysis_ready")
    _add_storage_roots(rows, "SAR analysis work", [settings.SAR_ANALYSIS_WORK_ROOT], "analysis_work")

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = os.path.normcase(os.path.normpath(row["path"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(_storage_status(row["label"], row["path"], row["role"]))
    return _group_storage_statuses(result)


class MonitorConfig(BaseModel):
    radar_dirs: List[str] = []
    orbit_dir: Optional[str] = None
    orbit_source_dirs: List[str] = []
    orbit_production_txt_pool: Optional[str] = None
    dinsar_dirs: List[str] = []
    dinsar_product_dir: Optional[str] = None
    sbas_product_root: Optional[str] = None
    task_pool_root: Optional[str] = None
    dinsar_task_pool_root: Optional[str] = None
    sbas_task_pool_root: Optional[str] = None
    gf3_task_pool_root: Optional[str] = None
    data_distribution_root: Optional[str] = None
    gf3_archive_source_dirs: List[str] = []
    gf3_source_dirs: List[str] = []
    gf3_legacy_gdal_enabled: bool = False
    gf3_sarscape_native_dirs: List[str] = []
    gf3_storage_dirs: List[str] = []
    gf3_sarscape_runtime_dir: Optional[str] = None
    gf3_sarscape_wrapper_exe: Optional[str] = None
    gf3_sarscape_idlrt_path: Optional[str] = None
    gf3_sarscape_dem_path: Optional[str] = None
    gf3_sarscape_polarizations: Optional[str] = None
    gf3_sarscape_auto_standardize: bool = True
    gf3_sarscape_clean_after_success: bool = True
    storage_roots: List[dict[str, Any]] = []
    # Manual-only: config is read from .env


class GF3UnpackConfig(BaseModel):
    source_dirs: List[str] = []
    target_dirs: List[str] = []
    archive_exts: List[str] = []
    delete_archive: bool = False


class GF3UnpackRunRequest(BaseModel):
    max_files_per_run: Optional[int] = Field(default=None, ge=0)


class GF3SarscapeSyncRequest(BaseModel):
    force: bool = False
    register: bool = True
    quicklook_only: bool = False
    native_dirs: List[str] = []


class GF3QuicklookWebpRequest(BaseModel):
    force: bool = False
    max_records: Optional[int] = Field(default=None, ge=0)


class GF3SarscapeProduceRequest(BaseModel):
    max_archives_per_run: Optional[int] = Field(default=None, ge=0)
    selected_dates: List[str] = []
    auto_standardize: Optional[bool] = None
    clean_after_success: Optional[bool] = None
    force_standardize: bool = False
    register: bool = True
    cleanup_dry_run: bool = False


class GF3SarscapeCleanRequest(BaseModel):
    dry_run: bool = False
    require_standardized: bool = True
    max_scenes: Optional[int] = Field(default=None, ge=0)


@router.post("/monitor/config")
async def update_monitor_config(config: MonitorConfig):
    """
    鏇存柊鏁版嵁鐩戞帶閰嶇疆锛堥浄杈俱€佺簿杞ㄥ拰Dinsar缁撴灉锛夈€?
    """
    raise HTTPException(
        status_code=403,
        detail="Monitor config is read-only. Edit .env and restart the backend."
    )


@router.post("/monitor/run-now")
async def run_monitor_now(target: Optional[str] = None, background_tasks: BackgroundTasks = None, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    鎵嬪姩瑙﹀彂涓€娆＄洃鎺т换鍔★紙鎵弿鎵€鏈夐厤缃殑鐩綍锛夈€?
    target: 'radar', 'orbit', 'dinsar' 鎴?None (鍏ㄩ儴)
    """
    normalized_target = (target or "").strip().lower() or None
    if normalized_target and normalized_target not in MONITOR_ALLOWED_TARGETS:
        allowed = ", ".join(sorted(MONITOR_ALLOWED_TARGETS))
        raise HTTPException(status_code=400, detail=f"Invalid target: {target}. Allowed: {allowed}.")

    radar_dirs = MONITOR_CONFIG.get("radar_dirs") or []
    orbit_dir = MONITOR_CONFIG.get("orbit_dir") or ""
    dinsar_dirs = MONITOR_CONFIG.get("dinsar_dirs") or []
    gf3_storage_dirs = MONITOR_CONFIG.get("gf3_storage_dirs") or []

    if normalized_target == "radar" and not radar_dirs:
        raise HTTPException(status_code=400, detail="MONITOR_RADAR_DIRS is not configured.")
    if normalized_target == "orbit" and not orbit_dir:
        raise HTTPException(status_code=400, detail="MONITOR_ORBIT_DIR is not configured.")
    if normalized_target == "dinsar" and not dinsar_dirs:
        raise HTTPException(status_code=400, detail="MONITOR_DINSAR_DIRS is not configured.")
    if normalized_target == "gf3" and not gf3_storage_dirs:
        raise HTTPException(status_code=400, detail="GF3_STORAGE_DIRS is not configured.")
    if normalized_target is None and not (radar_dirs or orbit_dir or dinsar_dirs):
        raise HTTPException(status_code=400, detail="Monitor paths are not configured in .env.")

    task_type = "SCAN_DATA" if normalized_target in ["radar", "orbit", "gf3", None] else "SCAN_DINSAR"
    task_name = f"Manual scan ({normalized_target or 'all'})"

    try:
        task_id = await task_service.create_task(task_type, task_name)
        if task_type == "SCAN_DINSAR":
            payload = {"dirs": dinsar_dirs}
            job_type = "SCAN_DINSAR"
        elif normalized_target == "gf3":
            # GF3 scan uses gf3_storage_dirs as radar_dirs.
            payload = {
                "radar_dirs": gf3_storage_dirs,
                "target": "radar",
            }
            job_type = "SCAN_DATA"
        else:
            payload = {
                "use_monitor_config": True,
                "target": normalized_target,
            }
            job_type = "SCAN_DATA"
        await job_queue_service.create_job(job_type, payload=payload, task_id=task_id)

        return {
            "message": f"Manual scan queued for {normalized_target or 'all'}.",
            "task_id": task_id
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/monitor/gf3-process")
async def run_gf3_batch_process(admin_user: AuthUserORM = Depends(_require_admin)):
    """
    Batch GF3 legacy L1A to L2 processing.
    """
    if not settings.GF3_LEGACY_GDAL_ENABLED:
        raise HTTPException(
            status_code=409,
            detail=(
                "Legacy GF3 Python/GDAL preprocessing is disabled. "
                "Use GF3 native _geo result registration or set GF3_LEGACY_GDAL_ENABLED=true explicitly."
            ),
        )
    gf3_source_dirs = MONITOR_CONFIG.get("gf3_source_dirs") or []
    if not gf3_source_dirs:
        raise HTTPException(status_code=400, detail="GF3_SOURCE_DIRS is not configured.")

    task_type = "GF3_BATCH_PROCESS"
    task_name = "GF3 legacy batch process"

    try:
        task_id = await task_service.create_task(task_type, task_name)
        await job_queue_service.create_job(
            "GF3_BATCH_PROCESS",
            payload={"source_dirs": gf3_source_dirs},
            task_id=task_id,
        )
        return {
            "message": "GF3 batch process task submitted",
            "task_id": task_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/monitor/gf3-sarscape-sync", status_code=202)
async def run_gf3_sarscape_sync(
    request_data: GF3SarscapeSyncRequest | None = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    Scan GF3 SARscape native _geo outputs.
    """
    options = request_data or GF3SarscapeSyncRequest()
    gf3_native_dirs = options.native_dirs or MONITOR_CONFIG.get("gf3_sarscape_native_dirs") or []
    gf3_storage_dirs = MONITOR_CONFIG.get("gf3_storage_dirs") or []
    if not gf3_native_dirs:
        raise HTTPException(status_code=400, detail="GF3_SARSCAPE_NATIVE_DIRS is not configured.")
    if not gf3_storage_dirs:
        raise HTTPException(status_code=400, detail="GF3_STORAGE_DIRS is not configured.")

    task_type = "GF3_SARSCAPE_SYNC"
    task_name = "GF3 SARscape native result inventory" if options.quicklook_only else "GF3 SARscape native standardize"
    payload = {
        "native_dirs": gf3_native_dirs,
        "storage_root": gf3_storage_dirs[0],
        "force": bool(options.force),
        "register": bool(options.register),
        "quicklook_only": bool(options.quicklook_only),
    }

    try:
        task_id = await task_service.create_task(task_type, task_name, params=payload)
        await job_queue_service.create_job(task_type, payload=payload, task_id=task_id)
        return {
            "message": (
                "GF3 SARscape native result inventory task submitted"
                if options.quicklook_only
                else "GF3 SARscape native standardize task submitted"
            ),
            "task_id": task_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/monitor/gf3-quicklook-webp", status_code=202)
async def run_gf3_quicklook_webp(
    request_data: GF3QuicklookWebpRequest | None = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    Generate local WebP cache files from registered GF3 SARscape native _geo records.
    """
    options = request_data or GF3QuicklookWebpRequest()
    task_type = "GF3_QUICKLOOK_WEBP"
    task_name = "GF3 native _geo WebP cache"
    gf3_native_dirs = MONITOR_CONFIG.get("gf3_sarscape_native_dirs") or []
    if not gf3_native_dirs:
        raise HTTPException(status_code=400, detail="GF3_SARSCAPE_NATIVE_DIRS is not configured.")
    payload = {
        "force": bool(options.force),
        "max_records": int(options.max_records or 0),
        "native_dirs": gf3_native_dirs,
    }

    try:
        task_id = await task_service.create_task(task_type, task_name, params=payload)
        await job_queue_service.create_job(task_type, payload=payload, task_id=task_id)
        return {
            "message": "GF3 native _geo WebP cache task submitted",
            "task_id": task_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))



@router.post("/monitor/gf3-sarscape-produce", status_code=202)
async def run_gf3_sarscape_produce(
    request_data: GF3SarscapeProduceRequest | None = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    GF3 SARscape production is disabled on this management machine.
    """
    raise HTTPException(
        status_code=409,
        detail=(
            "GF3 SARscape production is disabled on this management machine. "
            "Run GF3 production on the SARscape host, copy completed _geo results to local "
            "GF3_SARSCAPE_NATIVE_DIRS, then use GF3 native result registration and GF3 _geo WebP generation."
        ),
    )


@router.get("/monitor/gf3-sarscape-dates")
async def list_gf3_sarscape_dates(admin_user: AuthUserORM = Depends(_require_admin)):
    """
    GF3 SARscape production date selection is disabled on this management machine.
    """
    raise HTTPException(
        status_code=409,
        detail="GF3 SARscape production date selection is disabled. Register local _geo native results instead.",
    )


@router.post("/monitor/gf3-sarscape-clean", status_code=202)
async def run_gf3_sarscape_clean(
    request_data: GF3SarscapeCleanRequest | None = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    Clean SARscape intermediate files from GF3 native pool after standard GeoTIFFs exist.
    """
    gf3_native_dirs = MONITOR_CONFIG.get("gf3_sarscape_native_dirs") or []
    gf3_storage_dirs = MONITOR_CONFIG.get("gf3_storage_dirs") or []
    if not gf3_native_dirs:
        raise HTTPException(status_code=400, detail="GF3_SARSCAPE_NATIVE_DIRS is not configured.")
    if not gf3_storage_dirs:
        raise HTTPException(status_code=400, detail="GF3_STORAGE_DIRS is not configured.")

    options = request_data or GF3SarscapeCleanRequest()
    task_type = "GF3_SARSCAPE_CLEAN"
    task_name = "GF3 SARscape native cleanup"
    payload = {
        "native_dirs": gf3_native_dirs,
        "storage_root": gf3_storage_dirs[0],
        "dry_run": bool(options.dry_run),
        "require_standardized": bool(options.require_standardized),
        "max_scenes": int(options.max_scenes or 0),
    }

    try:
        task_id = await task_service.create_task(task_type, task_name, params=payload)
        await job_queue_service.create_job(task_type, payload=payload, task_id=task_id)
        return {
            "message": "GF3 SARscape cleanup task submitted",
            "task_id": task_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/monitor/gf3-unpack/config")
async def get_gf3_unpack_config(admin_user: AuthUserORM = Depends(_require_admin)):
    return GF3UnpackConfig(
        source_dirs=MONITOR_CONFIG.get("gf3_archive_source_dirs") or [],
        target_dirs=MONITOR_CONFIG.get("gf3_source_dirs") or [],
        archive_exts=split_env_paths(settings.GF3_ARCHIVE_EXTS),
        delete_archive=bool(settings.GF3_UNPACK_DELETE_ARCHIVE),
    )


@router.post("/monitor/gf3-unpack", status_code=202)
async def run_gf3_unpack(
    request_data: GF3UnpackRunRequest | None = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    Unpack GF3 archives into GF3_SOURCE_DIRS for the legacy pipeline.
    """
    if not settings.GF3_LEGACY_GDAL_ENABLED:
        raise HTTPException(
            status_code=409,
            detail=(
                "Legacy GF3 archive unpack is disabled. "
                "Use GF3 native _geo result registration or set GF3_LEGACY_GDAL_ENABLED=true explicitly."
            ),
        )
    gf3_archive_source_dirs = MONITOR_CONFIG.get("gf3_archive_source_dirs") or []
    gf3_source_dirs = MONITOR_CONFIG.get("gf3_source_dirs") or []
    if not gf3_archive_source_dirs:
        raise HTTPException(status_code=400, detail="GF3_ARCHIVE_SOURCE_DIRS is not configured.")
    if not gf3_source_dirs:
        raise HTTPException(status_code=400, detail="GF3_SOURCE_DIRS is not configured.")

    max_files = None
    if request_data is not None and request_data.max_files_per_run is not None:
        max_files = max(0, int(request_data.max_files_per_run))

    task_type = "GF3_UNPACK"
    task_name = "GF3 archive unpack"
    payload = {
        "source_dirs": gf3_archive_source_dirs,
        "target_dirs": gf3_source_dirs,
        "archive_exts": split_env_paths(settings.GF3_ARCHIVE_EXTS),
    }
    if max_files is not None:
        payload["max_files_per_run"] = max_files

    try:
        task_id = await task_service.create_task(task_type, task_name, params=payload)
        await job_queue_service.create_job(task_type, payload=payload, task_id=task_id)
        return {
            "message": "GF3 unpack task submitted",
            "task_id": task_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/monitor/status")
async def get_monitor_status():
    """
    鑾峰彇褰撳墠鐩戞帶鐘舵€併€?
    """
    config = dict(MONITOR_CONFIG)
    config.update(
        {
            "task_pool_root": settings.TASK_POOL_ROOT,
            "dinsar_task_pool_root": settings.DINSAR_TASK_POOL_ROOT,
            "sbas_task_pool_root": settings.SBAS_TASK_POOL_ROOT,
            "gf3_task_pool_root": settings.GF3_TASK_POOL_ROOT,
            "data_distribution_root": settings.DATA_DISTRIBUTION_ROOT,
            "dinsar_product_dir": settings.DINSAR_PRODUCT_DIR,
            "sbas_product_root": settings.GAMMA_SBAS_PRODUCT_ROOT,
            "orbit_source_dirs": split_env_paths(settings.ORBIT_SOURCE_DIRS) or split_env_paths(settings.MONITOR_ORBIT_DIR),
            "orbit_production_txt_pool": settings.ORBIT_POOL_ENVI,
            "storage_roots": _collect_storage_roots(),
        }
    )
    return config


@router.get("/monitor/logs")
async def get_monitor_logs(
    limit: int = MONITOR_LOG_DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    鑾峰彇鏈€鏂扮殑鐩戞帶鏃ュ織 (鏉ヨ嚜浠诲姟鏃ュ織琛?銆?
    """
    safe_limit = min(MONITOR_LOG_MAX_LIMIT, max(1, int(limit or MONITOR_LOG_DEFAULT_LIMIT)))
    safe_offset = min(MONITOR_LOG_MAX_OFFSET, max(0, int(offset or 0)))
    result = await db.execute(
        select(TaskLogORM, SystemTaskORM.task_type, SystemTaskORM.task_name)
        .join(SystemTaskORM, TaskLogORM.task_id == SystemTaskORM.task_id)
        .order_by(TaskLogORM.timestamp.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    rows = result.all()

    logs = []
    for log, task_type, task_name in reversed(rows):
        ts = log.timestamp.strftime('%H:%M:%S') if log.timestamp else '--:--:--'
        label = task_type or "TASK"
        if task_name:
            label = f"{label}:{task_name}"
        logs.append(f"[{ts}] [{log.log_level}] {label} - {log.message}")

    return {
        "limit": safe_limit,
        "offset": safe_offset,
        "count": len(logs),
        "logs": logs,
    }


@router.delete("/monitor/scan-task-history")
async def clear_monitor_scan_task_history(
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Clear finished scan task records and their logs from the monitor panel.

    Running and pending tasks are intentionally preserved.
    """
    task_rows = await db.execute(
        select(SystemTaskORM.task_id)
        .where(SystemTaskORM.task_type.in_(MONITOR_SCAN_TASK_TYPES))
        .where(SystemTaskORM.status.in_(MONITOR_TERMINAL_TASK_STATUSES))
    )
    task_ids = [str(task_id) for task_id in task_rows.scalars().all() if task_id]
    if not task_ids:
        return {
            "deleted_task_count": 0,
            "deleted_log_count": 0,
            "preserved_active": True,
        }

    log_count_result = await db.execute(
        select(func.count(TaskLogORM.id)).where(TaskLogORM.task_id.in_(task_ids))
    )
    deleted_log_count = int(log_count_result.scalar_one() or 0)

    await db.execute(delete(TaskLogORM).where(TaskLogORM.task_id.in_(task_ids)))
    task_delete_result = await db.execute(delete(SystemTaskORM).where(SystemTaskORM.task_id.in_(task_ids)))
    await db.commit()

    return {
        "deleted_task_count": int(task_delete_result.rowcount or len(task_ids)),
        "deleted_log_count": deleted_log_count,
        "preserved_active": True,
    }
