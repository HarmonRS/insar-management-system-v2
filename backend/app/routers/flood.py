"""Flood disaster analysis router.

This router exposes the flood-analysis business API while reusing the
existing water/flood processing records and jobs during migration.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from . import water as water_compat
from .dependencies import _get_current_user, _require_admin

router = APIRouter()


class FloodPreprocessRequest(BaseModel):
    radar_data_id: int = Field(..., description="RadarDataORM 主键")


class FloodWaterExtractionRequest(BaseModel):
    scene_id: Optional[int] = Field(default=None, description="SARSceneGeoORM 主键")
    input_path: Optional[str] = Field(default=None, description="直接指定 GeoTIFF/ENVI 路径")


class FloodPairSearchRequest(BaseModel):
    pre_start: Optional[str] = Field(default=None, description="灾前开始日期 YYYYMMDD")
    pre_end: Optional[str] = Field(default=None, description="灾前结束日期 YYYYMMDD")
    post_start: Optional[str] = Field(default=None, description="灾后开始日期 YYYYMMDD")
    post_end: Optional[str] = Field(default=None, description="灾后结束日期 YYYYMMDD")
    overlap_threshold: float = Field(default=0.3, ge=0.0, le=1.0, description="最小重叠比例")


class FloodDetectionRequest(BaseModel):
    pre_scene_id: int = Field(..., description="灾前 SARSceneGeoORM 主键")
    post_scene_id: int = Field(..., description="灾后 SARSceneGeoORM 主键")
    refine: bool = Field(default=False, description="是否启用 MRF 精化")


@router.post("/flood/preprocess", status_code=202)
async def submit_flood_preprocess(
    req: FloodPreprocessRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """提交水体提取前置处理任务，当前复用旧 water geocode 链路。"""
    return await water_compat.submit_geocode(req, db=db, admin_user=admin_user)


@router.get("/flood/scenes")
async def list_flood_scenes(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    """列出可作为水体提取输入的地理编码场景。"""
    return await water_compat.list_scenes(limit=limit, offset=offset, db=db, current_user=current_user)


@router.get("/flood/scenes/done-radar-ids")
async def list_flood_done_radar_ids(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await water_compat.list_done_scene_radar_ids(db=db, current_user=current_user)


@router.get("/flood/scenes/active-radar-ids")
async def list_flood_active_radar_ids(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await water_compat.list_active_scene_radar_ids(db=db, current_user=current_user)


@router.post("/flood/scenes/{scene_id}/reset")
async def reset_flood_scene(
    scene_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    return await water_compat.reset_scene_status(scene_id=scene_id, db=db, admin_user=admin_user)


@router.post("/flood/water-extractions", status_code=202)
async def submit_flood_water_extraction(
    req: FloodWaterExtractionRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """提交单景水体提取任务，当前复用 Otsu 快速水体检测实现。"""
    return await water_compat.submit_water_detect(req, db=db, admin_user=admin_user)


@router.get("/flood/water-extractions")
async def list_flood_water_extractions(
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await water_compat.list_water_detections(
        limit=limit,
        offset=offset,
        status=status,
        db=db,
        current_user=current_user,
    )


@router.get("/flood/water-extractions/{extraction_id}/preview")
async def get_flood_water_extraction_preview(
    extraction_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await water_compat.get_water_detection_preview(
        detection_id=extraction_id,
        db=db,
        current_user=current_user,
    )


@router.post("/flood/pairs/search")
async def search_flood_pairs(
    req: FloodPairSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await water_compat.find_water_pairs(req, db=db, current_user=current_user)


@router.post("/flood/detections", status_code=202)
async def submit_flood_detection(
    req: FloodDetectionRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    return await water_compat.submit_flood_detect(req, db=db, admin_user=admin_user)


@router.get("/flood/detections")
async def list_flood_detections(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await water_compat.list_flood_events(db=db, current_user=current_user)


@router.get("/flood/detections/{detection_id}/preview/{layer}")
async def get_flood_detection_preview(
    detection_id: int,
    layer: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    normalized = layer.strip().lower()
    if normalized == "pre":
        return await water_compat.flood_event_pre_preview(
            event_id=detection_id,
            db=db,
            current_user=current_user,
        )
    if normalized == "post":
        return await water_compat.flood_event_post_preview(
            event_id=detection_id,
            db=db,
            current_user=current_user,
        )
    if normalized == "classified":
        return await water_compat.flood_event_classified_preview(
            event_id=detection_id,
            db=db,
            current_user=current_user,
        )
    raise HTTPException(status_code=404, detail=f"不支持的洪涝预览图层: {layer}")
