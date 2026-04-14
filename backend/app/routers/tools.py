from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import read_int_env
from ..database import get_db
from ..services.job_queue_service import job_queue_service
from ..services.task_service import TASK_LOG_DEFAULT_LIMIT, TASK_LOG_MAX_LIMIT, TASK_QUERY_MAX_OFFSET, task_service
from .dependencies import _add_operation_audit_log, _validate_export_path

router = APIRouter()

COPY_BATCH_ALLOWED_STATUSES = {"PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"}
COPY_BATCH_TEXT_MAX_LENGTH = read_int_env(
    "COPY_BATCH_TEXT_MAX_LENGTH",
    2048,
    minimum=64,
    maximum=32767,
)
COPY_BATCH_MAX_STATUS_COUNT = read_int_env(
    "COPY_BATCH_MAX_STATUS_COUNT",
    8,
    minimum=1,
    maximum=64,
)


class CopyBatchRequest(BaseModel):
    batch_id: str = Field(max_length=COPY_BATCH_TEXT_MAX_LENGTH)
    dest_dir: str = Field(max_length=COPY_BATCH_TEXT_MAX_LENGTH)
    copy_statuses: Optional[List[str]] = None

    @field_validator("batch_id", "dest_dir", mode="before")
    @classmethod
    def _normalize_required_text(cls, value):
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Field must not be empty.")
        return normalized

    @field_validator("copy_statuses", mode="before")
    @classmethod
    def _validate_copy_statuses_length(cls, value):
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("copy_statuses must be a list.")
        if len(value) > COPY_BATCH_MAX_STATUS_COUNT:
            raise ValueError(
                f"copy_statuses exceeds max count ({COPY_BATCH_MAX_STATUS_COUNT})."
            )
        return value


def _normalize_copy_batch_statuses(copy_statuses: Optional[List[str]]) -> List[str]:
    if not copy_statuses:
        return ["COMPLETED"]

    normalized: List[str] = []
    for raw in copy_statuses:
        status = (raw or "").strip().upper()
        if not status:
            continue
        if status not in COPY_BATCH_ALLOWED_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid copy status: {status}. Allowed: {sorted(COPY_BATCH_ALLOWED_STATUSES)}",
            )
        if status not in normalized:
            normalized.append(status)

    return normalized or ["COMPLETED"]


@router.post("/tools/copy-ps-stack")
async def copy_ps_stack_endpoint(
    request: CopyBatchRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Start PS-InSAR copy task from a batch.
    """
    _validate_export_path(request.dest_dir, "dest_dir")
    try:
        copy_statuses = _normalize_copy_batch_statuses(request.copy_statuses)
        params = {
            "dest_dir": request.dest_dir,
            "file_type": "PS_STACK",
            "batch_id": request.batch_id,
            "copy_statuses": copy_statuses,
        }
        task_id = await task_service.create_task("COPY_DATA", f"PS数据分发: {request.dest_dir}", params=params)

        payload = {
            "file_type": "PS_STACK",
            "dest_dir": request.dest_dir,
            "batch_id": request.batch_id,
            "copy_statuses": copy_statuses,
        }
        await job_queue_service.create_job("COPY_DATA", payload=payload, task_id=task_id)
        await _add_operation_audit_log(
            db,
            request=http_request,
            action="task_queued",
            resource="tools/copy-ps-stack",
            detail={
                "task_id": task_id,
                "batch_id": request.batch_id,
                "dest_dir": request.dest_dir,
                "copy_statuses": copy_statuses,
            },
        )
        await db.commit()
        return {"message": "PS-InSAR复制任务已进入队列", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/tools/copy-dinsar-pairs")
async def copy_dinsar_pairs_endpoint(
    request: CopyBatchRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Start D-InSAR copy task from a batch.
    """
    _validate_export_path(request.dest_dir, "dest_dir")
    try:
        copy_statuses = _normalize_copy_batch_statuses(request.copy_statuses)
        params = {
            "dest_dir": request.dest_dir,
            "file_type": "DINSAR_PAIRS",
            "batch_id": request.batch_id,
            "copy_statuses": copy_statuses,
        }
        task_id = await task_service.create_task("COPY_DATA", f"D-InSAR 数据分发: {request.dest_dir}", params=params)

        payload = {
            "file_type": "DINSAR_PAIRS",
            "dest_dir": request.dest_dir,
            "batch_id": request.batch_id,
            "copy_statuses": copy_statuses,
        }
        await job_queue_service.create_job("COPY_DATA", payload=payload, task_id=task_id)
        await _add_operation_audit_log(
            db,
            request=http_request,
            action="task_queued",
            resource="tools/copy-dinsar-pairs",
            detail={
                "task_id": task_id,
                "batch_id": request.batch_id,
                "dest_dir": request.dest_dir,
                "copy_statuses": copy_statuses,
            },
        )
        await db.commit()
        return {"message": "D-InSAR复制任务已进入队列", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/tools/copy-status/{task_id}")
async def get_copy_status_endpoint(
    task_id: str,
    limit: int = TASK_LOG_DEFAULT_LIMIT,
    offset: int = 0,
):
    """
    获取复制任务的状态和日志。
    """
    task = await task_service.get_task(task_id)
    safe_limit = min(TASK_LOG_MAX_LIMIT, max(1, int(limit or TASK_LOG_DEFAULT_LIMIT)))
    safe_offset = min(TASK_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    logs = await task_service.get_logs(task_id, limit=safe_limit, offset=safe_offset)

    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")

    return {
        "task_id": task_id,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "limit": safe_limit,
        "offset": safe_offset,
        "logs": [f"[{l.timestamp.strftime('%H:%M:%S')}] [{l.log_level}] {l.message}" for l in logs]
    }
