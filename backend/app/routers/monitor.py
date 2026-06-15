from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
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


class MonitorConfig(BaseModel):
    radar_dirs: List[str] = []
    orbit_dir: Optional[str] = None
    dinsar_dirs: List[str] = []
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
    更新数据监控配置（雷达、精轨和Dinsar结果）。
    """
    raise HTTPException(
        status_code=403,
        detail="Monitor config is read-only. Edit .env and restart the backend."
    )


@router.post("/monitor/run-now")
async def run_monitor_now(target: Optional[str] = None, background_tasks: BackgroundTasks = None, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    手动触发一次监控任务（扫描所有配置的目录）。
    target: 'radar', 'orbit', 'dinsar' 或 None (全部)
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
    task_name = f"手动触发扫描 ({normalized_target or '全部'})"

    try:
        task_id = await task_service.create_task(task_type, task_name)
        if task_type == "SCAN_DINSAR":
            payload = {"dirs": dinsar_dirs}
            job_type = "SCAN_DINSAR"
        elif normalized_target == "gf3":
            # GF3 扫描：用 gf3_storage_dirs 作为 radar_dirs
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
            "message": f"已触发{normalized_target or '全部'}手动扫描任务（已进入队列）",
            "task_id": task_id
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/monitor/gf3-process")
async def run_gf3_batch_process(admin_user: AuthUserORM = Depends(_require_admin)):
    """
    批量 GF3 L1A→L2 处理：扫描 GF3_SOURCE_DIRS，过滤已处理的，逐个处理并自动入库。
    """
    if not settings.GF3_LEGACY_GDAL_ENABLED:
        raise HTTPException(
            status_code=409,
            detail=(
                "Legacy GF3 Python/GDAL preprocessing is disabled. "
                "Use GF3 SARscape production or set GF3_LEGACY_GDAL_ENABLED=true explicitly."
            ),
        )
    gf3_source_dirs = MONITOR_CONFIG.get("gf3_source_dirs") or []
    if not gf3_source_dirs:
        raise HTTPException(status_code=400, detail="GF3_SOURCE_DIRS is not configured.")

    task_type = "GF3_BATCH_PROCESS"
    task_name = "GF3 批量 L1A→L2 处理"

    try:
        task_id = await task_service.create_task(task_type, task_name)
        await job_queue_service.create_job(
            "GF3_BATCH_PROCESS",
            payload={"source_dirs": gf3_source_dirs},
            task_id=task_id,
        )
        return {
            "message": "GF3 批量处理任务已提交",
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
    扫描 GF3 SARscape 原生 _geo 二进制池，转换为标准 GeoTIFF，并登记入库。
    """
    gf3_native_dirs = MONITOR_CONFIG.get("gf3_sarscape_native_dirs") or []
    gf3_storage_dirs = MONITOR_CONFIG.get("gf3_storage_dirs") or []
    if not gf3_native_dirs:
        raise HTTPException(status_code=400, detail="GF3_SARSCAPE_NATIVE_DIRS is not configured.")
    if not gf3_storage_dirs:
        raise HTTPException(status_code=400, detail="GF3_STORAGE_DIRS is not configured.")

    options = request_data or GF3SarscapeSyncRequest()
    task_type = "GF3_SARSCAPE_SYNC"
    task_name = "GF3 SARscape 原生结果标准化"
    payload = {
        "native_dirs": gf3_native_dirs,
        "storage_root": gf3_storage_dirs[0],
        "force": bool(options.force),
        "register": bool(options.register),
    }

    try:
        task_id = await task_service.create_task(task_type, task_name, params=payload)
        await job_queue_service.create_job(task_type, payload=payload, task_id=task_id)
        return {
            "message": "GF3 SARscape 原生结果标准化任务已提交",
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
    Run GF3 raw archives through the SARscape wrapper, standardize outputs, and optionally clean intermediates.
    """
    gf3_archive_source_dirs = MONITOR_CONFIG.get("gf3_archive_source_dirs") or []
    gf3_native_dirs = MONITOR_CONFIG.get("gf3_sarscape_native_dirs") or []
    gf3_storage_dirs = MONITOR_CONFIG.get("gf3_storage_dirs") or []
    if not gf3_archive_source_dirs:
        raise HTTPException(status_code=400, detail="GF3_ARCHIVE_SOURCE_DIRS is not configured.")
    if not gf3_native_dirs:
        raise HTTPException(status_code=400, detail="GF3_SARSCAPE_NATIVE_DIRS is not configured.")
    if not gf3_storage_dirs:
        raise HTTPException(status_code=400, detail="GF3_STORAGE_DIRS is not configured.")
    if not settings.GF3_SARSCAPE_WRAPPER_EXE:
        raise HTTPException(status_code=400, detail="GF3_SARSCAPE_WRAPPER_EXE is not configured.")
    if not (settings.GF3_SARSCAPE_DEM_PATH or settings.GF3_GEO_DEM_PATH):
        raise HTTPException(status_code=400, detail="GF3_SARSCAPE_DEM_PATH or GF3_GEO_DEM_PATH is not configured.")

    options = request_data or GF3SarscapeProduceRequest()
    selected_dates = []
    for raw_date in options.selected_dates or []:
        text = str(raw_date or "").strip()
        if not text:
            continue
        normalized = text.replace("-", "").replace("_", "")
        if len(normalized) != 8 or not normalized.isdigit():
            raise HTTPException(status_code=400, detail=f"Invalid GF3 scene date: {raw_date}")
        if normalized not in selected_dates:
            selected_dates.append(normalized)
    task_type = "GF3_SARSCAPE_PRODUCE"
    task_name = "GF3 SARscape production"
    auto_standardize = settings.GF3_SARSCAPE_AUTO_STANDARDIZE if options.auto_standardize is None else bool(options.auto_standardize)
    clean_after_success = settings.GF3_SARSCAPE_CLEAN_AFTER_SUCCESS if options.clean_after_success is None else bool(options.clean_after_success)
    payload = {
        "source_dirs": gf3_archive_source_dirs,
        "native_dirs": gf3_native_dirs,
        "native_root": gf3_native_dirs[0],
        "storage_root": gf3_storage_dirs[0],
        "wrapper_exe": settings.GF3_SARSCAPE_WRAPPER_EXE,
        "idlrt_path": settings.GF3_SARSCAPE_IDLRT_PATH,
        "dem_path": settings.GF3_SARSCAPE_DEM_PATH or settings.GF3_GEO_DEM_PATH,
        "polarizations": settings.GF3_SARSCAPE_POLARIZATIONS,
        "archive_exts": split_env_paths(settings.GF3_ARCHIVE_EXTS),
        "max_archives_per_run": int(options.max_archives_per_run or 0),
        "selected_dates": selected_dates,
        "local_staging_root": settings.GF3_TASK_POOL_ROOT,
        "timeout_seconds": int(settings.GF3_SARSCAPE_PRODUCE_TIMEOUT_SECONDS or 0),
        "keep_extracted": bool(settings.GF3_SARSCAPE_KEEP_EXTRACTED),
        "auto_standardize": bool(auto_standardize),
        "clean_after_success": bool(clean_after_success),
        "force_standardize": bool(options.force_standardize),
        "register": bool(options.register),
        "cleanup_require_standardized": True,
        "cleanup_dry_run": bool(options.cleanup_dry_run),
    }

    try:
        task_id = await task_service.create_task(task_type, task_name, params=payload)
        await job_queue_service.create_job(task_type, payload=payload, task_id=task_id)
        return {
            "message": (
                f"GF3 SARscape production task submitted for {', '.join(selected_dates)}"
                if selected_dates
                else "GF3 SARscape production task submitted"
            ),
            "task_id": task_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/monitor/gf3-sarscape-dates")
async def list_gf3_sarscape_dates(admin_user: AuthUserORM = Depends(_require_admin)):
    """
    List available GF3 SARscape source dates from configured raw archive roots.
    """
    from ..services.gf3_sarscape_production_service import discover_gf3_sarscape_inputs

    gf3_archive_source_dirs = MONITOR_CONFIG.get("gf3_archive_source_dirs") or []
    if not gf3_archive_source_dirs:
        raise HTTPException(status_code=400, detail="GF3_ARCHIVE_SOURCE_DIRS is not configured.")

    discovery = discover_gf3_sarscape_inputs(
        gf3_archive_source_dirs,
        archive_exts=split_env_paths(settings.GF3_ARCHIVE_EXTS),
    )
    by_date: dict[str, dict[str, object]] = {}
    undated = 0
    for item in discovery.get("inputs") or []:
        scene_name = str(item.get("scene_name") or "")
        date_text = str(item.get("scene_date") or "")
        if not date_text:
            undated += 1
            continue
        bucket = by_date.setdefault(date_text, {"date": date_text, "scene_count": 0, "scenes": []})
        bucket["scene_count"] = int(bucket.get("scene_count") or 0) + 1
        scenes = bucket.get("scenes")
        if isinstance(scenes, list) and len(scenes) < 20:
            scenes.append(scene_name)

    dates = sorted(by_date.values(), key=lambda item: str(item.get("date") or ""), reverse=True)
    return {
        "dates": dates,
        "input_count": discovery.get("input_count") or 0,
        "undated_count": undated,
        "missing_roots": discovery.get("missing_roots") or [],
    }


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
    将 GF3 压缩包池解包到 GF3_SOURCE_DIRS，作为后续 L1A→L2 预处理输入。
    """
    if not settings.GF3_LEGACY_GDAL_ENABLED:
        raise HTTPException(
            status_code=409,
            detail=(
                "Legacy GF3 archive unpack is disabled. "
                "Use GF3 SARscape production or set GF3_LEGACY_GDAL_ENABLED=true explicitly."
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
    task_name = "GF3 压缩包解包"
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
            "message": "GF3 解包任务已提交",
            "task_id": task_id,
        }
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/monitor/status")
async def get_monitor_status():
    """
    获取当前监控状态。
    """
    return MONITOR_CONFIG


@router.get("/monitor/logs")
async def get_monitor_logs(
    limit: int = MONITOR_LOG_DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    获取最新的监控日志 (来自任务日志表)。
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
