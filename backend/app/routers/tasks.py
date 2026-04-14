from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..auth_utils import verify_password
from ..models import AuthUserORM, TaskInfo
from ..services.task_service import (
    TASK_ACTIVE_DEFAULT_LIMIT,
    TASK_ACTIVE_MAX_LIMIT,
    TASK_LOG_DEFAULT_LIMIT,
    TASK_LOG_MAX_LIMIT,
    TASK_QUERY_MAX_OFFSET,
    task_service,
)
from ..auth_service import SESSION_COOKIE_NAME, get_user_by_session_token
from ..database import AsyncSessionLocal
from .dependencies import _require_admin

router = APIRouter()


class ForceCancelRequest(BaseModel):
    password: str


@router.get("/tasks/active", response_model=List[TaskInfo])
async def get_active_tasks(limit: int = TASK_ACTIVE_DEFAULT_LIMIT, offset: int = 0):
    """获取当前正在运行的所有后台任务"""
    safe_limit = min(TASK_ACTIVE_MAX_LIMIT, max(1, int(limit or TASK_ACTIVE_DEFAULT_LIMIT)))
    safe_offset = min(TASK_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    orm_tasks = await task_service.get_active_tasks(limit=safe_limit, offset=safe_offset)
    return [TaskInfo.model_validate(t) for t in orm_tasks]


@router.get("/tasks/active/stream")
async def stream_active_tasks(request: Request):
    """SSE 端点：每 3s 推送一次活跃任务列表，替代前端轮询。"""
    # Authenticate via Cookie at connection establishment
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token and AsyncSessionLocal:
        async with AsyncSessionLocal() as db:
            user = await get_user_by_session_token(db, token)
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required.")
    elif not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                orm_tasks = await task_service.get_active_tasks(
                    limit=TASK_ACTIVE_MAX_LIMIT, offset=0
                )
                tasks_data = [TaskInfo.model_validate(t).model_dump() for t in orm_tasks]
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
    """获取特定任务的状态"""
    task = await task_service.get_task(task_id)
    if task:
        return TaskInfo.model_validate(task)
    return None


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(task_id: str, limit: int = TASK_LOG_DEFAULT_LIMIT, offset: int = 0):
    """获取指定任务的日志（支持 limit/offset）。"""
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")

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
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "level": log.log_level,
                "message": log.message,
            }
            for log in logs
        ],
    }


@router.post("/tasks/{task_id}/force-cancel")
async def force_cancel_task(
    task_id: str,
    body: ForceCancelRequest,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """管理员输入密码后强制取消任务（解锁前端）"""
    if not verify_password(body.password, admin_user.password_hash):
        raise HTTPException(status_code=403, detail="密码错误")
    task = await task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    if task.status not in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=400, detail="任务已结束，无需取消")
    await task_service.update_task(task_id, status="CANCELLED", message="管理员强制取消")
    return {"message": "任务已强制取消", "task_id": task_id}
