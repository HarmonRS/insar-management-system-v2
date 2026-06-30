from __future__ import annotations

import asyncio
import os
import re as _re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import idl_service
from ..config import read_int_env
from ..database import get_db
from .dependencies import _require_admin, _get_current_user, _validate_export_path, _validate_root_dir
from ..models import AuthUserORM
from ..services import envi_service
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service

router = APIRouter()

IDL_JOB_MAX_ATTEMPTS = read_int_env(
    "IDL_JOB_MAX_ATTEMPTS",
    6,
    minimum=1,
    maximum=30,
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ImportJobRequest(BaseModel):
    root_dir: str
    num_to_process: Optional[int] = Field(default=0, ge=0)
    timeout_seconds: Optional[int] = Field(default=None, ge=60)


class DinsarJobRequest(BaseModel):
    root_dir: str
    num_to_process: Optional[int] = Field(default=0, ge=0)
    timeout_seconds: Optional[int] = Field(default=None, ge=60)
    mode: str = Field(default="metatask", pattern=r"^(metatask|custom)$")


class InspectRequest(BaseModel):
    root_dir: str


class ExtractDispRequest(BaseModel):
    root_dir: str
    dest_dir: Optional[str] = None


class SarscapeSbasInspectRequest(BaseModel):
    task_names: Optional[List[str]] = None
    include_parameters: bool = False
    timeout_seconds: Optional[int] = Field(default=120, ge=10, le=600)


# ---------------------------------------------------------------------------
# Job queue helper
# ---------------------------------------------------------------------------

async def _queue_envi_job(
    *,
    job_type: str,
    task_type: str,
    default_task_name: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        task_id = await task_service.create_task(
            task_type=task_type,
            task_name=default_task_name,
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=job_type,
            payload=payload,
            task_id=task_id,
            max_attempts=IDL_JOB_MAX_ATTEMPTS,
        )
        return {
            "task_id": task_id,
            "job_id": job_id,
            "job_type": job_type,
            "message": "ENVI job queued.",
        }
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "任务冲突" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/idl/status")
async def get_status_endpoint():
    return envi_service.get_status()


@router.post("/idl/launch-workbench")
async def launch_workbench_endpoint(
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    success, message = idl_service.launch_idl_workbench()
    if not success:
        raise HTTPException(status_code=500, detail=message)
    return {"message": message}


@router.post("/idl/inspect/import")
async def inspect_import_endpoint(
    request: InspectRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    _validate_root_dir(request.root_dir)
    return envi_service.inspect_import(request.root_dir)


@router.post("/idl/inspect/dinsar")
async def inspect_dinsar_endpoint(
    request: InspectRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    _validate_root_dir(request.root_dir)
    return envi_service.inspect_dinsar(request.root_dir)


@router.post("/idl/inspect/sarscape-sbas")
async def inspect_sarscape_sbas_endpoint(
    request: SarscapeSbasInspectRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    try:
        return envi_service.inspect_sarscape_sbas_tasks_subprocess(
            request.task_names,
            timeout_seconds=request.timeout_seconds or 120,
            include_parameters=bool(request.include_parameters),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/idl/jobs/import")
async def run_import_job_endpoint(
    request: ImportJobRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    _validate_root_dir(request.root_dir)
    return await _queue_envi_job(
        job_type="IDL_RUN_IMPORT",
        task_type="IDL_IMPORT",
        default_task_name="ENVI Batch Import",
        payload={
            "root_dir": request.root_dir,
            "num_to_process": request.num_to_process or 0,
            "timeout_seconds": request.timeout_seconds,
        },
    )


@router.post("/idl/jobs/dinsar")
async def run_dinsar_job_endpoint(
    request: DinsarJobRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    _validate_root_dir(request.root_dir)
    return await _queue_envi_job(
        job_type="IDL_RUN_DINSAR",
        task_type="IDL_DINSAR",
        default_task_name="ENVI D-InSAR Workflow",
        payload={
            "root_dir": request.root_dir,
            "num_to_process": request.num_to_process or 0,
            "timeout_seconds": request.timeout_seconds,
            "mode": request.mode,
        },
    )


@router.get("/idl/jobs/recent")
async def list_recent_runs_endpoint(limit: int = 20):
    return {"runs": envi_service.list_recent_runs(limit=limit)}


_RUN_ID_RE = _re.compile(r"^[\w\-]{4,80}$")
_LOG_MAX_BYTES = 200 * 1024  # 200 KB


@router.delete("/idl/jobs/{run_id}")
async def delete_run_endpoint(
    run_id: str,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """Delete a run record and its associated log file."""
    _ = admin_user
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="无效的 run_id 格式")
    deleted = []
    for ext in (".json", ".log"):
        candidates = [
            os.path.join(envi_service.RUNTIME_DIR, "runs", f"{run_id}{ext}"),
            os.path.join(envi_service.RUNTIME_DIR, f"{run_id}{ext}"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                os.unlink(path)
                deleted.append(os.path.basename(path))
    if not deleted:
        raise HTTPException(status_code=404, detail="未找到该运行记录")
    return {"message": f"已删除: {', '.join(deleted)}"}


@router.get("/idl/jobs/{run_id}/log")
async def get_job_log_endpoint(
    run_id: str,
    current_user: AuthUserORM = Depends(_get_current_user),
):
    _ = current_user
    if not _RUN_ID_RE.match(run_id):
        raise HTTPException(status_code=400, detail="无效的 run_id 格式")
    log_path = os.path.join(envi_service.RUNTIME_DIR, f"{run_id}.log")
    if not os.path.isfile(log_path):
        raise HTTPException(status_code=404, detail="日志文件不存在")
    size_bytes = os.path.getsize(log_path)
    truncated = size_bytes > _LOG_MAX_BYTES
    with open(log_path, "r", encoding="utf-8", errors="replace") as fp:
        if truncated:
            fp.seek(size_bytes - _LOG_MAX_BYTES)
            content = "...[日志已截断，仅显示末尾 200KB]...\n" + fp.read()
        else:
            content = fp.read()
    return {"run_id": run_id, "content": content, "size_bytes": size_bytes, "truncated": truncated}


@router.get("/idl/task-overview")
async def get_task_overview_endpoint(
    root_dir: str,
    current_user: AuthUserORM = Depends(_get_current_user),
):
    _ = current_user
    _validate_root_dir(root_dir)
    try:
        result = await asyncio.to_thread(envi_service.get_task_overview, root_dir)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/idl/extract-disp", status_code=202)
async def extract_disp_endpoint(
    request: ExtractDispRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = admin_user
    _validate_root_dir(request.root_dir)
    if request.dest_dir:
        _validate_export_path(request.dest_dir, "dest_dir")
    try:
        payload = {
            "root_dir": request.root_dir,
            "dest_dir": request.dest_dir,
        }
        task_id = await task_service.create_task(
            "EXTRACT_DINSAR_PRODUCTS",
            "D-InSAR 结果提取与登记",
            params=payload,
            db=db,
        )
        job_id = await job_queue_service.create_job(
            "EXTRACT_DINSAR_PRODUCTS",
            payload=payload,
            task_id=task_id,
            db=db,
        )
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "queued": True,
        "task_id": task_id,
        "job_id": job_id,
        "message": "D-InSAR 结果提取与登记任务已入队",
    }
