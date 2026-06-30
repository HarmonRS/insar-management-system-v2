from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select

from .. import database
from ..auth_service import SESSION_COOKIE_NAME, get_user_by_session_token
from ..auth_utils import verify_password
from ..config import settings
from ..models import AuthUserORM, SystemJobORM, SystemTaskORM, SystemWorkerHeartbeatORM, TaskInfo
from ..services.dinsar_production_service import dinsar_production_service
from ..services.task_service import (
    TASK_ACTIVE_DEFAULT_LIMIT,
    TASK_ACTIVE_MAX_LIMIT,
    TASK_LOG_DEFAULT_LIMIT,
    TASK_LOG_MAX_LIMIT,
    TASK_QUERY_MAX_OFFSET,
    task_service,
)
from .dependencies import _require_admin

router = APIRouter()


class ForceCancelRequest(BaseModel):
    password: str


def _new_session():
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _split_csv_param(raw: Optional[str]) -> List[str]:
    values: List[str] = []
    for chunk in str(raw or "").split(","):
        text = chunk.strip()
        if text and text not in values:
            values.append(text)
    return values


def _dt(value):
    return value.isoformat() if value else None


def _task_payload(task: SystemTaskORM) -> dict:
    return TaskInfo.model_validate(task).model_dump(mode="json")


def _worker_note(worker: SystemWorkerHeartbeatORM) -> dict:
    try:
        parsed = json.loads(str(worker.note or "") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _worker_concurrency(worker: SystemWorkerHeartbeatORM) -> int:
    note = _worker_note(worker)
    try:
        return max(1, int(note.get("concurrency") or 1))
    except (TypeError, ValueError):
        return 1


def _job_payload(job: SystemJobORM, task_by_id: dict[str, SystemTaskORM]) -> dict:
    task = task_by_id.get(str(job.task_id or ""))
    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "priority": int(job.priority or 0),
        "attempts": int(job.attempts or 0),
        "max_attempts": int(job.max_attempts or 0),
        "task_id": job.task_id,
        "task_type": task.task_type if task else None,
        "task_name": task.task_name if task else None,
        "task_status": task.status if task else None,
        "task_progress": int(task.progress or 0) if task else None,
        "task_message": task.message if task else None,
        "workflow_run_id": job.workflow_run_id,
        "workflow_step_id": job.workflow_step_id,
        "locked_by": job.locked_by,
        "locked_at": _dt(job.locked_at),
        "heartbeat_at": _dt(job.heartbeat_at),
        "next_run_at": _dt(job.next_run_at),
        "created_at": _dt(job.created_at),
        "started_at": _dt(job.started_at),
        "finished_at": _dt(job.finished_at),
        "last_error": job.last_error,
    }


def _worker_payload(worker: SystemWorkerHeartbeatORM, active_job_count: int, concurrency: int) -> dict:
    note = _worker_note(worker)
    return {
        "worker_id": worker.worker_id,
        "hostname": worker.hostname,
        "pid": worker.pid,
        "note": worker.note,
        "concurrency": concurrency,
        "allowed_job_types": note.get("allowed_job_types") if isinstance(note.get("allowed_job_types"), list) else [],
        "started_at": _dt(worker.started_at),
        "last_seen": _dt(worker.last_seen),
        "active_job_count": active_job_count,
    }


