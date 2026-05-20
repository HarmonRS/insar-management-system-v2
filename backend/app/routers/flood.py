"""Flood disaster analysis router."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..services import flood_analysis_service
from ..services import flood_overlay_service
from ..services import flood_product_service
from .dependencies import _get_current_user, _require_admin

router = APIRouter()


class FloodPreprocessRequest(BaseModel):
    radar_data_id: int = Field(..., description="RadarDataORM primary key")


class FloodWaterExtractionRequest(BaseModel):
    scene_id: Optional[int] = Field(default=None, description="SARSceneGeoORM primary key")
    input_path: Optional[str] = Field(default=None, description="Direct GeoTIFF/ENVI input path")


class FloodPairSearchRequest(BaseModel):
    pre_start: Optional[str] = Field(default=None, description="Pre-flood start date in YYYYMMDD")
    pre_end: Optional[str] = Field(default=None, description="Pre-flood end date in YYYYMMDD")
    post_start: Optional[str] = Field(default=None, description="Post-flood start date in YYYYMMDD")
    post_end: Optional[str] = Field(default=None, description="Post-flood end date in YYYYMMDD")
    overlap_threshold: float = Field(default=0.3, ge=0.0, le=1.0, description="Minimum overlap ratio")


class FloodDisasterPairSearchRequest(BaseModel):
    disaster_name: Optional[str] = Field(default=None, description="Disaster/event display name")
    disaster_date: str = Field(..., description="Disaster date in YYYYMMDD")
    region_tree_id: Optional[str] = Field(default=None, description="AOI admin region tree id")
    aoi_geojson: Optional[dict[str, Any]] = Field(default=None, description="AOI GeoJSON FeatureCollection")
    pre_window_days: int = Field(default=30, ge=1, le=365)
    post_window_days: int = Field(default=30, ge=1, le=365)
    min_aoi_coverage_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    min_pair_overlap_ratio: float = Field(default=0.3, ge=0.0, le=1.0)
    max_pairs: int = Field(default=50, ge=1, le=200)
    satellites: Optional[list[str]] = None
    polarization: Optional[str] = None
    imaging_mode: Optional[str] = None
    product_level: Optional[str] = None
    require_same_polarization: bool = True
    require_same_imaging_mode: bool = False


class FloodDetectionRequest(BaseModel):
    pre_scene_id: int = Field(..., description="Pre-flood SARSceneGeoORM primary key")
    post_scene_id: int = Field(..., description="Post-flood SARSceneGeoORM primary key")
    refine: bool = Field(default=False, description="Enable MRF refinement")


class FloodOverlayRequest(BaseModel):
    near_threshold_m: float = Field(default=500.0, ge=0.0, le=10000.0, description="Near-flood threshold in meters")


@router.post("/flood/preprocess", status_code=202)
async def submit_flood_preprocess(
    req: FloodPreprocessRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    return await flood_analysis_service.submit_geocode_job(req, db=db)


@router.get("/flood/scenes")
async def list_flood_scenes(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.list_scenes(limit=limit, offset=offset, db=db)


@router.get("/flood/scenes/done-radar-ids")
async def list_flood_done_radar_ids(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.list_done_scene_radar_ids(db=db)


@router.get("/flood/scenes/active-radar-ids")
async def list_flood_active_radar_ids(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.list_active_scene_radar_ids(db=db)


@router.post("/flood/scenes/{scene_id}/reset")
async def reset_flood_scene(
    scene_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    return await flood_analysis_service.reset_scene_status(scene_id=scene_id, db=db)


@router.post("/flood/water-extractions", status_code=202)
async def submit_flood_water_extraction(
    req: FloodWaterExtractionRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    return await flood_analysis_service.submit_water_extraction(req, db=db)


@router.get("/flood/water-extractions")
async def list_flood_water_extractions(
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.list_water_extractions(
        limit=limit,
        offset=offset,
        status=status,
        db=db,
    )


@router.get("/flood/water-extractions/{extraction_id}/preview")
async def get_flood_water_extraction_preview(
    extraction_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.get_water_extraction_preview(extraction_id=extraction_id, db=db)


@router.post("/flood/pairs/search")
async def search_flood_pairs(
    req: FloodPairSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.search_pairs(req, db=db)


@router.post("/flood/disaster-pairs/search")
async def search_flood_disaster_pairs(
    req: FloodDisasterPairSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.search_disaster_pairs(req, db=db)


@router.post("/flood/detections", status_code=202)
async def submit_flood_detection(
    req: FloodDetectionRequest,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    return await flood_analysis_service.submit_flood_detection(req, db=db)


@router.get("/flood/detections")
async def list_flood_detections(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.list_flood_detections(db=db)


@router.get("/flood/detections/{detection_id}/preview/{layer}")
async def get_flood_detection_preview(
    detection_id: int,
    layer: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_analysis_service.get_flood_detection_preview(
        detection_id=detection_id,
        layer=layer,
        db=db,
    )


@router.post("/flood/detections/{detection_id}/overlay", status_code=201)
async def run_flood_overlay(
    detection_id: int,
    req: FloodOverlayRequest | None = None,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    threshold = req.near_threshold_m if req else 500.0
    return await flood_overlay_service.run_overlay(
        detection_id=detection_id,
        db=db,
        near_threshold_m=threshold,
    )


@router.get("/flood/detections/{detection_id}/impact")
async def get_flood_impact(
    detection_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_overlay_service.get_overlay_result(detection_id=detection_id, db=db)


@router.post("/flood/detections/{detection_id}/products", status_code=201)
async def create_flood_product(
    detection_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    return await flood_product_service.create_flood_product_for_detection(detection_id=detection_id, db=db)


@router.get("/flood/products")
async def list_flood_products(
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_product_service.list_flood_products(
        db=db,
        limit=limit,
        offset=offset,
        status=status,
    )


@router.get("/flood/products/{product_id}")
async def get_flood_product(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_product_service.get_flood_product(product_id_or_pk=product_id, db=db)


@router.get("/flood/products/{product_id}/manifest")
async def get_flood_product_manifest(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_product_service.get_flood_product_manifest(product_id_or_pk=product_id, db=db)


@router.get("/flood/results")
async def list_flood_results(
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_product_service.list_flood_products(
        db=db,
        limit=limit,
        offset=offset,
        status=status,
    )


@router.get("/flood/results/{product_id}")
async def get_flood_result(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_product_service.get_flood_product(product_id_or_pk=product_id, db=db)


@router.get("/flood/results/{product_id}/manifest")
async def get_flood_result_manifest(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    return await flood_product_service.get_flood_product_manifest(product_id_or_pk=product_id, db=db)
