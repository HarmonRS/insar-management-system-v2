from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import database
from ..auth_service import SESSION_COOKIE_NAME, get_user_by_session_token
from ..auth_utils import verify_password
from ..models import AuthUserORM, TaskInfo
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