@router.get("/tasks/active", response_model=List[TaskInfo])
async def get_active_tasks(limit: int = TASK_ACTIVE_DEFAULT_LIMIT, offset: int = 0):
    safe_limit = min(TASK_ACTIVE_MAX_LIMIT, max(1, int(limit or TASK_ACTIVE_DEFAULT_LIMIT)))
    safe_offset = min(TASK_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    orm_tasks = await task_service.get_active_tasks(limit=safe_limit, offset=safe_offset)
    return [TaskInfo.model_validate(task) for task in orm_tasks]


@router.get("/tasks/recent", response_model=List[TaskInfo])
async def get_recent_tasks(
    task_types: Optional[str] = Query(None, description="Comma-separated task types."),
    statuses: Optional[str] = Query(None, description="Comma-separated task statuses."),
    limit: int = TASK_ACTIVE_DEFAULT_LIMIT,
    offset: int = 0,
):
    safe_limit = min(TASK_ACTIVE_MAX_LIMIT, max(1, int(limit or TASK_ACTIVE_DEFAULT_LIMIT)))
    safe_offset = min(TASK_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    orm_tasks = await task_service.list_tasks(
        task_types=_split_csv_param(task_types),
        statuses=_split_csv_param(statuses),
        limit=safe_limit,
        offset=safe_offset,
    )
    return [TaskInfo.model_validate(task) for task in orm_tasks]


@router.get("/tasks/runtime-summary")
async def get_task_runtime_summary(limit: int = TASK_ACTIVE_DEFAULT_LIMIT, offset: int = 0):
    safe_limit = min(TASK_ACTIVE_MAX_LIMIT, max(1, int(limit or TASK_ACTIVE_DEFAULT_LIMIT)))
    safe_offset = min(TASK_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    active_job_statuses = ["READY", "RETRY", "RUNNING"]
    scan_job_types = {
        "SCAN_DATA",
        "SCAN_DINSAR",
        "SCAN_ASSET_INVENTORY",
        "AUDIT_SOURCE_ARCHIVE_INTEGRITY",
        "GF3_SARSCAPE_SYNC",
        "GF3_QUICKLOOK_WEBP",
    }

    worker_timeout = max(5, int(getattr(settings, "JOB_WORKER_HEALTH_TIMEOUT", 60) or 60))
    worker_threshold = datetime.utcnow() - timedelta(seconds=worker_timeout)
    configured_concurrency = max(1, int(getattr(settings, "JOB_WORKER_CONCURRENCY", 1) or 1))

    async with _new_session() as db:
        active_tasks = await task_service.get_active_tasks(limit=safe_limit, offset=safe_offset, db=db)

        workers_result = await db.execute(
            select(SystemWorkerHeartbeatORM)
            .where(SystemWorkerHeartbeatORM.last_seen >= worker_threshold)
            .order_by(SystemWorkerHeartbeatORM.last_seen.desc())
        )
        active_workers = workers_result.scalars().all()
        active_worker_ids = {str(worker.worker_id) for worker in active_workers}

        running_by_worker_result = await db.execute(
            select(SystemJobORM.locked_by, func.count(SystemJobORM.id))
            .where(SystemJobORM.status == "RUNNING")
            .group_by(SystemJobORM.locked_by)
        )
        running_by_worker = {
            str(worker_id or ""): int(count or 0)
            for worker_id, count in running_by_worker_result.all()
        }

        status_counts_result = await db.execute(
            select(SystemJobORM.status, func.count(SystemJobORM.id))
            .where(SystemJobORM.status.in_(active_job_statuses))
            .group_by(SystemJobORM.status)
        )
        job_status_counts = {
            "READY": 0,
            "RETRY": 0,
            "RUNNING": 0,
        }
        for status, count in status_counts_result.all():
            job_status_counts[str(status or "").upper()] = int(count or 0)

        status_rank = case(
            (SystemJobORM.status == "RUNNING", 0),
            (SystemJobORM.status == "RETRY", 1),
            else_=2,
        )
        jobs_result = await db.execute(
            select(SystemJobORM)
            .where(SystemJobORM.status.in_(active_job_statuses))
            .order_by(status_rank, SystemJobORM.priority.desc(), SystemJobORM.id.asc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        active_jobs = jobs_result.scalars().all()

        task_ids = {
            str(task.task_id)
            for task in active_tasks
            if task.task_id
        }
        task_ids.update(
            str(job.task_id)
            for job in active_jobs
            if job.task_id
        )
        task_by_id: dict[str, SystemTaskORM] = {}
        if task_ids:
            task_result = await db.execute(
                select(SystemTaskORM).where(SystemTaskORM.task_id.in_(sorted(task_ids)))
            )
            task_by_id = {
                str(task.task_id): task
                for task in task_result.scalars().all()
                if task.task_id
            }

    worker_concurrency_by_id = {
        str(worker.worker_id): _worker_concurrency(worker)
        for worker in active_workers
    }
    total_slots = sum(worker_concurrency_by_id.values())
    busy_slots = sum(
        count
        for worker_id, count in running_by_worker.items()
        if worker_id in active_worker_ids
    )
    running_count = int(job_status_counts.get("RUNNING") or 0)
    stale_running_count = max(0, running_count - busy_slots)
    queued_count = int(job_status_counts.get("READY") or 0) + int(job_status_counts.get("RETRY") or 0)

    task_items = [_task_payload(task) for task in active_tasks]
    job_items = [_job_payload(job, task_by_id) for job in active_jobs]
    scan_jobs = [
        item for item in job_items
        if str(item.get("job_type") or "").upper() in scan_job_types
    ]
    scan_tasks = [
        item for item in task_items
        if str(item.get("task_type") or "").upper() in scan_job_types
    ]

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "worker": {
            "ok": len(active_workers) > 0,
            "worker_count": len(active_workers),
            "configured_concurrency": configured_concurrency,
            "total_slots": total_slots,
            "busy_slots": busy_slots,
            "idle_slots": max(0, total_slots - busy_slots),
            "timeout_seconds": worker_timeout,
            "stale_running_job_count": stale_running_count,
            "workers": [
                _worker_payload(
                    worker,
                    running_by_worker.get(str(worker.worker_id), 0),
                    worker_concurrency_by_id.get(str(worker.worker_id), 1),
                )
                for worker in active_workers
            ],
        },
        "jobs": {
            "active_count": running_count + queued_count,
            "running_count": running_count,
            "queued_count": queued_count,
            "ready_count": int(job_status_counts.get("READY") or 0),
            "retry_count": int(job_status_counts.get("RETRY") or 0),
            "items": job_items,
        },
        "tasks": {
            "active_count": len(task_items),
            "running_count": sum(1 for item in task_items if item.get("status") == "RUNNING"),
            "pending_count": sum(1 for item in task_items if item.get("status") == "PENDING"),
            "items": task_items,
        },
        "scan": {
            "active_task_count": len(scan_tasks),
            "active_job_count": len(scan_jobs),
            "running_job_count": sum(1 for item in scan_jobs if item.get("status") == "RUNNING"),
            "queued_job_count": sum(1 for item in scan_jobs if item.get("status") in {"READY", "RETRY"}),
            "tasks": scan_tasks,
            "jobs": scan_jobs,
        },
    }


@router.get("/tasks/runtime-summary/stream")
async def stream_task_runtime_summary(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    async with _new_session() as db:
        user = await get_user_by_session_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                summary = await get_task_runtime_summary(
                    limit=TASK_ACTIVE_MAX_LIMIT,
                    offset=0,
                )
                yield f"data: {json.dumps(summary)}\n\n"
            except Exception:
                yield "data: {}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/tasks/active/stream")
async def stream_active_tasks(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    async with _new_session() as db:
        user = await get_user_by_session_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                orm_tasks = await task_service.get_active_tasks(
                    limit=TASK_ACTIVE_MAX_LIMIT,
                    offset=0,
                )
                tasks_data = [TaskInfo.model_validate(task).model_dump() for task in orm_tasks]
                yield f"data: {json.dumps(tasks_data)}\n\n"
            except Exception:
                yield "data: []\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/tasks/{task_id}", response_model=Optional[TaskInfo])
async def get_task_status(task_id: str):
    task = await task_service.get_task(task_id)
    if task:
        return TaskInfo.model_validate(task)
    return None


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, limit: int = TASK_LOG_DEFAULT_LIMIT, offset: int = 0):
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    safe_limit = min(TASK_LOG_MAX_LIMIT, max(1, int(limit or TASK_LOG_DEFAULT_LIMIT)))
    safe_offset = min(TASK_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    logs = await task_service.get_logs(task_id, limit=safe_limit, offset=safe_offset)
    return {
        "task_id": task_id,
        "limit": safe_limit,
        "offset": safe_offset,
        "count": len(logs),
        "logs": [
            {
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "level": log.log_level,
                "message": log.message,
            }
            for log in logs
        ],
    }


@router.delete("/tasks/{task_id}/logs/{log_id}")
async def delete_task_log(
    task_id: str,
    log_id: int,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    deleted = await task_service.delete_log(task_id, log_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task log entry not found.")

    return {
        "task_id": task_id,
        "log_id": log_id,
        "deleted": True,
    }


@router.delete("/tasks/{task_id}/logs")
async def clear_task_logs(
    task_id: str,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    deleted_count = await task_service.clear_logs(task_id)
    return {
        "task_id": task_id,
        "deleted_count": deleted_count,
    }


@router.delete("/tasks/{task_id}")
async def delete_task_record(
    task_id: str,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if str(task.status or "").upper() in {"PENDING", "RUNNING"}:
        raise HTTPException(status_code=409, detail="Cannot delete a pending or running task.")

    deleted = await task_service.delete_task_record(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {
        "task_id": task_id,
        "deleted": True,
    }


@router.post("/tasks/{task_id}/force-cancel")
async def force_cancel_task(
    task_id: str,
    body: ForceCancelRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    if not verify_password(body.password, admin_user.password_hash):
        raise HTTPException(status_code=403, detail="Password is incorrect.")

    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.status not in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=400, detail="Task is already finished.")

    killed_pid = None
    async with _new_session() as db:
        run = await dinsar_production_service.request_cancel(task_id, db=db)
        if run is not None:
            killed_pid = await dinsar_production_service.kill_active_execution_by_task_id(
                task_id,
                db=db,
            )

    await task_service.update_task(
        task_id,
        status="CANCELLED",
        message="Task cancelled by administrator.",
    )
    return {
        "message": "Task cancelled.",
        "task_id": task_id,
        "killed_pid": killed_pid,
    }
