from __future__ import annotations

import os
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..config import settings
from ..database import get_db
from ..models import AuthUserORM, HazardPoint, HazardPointORM
from ..services.data_service import data_service
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from .dependencies import _require_admin

router = APIRouter()


def _resolve_hazard_shp_path() -> str:
    base_dir = settings.HAZARD_POINTS_DIR
    filename = settings.HAZARD_POINTS_FILENAME
    if not base_dir or not filename:
        raise ValueError("HAZARD_POINTS_DIR and HAZARD_POINTS_FILENAME must be configured.")
    if str(base_dir).lower().endswith(".shp"):
        raise ValueError("HAZARD_POINTS_DIR must be a directory, not a .shp path.")
    if not os.path.isdir(str(base_dir)):
        raise FileNotFoundError(f"Hazard points directory not found: {base_dir}")
    shp_path = os.path.join(str(base_dir), str(filename))
    if not shp_path.lower().endswith(".shp"):
        raise ValueError(f"HAZARD_POINTS_FILENAME must end with .shp: {filename}")
    if not os.path.isfile(shp_path):
        raise FileNotFoundError(f"Hazard points Shapefile not found: {shp_path}")
    return shp_path


@router.post("/hazard-points/scan", status_code=202)
async def scan_hazard_points_endpoint(background_tasks: BackgroundTasks, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    扫描预定义路径下的地质灾害点 Shapefile。
    """
    try:
        shp_path = _resolve_hazard_shp_path()
        task_id = await task_service.create_task("SCAN_HAZARD", "灾害点数据同步")
        await job_queue_service.create_job("SCAN_HAZARD", payload={}, task_id=task_id)
        return {"message": "灾害点同步任务已进入队列", "task_id": task_id}
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/hazard-points", response_model=List[HazardPoint])
async def get_hazard_points_endpoint(db: AsyncSession = Depends(get_db)):
    """
    获取所有地质灾害点。
    """
    result = await db.execute(select(HazardPointORM))
    return result.scalars().all()
