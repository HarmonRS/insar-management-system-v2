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
    gf3_storage_dirs: List[str] = []
    # Manual-only: config is read from .env


class GF3UnpackConfig(BaseModel):
    source_dirs: List[str] = []
    target_dirs: List[str] = []
    archive_exts: List[str] = []
    delete_archive: bool = False


class GF3UnpackRunRequest(BaseModel):
    max_files_per_run: Optional[int] = Field(default=None, ge=0)


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
