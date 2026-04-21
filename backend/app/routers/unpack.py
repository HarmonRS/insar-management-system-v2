from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..models import AuthUserORM
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from ..services.unpack_service import build_unpack_run_config, get_unpack_config
from .dependencies import _require_admin

router = APIRouter()


class UnpackRunRequest(BaseModel):
    max_files_per_run: Optional[int] = Field(default=None, ge=0)
    max_runtime_minutes: Optional[int] = Field(default=None, ge=0)


@router.get("/unpack/config")
async def get_unpack_config_endpoint():
    return get_unpack_config()


@router.post("/unpack/run", status_code=202)
async def run_unpack_endpoint(
    request: Optional[UnpackRunRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    del admin_user

    overrides = request.model_dump(exclude_none=True) if request else None
    config = build_unpack_run_config(overrides)
    if not config.get("source_dirs"):
        raise HTTPException(status_code=400, detail="UNPACK_SOURCE_DIRS is not configured.")

    try:
        task_id = await task_service.create_task(
            "UNPACK_ARCHIVES",
            "Archive unpack",
            params=config,
        )
        await job_queue_service.create_job(
            "UNPACK_ARCHIVES",
            payload=config,
            task_id=task_id,
        )
        return {"message": "Unpack task queued", "task_id": task_id}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
