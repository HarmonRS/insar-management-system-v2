from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from ..services.unpack_service import get_unpack_config
from .dependencies import _require_admin

router = APIRouter()


@router.get("/unpack/config")
async def get_unpack_config_endpoint():
    """
    获取解包配置 (来源 .env)。
    """
    return get_unpack_config()


@router.post("/unpack/run", status_code=202)
async def run_unpack_endpoint(background_tasks: BackgroundTasks, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    触发一次解包任务。
    """
    config = get_unpack_config()
    if not config.get("source_dirs"):
        raise HTTPException(status_code=400, detail="UNPACK_SOURCE_DIRS is not configured.")

    try:
        task_id = await task_service.create_task(
            "UNPACK_ARCHIVES",
            "Archive unpack",
            params=config,
        )
        await job_queue_service.create_job("UNPACK_ARCHIVES", payload=config, task_id=task_id)
        return {"message": "Unpack task queued", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
