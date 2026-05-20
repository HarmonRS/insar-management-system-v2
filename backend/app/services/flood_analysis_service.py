"""Flood-analysis service layer.

This module owns the flood business API implementation used by
``backend.app.routers.flood``. It intentionally does not import the legacy
water router; the old router remains only as a compatibility surface.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import FloodDetectionORM, RadarDataORM, SARSceneGeoORM, WaterDetectionORM, WaterExtractionORM
from ..services.job_handlers import (
    JOB_TYPE_FLOOD_DETECTION,
    JOB_TYPE_SAR_SCENE_PREPROCESS,
    JOB_TYPE_WATER_DETECT,
)
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from ..utils import normalize_satellite_family

_FLOOD_JOB_MAX_ATTEMPTS = 3


async def _queue_flood_job(
    *,
    job_type: str,
    task_type: str,
    task_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        task_id = await task_service.create_task(
            task_type=task_type,
            task_name=task_name,
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=job_type,
            payload=payload,
            task_id=task_id,
            max_attempts=_FLOOD_JOB_MAX_ATTEMPTS,
        )
        return {"task_id": task_id, "job_id": job_id, "job_type": job_type, "message": "Job queued."}
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(status_code=409 if "conflict" in message.lower() else 400, detail=message) from exc


def _overlap_ratio(poly_a: Any, poly_b: Any) -> float:
    try:
        import json
        from shapely.geometry import Polygon, shape

        def _to_geom(poly: Any):
            if isinstance(poly, str):
                poly = json.loads(poly)
            if isinstance(poly, list):
                return Polygon(poly)
            return shape(poly)

        a = _to_geom(poly_a)
        b = _to_geom(poly_b)
        if not a.is_valid or not b.is_valid:
            return 0.0
        intersection_area = a.intersection(b).area
        smaller_area = min(a.area, b.area)
        return intersection_area / smaller_area if smaller_area > 0 else 0.0
    except Exception:
        return 0.0


def _parse_ymd(value: str | None, *, field: str) -> datetime:
    try:
        normalized = str(value or "").replace("-", "").strip()
        return datetime.strptime(normalized, "%Y%m%d")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYYMMDD") from exc


def _format_ymd(value: datetime) -> str:
    return value.strftime("%Y%m%d")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _same_text(left: Any, right: Any) -> bool:
    left_text = str(left or "").strip().lower()
    right_text = str(right or "").strip().lower()
    if not left_text or not right_text:
        return True
    return left_text == right_text


def _feature_collection_name(feature_collection: dict[str, Any]) -> str | None:
    try:
        features = feature_collection.get("features") or []
        properties = features[0].get("properties") or {}
        return properties.get("name") or properties.get("NAME") or properties.get("treeID")
    except Exception:
        return None


def _preprocess_engine_for_radar(radar: RadarDataORM) -> str | None:
    family = str(normalize_satellite_family(radar.satellite_family or radar.satellite) or "").upper()
    if family == "GF3":
        return "gf3_gdal"
    if family == "LT1":
        return "lt_gamma"
    return None


def _scene_analysis_path(scene: SARSceneGeoORM | None) -> str | None:
    if not scene:
        return None
    return scene.analysis_tif_path


def _resolve_aoi_wkt_from_request(req: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Resolve region/GeoJSON AOI using the same parser as the management search page."""
    aoi_geojson = getattr(req, "aoi_geojson", None)
    region_tree_id = getattr(req, "region_tree_id", None)

    if aoi_geojson:
        feature_collection = aoi_geojson
        source = "geojson"
    elif region_tree_id:
        from ..routers.dependencies import _resolve_region_aoi_payload

        payload = _resolve_region_aoi_payload(str(region_tree_id))
        feature_collection = payload.get("aoi_geojson") or payload
        source = "region"
    else:
        raise HTTPException(status_code=400, detail="region_tree_id or aoi_geojson is required")

    from ..routers.dependencies import _parse_aoi_geojson_form_value

    parsed = _parse_aoi_geojson_form_value(json.dumps(feature_collection, ensure_ascii=False))
    if not parsed:
        raise HTTPException(status_code=400, detail="AOI geometry is empty")
    aoi_wkt, normalized_feature_collection = parsed
    meta = {
        "source": source,
        "region_tree_id": region_tree_id,
        "name": _feature_collection_name(normalized_feature_collection),
    }
    return aoi_wkt, normalized_feature_collection, meta


def _radar_scene_item(scene: SARSceneGeoORM, radar: RadarDataORM, *, aoi_coverage_ratio: float | None = None) -> dict[str, Any]:
    return {
        "id": scene.id,
        "scene_id": scene.id,
        "radar_data_id": scene.radar_data_id,
        "satellite": radar.satellite,
        "imaging_date": radar.imaging_date,
        "acquisition_time_utc": radar.acquisition_time_utc,
        "imaging_mode": radar.imaging_mode,
        "product_level": radar.product_level,
        "polarization": radar.polarization,
        "orbit_direction": radar.orbit_direction,
        "geo_path": scene.geo_path,
        "analysis_tif_path": scene.analysis_tif_path,
        "analysis_dir": scene.analysis_dir,
        "analysis_preview_path": scene.analysis_preview_path,
        "analysis_engine": scene.analysis_engine,
        "analysis_profile": scene.analysis_profile,
        "analysis_backscatter_unit": scene.analysis_backscatter_unit,
        "analysis_quality_json": scene.analysis_quality_json,
        "coverage_polygon": radar.coverage_polygon,
        "min_lat": radar.min_lat,
        "max_lat": radar.max_lat,
        "min_lon": radar.min_lon,
        "max_lon": radar.max_lon,
        "aoi_coverage_ratio": aoi_coverage_ratio,
    }


async def _query_disaster_scene_pool(
    *,
    db: AsyncSession,
    req: Any,
    aoi_wkt: str,
    start_ymd: str,
    end_ymd: str,
    min_aoi_coverage_ratio: float,
    descending: bool,
) -> list[dict[str, Any]]:
    aoi_geom = func.ST_GeomFromText(aoi_wkt, 4326)
    aoi_area = func.ST_Area(func.Geography(aoi_geom))
    coverage_expr = (
        func.ST_Area(func.Geography(func.ST_Intersection(RadarDataORM.geom, aoi_geom)))
        / func.nullif(aoi_area, 0)
    ).label("aoi_coverage_ratio")

    filters = [
        SARSceneGeoORM.status == "DONE",
        SARSceneGeoORM.analysis_tif_path.isnot(None),
        RadarDataORM.geom.isnot(None),
        RadarDataORM.imaging_date.isnot(None),
        RadarDataORM.imaging_date >= start_ymd,
        RadarDataORM.imaging_date <= end_ymd,
        func.ST_Intersects(RadarDataORM.geom, aoi_geom),
    ]

    satellites = [str(item).strip() for item in (getattr(req, "satellites", None) or []) if str(item).strip()]
    if satellites:
        filters.append(RadarDataORM.satellite.in_(satellites))

    polarization = str(getattr(req, "polarization", "") or "").strip()
    if polarization:
        filters.append(RadarDataORM.polarization.ilike(f"%{polarization}%"))

    imaging_mode = str(getattr(req, "imaging_mode", "") or "").strip()
    if imaging_mode:
        filters.append(RadarDataORM.imaging_mode == imaging_mode)

    product_level = str(getattr(req, "product_level", "") or "").strip()
    if product_level:
        filters.append(RadarDataORM.product_level == product_level)

    order_by = RadarDataORM.imaging_date.desc() if descending else RadarDataORM.imaging_date.asc()
    result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM, coverage_expr)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(*filters)
        .order_by(order_by, SARSceneGeoORM.id.desc())
    )

    pool: list[dict[str, Any]] = []
    for scene, radar, aoi_coverage_ratio in result.all():
        coverage_ratio = max(0.0, min(1.0, _to_float(aoi_coverage_ratio)))
        if coverage_ratio < min_aoi_coverage_ratio:
            continue
        pool.append(_radar_scene_item(scene, radar, aoi_coverage_ratio=round(coverage_ratio, 4)))
    return pool


async def submit_geocode_job(req: Any, db: AsyncSession) -> dict[str, Any]:
    radar = await db.get(RadarDataORM, req.radar_data_id)
    if not radar:
        raise HTTPException(status_code=404, detail=f"RadarData id={req.radar_data_id} not found")

    result = await db.execute(
        select(SARSceneGeoORM)
        .where(SARSceneGeoORM.radar_data_id == req.radar_data_id)
        .with_for_update(skip_locked=True)
    )
    scene = result.scalar_one_or_none()
    if scene and scene.status in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=409, detail="Scene already has an active geocode job")
    if not scene:
        scene = SARSceneGeoORM(radar_data_id=req.radar_data_id, status="PENDING")
        db.add(scene)
        await db.flush()
    else:
        scene.status = "PENDING"
        scene.error_msg = None
    await db.flush()
    scene_id = scene.id
    await db.commit()

    engine = _preprocess_engine_for_radar(radar)
    if not engine:
        async with db.begin():
            failed_scene = await db.get(SARSceneGeoORM, scene_id)
            if failed_scene and failed_scene.status == "PENDING":
                failed_scene.status = "FAILED"
                failed_scene.error_msg = "No analysis-ready GeoTIFF preprocessor configured for this satellite"
        raise HTTPException(
            status_code=400,
            detail="洪涝模块不再使用 ENVI 兜底预处理；该卫星暂未配置 analysis-ready GeoTIFF 预处理器",
        )

    job_type = JOB_TYPE_SAR_SCENE_PREPROCESS
    task_type = f"FLOOD_SCENE_PREPROCESS_{scene_id}"
    task_name = f"Flood analysis-ready preprocess radar_id={req.radar_data_id} engine={engine}"
    payload = {"scene_id": scene_id, "radar_data_id": req.radar_data_id}
    payload["engine"] = engine

    try:
        return await _queue_flood_job(
            job_type=job_type,
            task_type=task_type,
            task_name=task_name,
            payload=payload,
        )
    except HTTPException:
        async with db.begin():
            failed_scene = await db.get(SARSceneGeoORM, scene_id)
            if failed_scene and failed_scene.status == "PENDING":
                failed_scene.status = "FAILED"
                failed_scene.error_msg = "Job queue failed"
        raise


async def reset_scene_status(scene_id: int, db: AsyncSession) -> dict[str, Any]:
    scene = await db.get(SARSceneGeoORM, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail=f"Scene id={scene_id} not found")
    if scene.status not in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=400, detail=f"Scene status is {scene.status}; reset is not needed")
    scene.status = "FAILED"
    scene.error_msg = "Manually reset"
    await db.commit()
    return {"id": scene_id, "status": "FAILED", "message": "Scene reset"}


async def list_done_scene_radar_ids(db: AsyncSession) -> dict[str, list[int]]:
    result = await db.execute(
        select(SARSceneGeoORM.radar_data_id).where(
            SARSceneGeoORM.status == "DONE",
            SARSceneGeoORM.analysis_tif_path.isnot(None),
        )
    )
    return {"ids": [row for (row,) in result.all()]}


async def list_active_scene_radar_ids(db: AsyncSession) -> dict[str, list[int]]:
    result = await db.execute(
        select(SARSceneGeoORM.radar_data_id).where(SARSceneGeoORM.status.in_(["PENDING", "RUNNING"]))
    )
    return {"ids": [row for (row,) in result.all()]}


async def list_scenes(limit: int, offset: int, db: AsyncSession) -> dict[str, Any]:
    total_result = await db.execute(select(func.count()).select_from(SARSceneGeoORM))
    total = total_result.scalar_one()

    result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .order_by(SARSceneGeoORM.id.desc())
        .limit(limit)
        .offset(offset)
    )

    items = []
    for scene, radar in result.all():
        items.append(
            {
                "id": scene.id,
                "radar_data_id": scene.radar_data_id,
                "satellite": radar.satellite,
                "imaging_date": radar.imaging_date,
                "acquisition_time_utc": radar.acquisition_time_utc,
                "imaging_mode": radar.imaging_mode,
                "product_level": radar.product_level,
                "polarization": radar.polarization,
                "orbit_direction": radar.orbit_direction,
                "geo_path": scene.geo_path,
                "analysis_tif_path": scene.analysis_tif_path,
                "analysis_dir": scene.analysis_dir,
                "analysis_preview_path": scene.analysis_preview_path,
                "analysis_engine": scene.analysis_engine,
                "analysis_profile": scene.analysis_profile,
                "analysis_backscatter_unit": scene.analysis_backscatter_unit,
                "analysis_nodata_value": scene.analysis_nodata_value,
                "analysis_metadata_json": scene.analysis_metadata_json,
                "analysis_quality_json": scene.analysis_quality_json,
                "pixel_size_m": scene.pixel_size_m,
                "status": scene.status,
                "error_msg": scene.error_msg,
                "created_at": scene.created_at.isoformat() if scene.created_at else None,
                "coverage_polygon": radar.coverage_polygon,
                "min_lat": radar.min_lat,
                "max_lat": radar.max_lat,
                "min_lon": radar.min_lon,
                "max_lon": radar.max_lon,
            }
        )
    return {"items": items, "total": total}


async def submit_water_extraction(req: Any, db: AsyncSession) -> dict[str, Any]:
    input_path = req.input_path
    scene_id = req.scene_id

    if scene_id:
        scene = await db.get(SARSceneGeoORM, scene_id)
        if not scene:
            raise HTTPException(status_code=404, detail=f"SARSceneGeoORM id={scene_id} not found")
        input_path = _scene_analysis_path(scene)
        if not input_path:
            raise HTTPException(status_code=400, detail="Scene has no analysis-ready GeoTIFF")

    if not input_path:
        raise HTTPException(status_code=400, detail="scene_id or input_path is required")

    extraction = WaterExtractionORM(
        scene_id=scene_id,
        processor=getattr(req, "processor", None) or "otsu",
        input_path=input_path,
        status="PENDING",
    )
    db.add(extraction)
    await db.flush()
    extraction_id = extraction.id
    await db.commit()

    try:
        queued = await _queue_flood_job(
            job_type=JOB_TYPE_WATER_DETECT,
            task_type=f"FLOOD_WATER_EXTRACTION_{extraction_id}",
            task_name=f"Flood water extraction id={extraction_id}",
            payload={"extraction_id": extraction_id, "processor": extraction.processor},
        )
        async with db.begin():
            queued_extraction = await db.get(WaterExtractionORM, extraction_id)
            if queued_extraction:
                queued_extraction.task_id = queued.get("task_id")
        return queued
    except HTTPException:
        async with db.begin():
            failed_extraction = await db.get(WaterExtractionORM, extraction_id)
            if failed_extraction and failed_extraction.status == "PENDING":
                failed_extraction.status = "FAILED"
                failed_extraction.error_msg = "Job queue failed"
        raise


async def list_water_extractions(
    *,
    limit: int,
    offset: int,
    status: str | None,
    db: AsyncSession,
) -> dict[str, Any]:
    count_query = select(func.count()).select_from(WaterExtractionORM)
    if status:
        count_query = count_query.where(WaterExtractionORM.status == status)
    total = (await db.execute(count_query)).scalar_one()

    query = (
        select(WaterExtractionORM, SARSceneGeoORM, RadarDataORM)
        .outerjoin(SARSceneGeoORM, WaterExtractionORM.scene_id == SARSceneGeoORM.id)
        .outerjoin(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .order_by(WaterExtractionORM.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if status:
        query = query.where(WaterExtractionORM.status == status)
    rows = (await db.execute(query)).all()

    items = []
    for detection, scene, radar in rows:
        items.append(
            {
                "id": detection.id,
                "scene_id": detection.scene_id,
                "processor": detection.processor,
                "task_id": detection.task_id,
                "radar_data_id": scene.radar_data_id if scene else None,
                "satellite": radar.satellite if radar else None,
                "imaging_date": radar.imaging_date if radar else None,
                "acquisition_time_utc": radar.acquisition_time_utc if radar else None,
                "imaging_mode": radar.imaging_mode if radar else None,
                "product_level": radar.product_level if radar else None,
                "polarization": radar.polarization if radar else None,
                "orbit_direction": radar.orbit_direction if radar else None,
                "coverage_polygon": radar.coverage_polygon if radar else None,
                "min_lat": radar.min_lat if radar else None,
                "max_lat": radar.max_lat if radar else None,
                "min_lon": radar.min_lon if radar else None,
                "max_lon": radar.max_lon if radar else None,
                "input_path": detection.input_path,
                "output_path": detection.output_path,
                "preview_path": detection.preview_path,
                "vector_path": detection.vector_path,
                "water_area_km2": detection.water_area_km2,
                "water_pixel_count": detection.water_pixel_count,
                "otsu_threshold_db": detection.threshold_value,
                "threshold_value": detection.threshold_value,
                "metadata_json": detection.metadata_json,
                "status": detection.status,
                "error_msg": detection.error_msg,
                "created_at": detection.created_at.isoformat() if detection.created_at else None,
                "updated_at": detection.updated_at.isoformat() if detection.updated_at else None,
            }
        )
    return {"items": items, "total": total}


async def submit_flood_detection(req: Any, db: AsyncSession) -> dict[str, Any]:
    pre_scene = await db.get(SARSceneGeoORM, req.pre_scene_id)
    post_scene = await db.get(SARSceneGeoORM, req.post_scene_id)
    if not pre_scene:
        raise HTTPException(status_code=404, detail=f"Pre-scene id={req.pre_scene_id} not found")
    if not post_scene:
        raise HTTPException(status_code=404, detail=f"Post-scene id={req.post_scene_id} not found")
    if pre_scene.status != "DONE":
        raise HTTPException(status_code=400, detail=f"Pre-scene is not DONE: {pre_scene.status}")
    if post_scene.status != "DONE":
        raise HTTPException(status_code=400, detail=f"Post-scene is not DONE: {post_scene.status}")
    if not pre_scene.analysis_tif_path:
        raise HTTPException(status_code=400, detail="Pre-scene has no analysis-ready GeoTIFF")
    if not post_scene.analysis_tif_path:
        raise HTTPException(status_code=400, detail="Post-scene has no analysis-ready GeoTIFF")

    result = await db.execute(
        select(FloodDetectionORM)
        .where(
            FloodDetectionORM.pre_scene_id == req.pre_scene_id,
            FloodDetectionORM.post_scene_id == req.post_scene_id,
        )
        .with_for_update(skip_locked=True)
    )
    detection = result.scalar_one_or_none()
    if detection and detection.status in ("PENDING", "RUNNING"):
        raise HTTPException(status_code=409, detail="Pair already has an active flood-detection job")
    if not detection:
        detection = FloodDetectionORM(
            pre_scene_id=req.pre_scene_id,
            post_scene_id=req.post_scene_id,
            status="PENDING",
        )
        db.add(detection)
        await db.flush()
    else:
        detection.status = "PENDING"
        detection.error_msg = None
    await db.flush()
    detection_id = detection.id
    await db.commit()

    try:
        return await _queue_flood_job(
            job_type=JOB_TYPE_FLOOD_DETECTION,
            task_type=f"FLOOD_DETECTION_{detection_id}",
            task_name=f"GeoTIFF flood detection pre={req.pre_scene_id} post={req.post_scene_id}",
            payload={"detection_id": detection_id, "refine": req.refine},
        )
    except HTTPException:
        async with db.begin():
            failed_detection = await db.get(FloodDetectionORM, detection_id)
            if failed_detection and failed_detection.status == "PENDING":
                failed_detection.status = "FAILED"
                failed_detection.error_msg = "Job queue failed"
        raise


async def list_flood_detections(db: AsyncSession) -> dict[str, Any]:
    result = await db.execute(
        select(FloodDetectionORM)
        .options(
            selectinload(FloodDetectionORM.pre_scene).selectinload(SARSceneGeoORM.radar_data),
            selectinload(FloodDetectionORM.post_scene).selectinload(SARSceneGeoORM.radar_data),
        )
        .order_by(FloodDetectionORM.id.desc())
    )
    detections = result.scalars().all()

    items = []
    for detection in detections:
        pre_radar = detection.pre_scene.radar_data if detection.pre_scene else None
        post_radar = detection.post_scene.radar_data if detection.post_scene else None
        items.append(
            {
                "id": detection.id,
                "pre_scene_id": detection.pre_scene_id,
                "post_scene_id": detection.post_scene_id,
                "pre_imaging_date": pre_radar.imaging_date if pre_radar else None,
                "post_imaging_date": post_radar.imaging_date if post_radar else None,
                "pre_satellite": pre_radar.satellite if pre_radar else None,
                "post_satellite": post_radar.satellite if post_radar else None,
                "pre_geo_path": _scene_analysis_path(detection.pre_scene),
                "post_geo_path": _scene_analysis_path(detection.post_scene),
                "pre_analysis_tif_path": _scene_analysis_path(detection.pre_scene),
                "post_analysis_tif_path": _scene_analysis_path(detection.post_scene),
                "classified_path": detection.classified_path,
                "flood_area_km2": detection.flood_area_km2,
                "stable_water_area_km2": detection.stable_water_area_km2,
                "status": detection.status,
                "error_msg": detection.error_msg,
                "created_at": detection.created_at.isoformat() if detection.created_at else None,
                "updated_at": detection.updated_at.isoformat() if detection.updated_at else None,
            }
        )
    return {"items": items, "total": len(items)}


async def search_pairs(req: Any, db: AsyncSession) -> dict[str, Any]:
    pre_filters = [SARSceneGeoORM.status == "DONE", SARSceneGeoORM.analysis_tif_path.isnot(None)]
    if req.pre_start:
        pre_filters.append(RadarDataORM.imaging_date >= req.pre_start)
    if req.pre_end:
        pre_filters.append(RadarDataORM.imaging_date <= req.pre_end)
    pre_result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(*pre_filters)
    )

    post_filters = [SARSceneGeoORM.status == "DONE", SARSceneGeoORM.analysis_tif_path.isnot(None)]
    if req.post_start:
        post_filters.append(RadarDataORM.imaging_date >= req.post_start)
    if req.post_end:
        post_filters.append(RadarDataORM.imaging_date <= req.post_end)
    post_result = await db.execute(
        select(SARSceneGeoORM, RadarDataORM)
        .join(RadarDataORM, SARSceneGeoORM.radar_data_id == RadarDataORM.id)
        .where(*post_filters)
    )

    candidates = []
    for pre_scene, pre_radar in pre_result.all():
        for post_scene, post_radar in post_result.all():
            if pre_scene.id == post_scene.id:
                continue
            ratio = 0.0
            if pre_radar.coverage_polygon and post_radar.coverage_polygon:
                ratio = _overlap_ratio(pre_radar.coverage_polygon, post_radar.coverage_polygon)
            if ratio < req.overlap_threshold:
                continue
            try:
                pre_date = datetime.strptime(pre_radar.imaging_date, "%Y%m%d")
                post_date = datetime.strptime(post_radar.imaging_date, "%Y%m%d")
                time_diff = abs((post_date - pre_date).days)
            except Exception:
                time_diff = None
            candidates.append(
                {
                    "pre": {
                        "id": pre_scene.id,
                        "imaging_date": pre_radar.imaging_date,
                        "satellite": pre_radar.satellite,
                        "geo_path": _scene_analysis_path(pre_scene),
                        "analysis_tif_path": _scene_analysis_path(pre_scene),
                    },
                    "post": {
                        "id": post_scene.id,
                        "imaging_date": post_radar.imaging_date,
                        "satellite": post_radar.satellite,
                        "geo_path": _scene_analysis_path(post_scene),
                        "analysis_tif_path": _scene_analysis_path(post_scene),
                    },
                    "overlap_ratio": round(ratio, 4),
                    "time_diff_days": time_diff,
                }
            )

    candidates.sort(key=lambda item: item["overlap_ratio"], reverse=True)
    used_pre: set[int] = set()
    used_post: set[int] = set()
    pairs = []
    for candidate in candidates:
        pre_id = candidate["pre"]["id"]
        post_id = candidate["post"]["id"]
        if pre_id in used_pre or post_id in used_post:
            continue
        used_pre.add(pre_id)
        used_post.add(post_id)
        pairs.append(candidate)

    pairs.sort(key=lambda item: item["overlap_ratio"], reverse=True)
    return {"pairs": pairs, "total": len(pairs)}


async def search_disaster_pairs(req: Any, db: AsyncSession) -> dict[str, Any]:
    disaster_date = _parse_ymd(req.disaster_date, field="disaster_date")
    pre_window_days = max(1, int(getattr(req, "pre_window_days", 30) or 30))
    post_window_days = max(1, int(getattr(req, "post_window_days", 30) or 30))
    min_aoi_coverage_ratio = max(0.0, min(1.0, float(getattr(req, "min_aoi_coverage_ratio", 0.2) or 0.0)))
    min_pair_overlap_ratio = max(0.0, min(1.0, float(getattr(req, "min_pair_overlap_ratio", 0.3) or 0.0)))
    max_pairs = max(1, min(200, int(getattr(req, "max_pairs", 50) or 50)))

    aoi_wkt, aoi_geojson, aoi_meta = _resolve_aoi_wkt_from_request(req)
    pre_start = disaster_date - timedelta(days=pre_window_days)
    pre_end = disaster_date - timedelta(days=1)
    post_start = disaster_date
    post_end = disaster_date + timedelta(days=post_window_days)

    pre_pool = await _query_disaster_scene_pool(
        db=db,
        req=req,
        aoi_wkt=aoi_wkt,
        start_ymd=_format_ymd(pre_start),
        end_ymd=_format_ymd(pre_end),
        min_aoi_coverage_ratio=min_aoi_coverage_ratio,
        descending=True,
    )
    post_pool = await _query_disaster_scene_pool(
        db=db,
        req=req,
        aoi_wkt=aoi_wkt,
        start_ymd=_format_ymd(post_start),
        end_ymd=_format_ymd(post_end),
        min_aoi_coverage_ratio=min_aoi_coverage_ratio,
        descending=False,
    )

    candidates: list[dict[str, Any]] = []
    require_same_polarization = bool(getattr(req, "require_same_polarization", True))
    require_same_imaging_mode = bool(getattr(req, "require_same_imaging_mode", False))
    total_window = max(1, pre_window_days + post_window_days)

    for pre in pre_pool:
        pre_date = _parse_ymd(pre.get("imaging_date"), field="pre.imaging_date")
        for post in post_pool:
            if pre["id"] == post["id"]:
                continue
            if require_same_polarization and not _same_text(pre.get("polarization"), post.get("polarization")):
                continue
            if require_same_imaging_mode and not _same_text(pre.get("imaging_mode"), post.get("imaging_mode")):
                continue

            post_date = _parse_ymd(post.get("imaging_date"), field="post.imaging_date")
            scene_overlap = _overlap_ratio(pre.get("coverage_polygon"), post.get("coverage_polygon"))
            if scene_overlap < min_pair_overlap_ratio:
                continue

            pre_delta_days = max(0, (disaster_date - pre_date).days)
            post_delta_days = max(0, (post_date - disaster_date).days)
            time_score = max(0.0, 1.0 - ((pre_delta_days + post_delta_days) / total_window))
            aoi_score = min(_to_float(pre.get("aoi_coverage_ratio")), _to_float(post.get("aoi_coverage_ratio")))
            score = (scene_overlap * 0.45) + (aoi_score * 0.35) + (time_score * 0.20)
            candidates.append(
                {
                    "pre": pre,
                    "post": post,
                    "overlap_ratio": round(scene_overlap, 4),
                    "aoi_coverage_ratio": round(aoi_score, 4),
                    "time_score": round(time_score, 4),
                    "score": round(score, 4),
                    "pre_delta_days": pre_delta_days,
                    "post_delta_days": post_delta_days,
                    "time_diff_days": max(0, (post_date - pre_date).days),
                    "same_polarization": _same_text(pre.get("polarization"), post.get("polarization")),
                    "same_imaging_mode": _same_text(pre.get("imaging_mode"), post.get("imaging_mode")),
                }
            )

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["overlap_ratio"],
            item["aoi_coverage_ratio"],
            -item["time_diff_days"],
        ),
        reverse=True,
    )
    selected_pairs = candidates[:max_pairs]

    warnings: list[str] = []
    if not pre_pool:
        warnings.append("No pre-disaster DONE scenes match the disaster AOI and time window")
    if not post_pool:
        warnings.append("No post-disaster DONE scenes match the disaster AOI and time window")
    if pre_pool and post_pool and not selected_pairs:
        warnings.append("Pre/post scene pools exist, but no pair meets the overlap/polarization constraints")

    return {
        "disaster": {
            "name": getattr(req, "disaster_name", None),
            "date": _format_ymd(disaster_date),
            "pre_start": _format_ymd(pre_start),
            "pre_end": _format_ymd(pre_end),
            "post_start": _format_ymd(post_start),
            "post_end": _format_ymd(post_end),
        },
        "aoi": {
            **aoi_meta,
            "geojson": aoi_geojson,
        },
        "pre_pool": pre_pool,
        "post_pool": post_pool,
        "candidate_pairs": selected_pairs,
        "pairs": selected_pairs,
        "total": len(selected_pairs),
        "summary": {
            "pre_pool_count": len(pre_pool),
            "post_pool_count": len(post_pool),
            "candidate_count": len(selected_pairs),
            "min_aoi_coverage_ratio": min_aoi_coverage_ratio,
            "min_pair_overlap_ratio": min_pair_overlap_ratio,
        },
        "warnings": warnings,
    }


def _open_envi_rasterio(path: str):
    import rasterio

    normalized_path = path.replace("\\", "/")
    try:
        return rasterio.open(normalized_path)
    except Exception:
        pass
    for ext in (".bin", ".img", ".tif", ".tiff"):
        try:
            return rasterio.open(normalized_path + ext)
        except Exception:
            pass
    raise FileNotFoundError(f"Raster file cannot be opened: {path}")


def _raster_to_png_bytes(path: str, colormap: dict[int, tuple[int, int, int, int]]) -> tuple[bytes, list[float]]:
    import numpy as np
    from PIL import Image

    with _open_envi_rasterio(path) as ds:
        data = ds.read(1)
        bounds = ds.bounds
        geo_bounds = [bounds.bottom, bounds.left, bounds.top, bounds.right]

    rgba = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)
    for value, color in colormap.items():
        rgba[data == value] = color
    image = Image.fromarray(rgba, "RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), geo_bounds


def _geo_raster_to_png_bytes(path: str) -> tuple[bytes, list[float]]:
    import numpy as np
    from PIL import Image

    with _open_envi_rasterio(path) as ds:
        data = ds.read(1).astype("float32")
        nodata = ds.nodata
        bounds = ds.bounds
        geo_bounds = [bounds.bottom, bounds.left, bounds.top, bounds.right]

    if nodata is not None:
        nodata_mask = (data == nodata) | ~np.isfinite(data)
    else:
        nodata_mask = ~np.isfinite(data)

    valid = data[~nodata_mask]
    if valid.size == 0:
        normalized = np.zeros_like(data, dtype=np.uint8)
    else:
        p2, p98 = np.percentile(valid, 2), np.percentile(valid, 98)
        clipped = np.clip(data, p2, p98)
        normalized = ((clipped - p2) / max(p98 - p2, 1e-9) * 255).astype(np.uint8)

    rgba = np.stack([normalized, normalized, normalized, np.full_like(normalized, 200)], axis=-1)
    rgba[nodata_mask, 3] = 0
    image = Image.fromarray(rgba, "RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), geo_bounds


_FLOOD_COLORMAP = {
    1: (24, 144, 255, 200),
    2: (255, 77, 79, 220),
    3: (250, 173, 20, 180),
    4: (80, 80, 80, 80),
}


async def get_flood_detection_preview(detection_id: int, layer: str, db: AsyncSession):
    normalized_layer = layer.strip().lower()
    if normalized_layer == "classified":
        detection = await db.get(FloodDetectionORM, detection_id)
        if not detection or not detection.classified_path:
            raise HTTPException(status_code=404, detail="Classified result not found")
        path = detection.classified_path.replace("\\", "/")
        png_bytes, geo_bounds = await _render_classified_preview(path)
        return JSONResponse(
            {
                "image_b64": base64.b64encode(png_bytes).decode(),
                "bounds": geo_bounds,
                "legend": {
                    "stable_water": "#1890ff",
                    "flood": "#ff4d4f",
                    "high_backscatter": "#faad14",
                    "non_water": "#505050",
                },
            }
        )
    if normalized_layer in ("pre", "post"):
        return await _get_scene_preview_for_detection(detection_id, normalized_layer, db)
    raise HTTPException(status_code=404, detail=f"Unsupported preview layer: {layer}")


async def _render_classified_preview(path: str) -> tuple[bytes, list[float]]:
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Requested file does not exist")
    try:
        return await asyncio.to_thread(_raster_to_png_bytes, path, _FLOOD_COLORMAP)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Render failed: {exc}") from exc


async def _get_scene_preview_for_detection(detection_id: int, layer: str, db: AsyncSession):
    detection = await db.get(FloodDetectionORM, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Flood detection not found")
    scene_id = detection.pre_scene_id if layer == "pre" else detection.post_scene_id
    scene = await db.get(SARSceneGeoORM, scene_id)
    scene_path = _scene_analysis_path(scene)
    if not scene_path:
        raise HTTPException(status_code=404, detail="Scene analysis-ready GeoTIFF not found")
    path = scene_path.replace("\\", "/")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Requested file does not exist")
    try:
        png_bytes, geo_bounds = await asyncio.to_thread(_geo_raster_to_png_bytes, path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Render failed: {exc}") from exc
    return JSONResponse({"image_b64": base64.b64encode(png_bytes).decode(), "bounds": geo_bounds})


async def get_water_extraction_preview(extraction_id: int, db: AsyncSession) -> dict[str, Any]:
    detection = await db.get(WaterExtractionORM, extraction_id)
    if not detection:
        detection = await db.get(WaterDetectionORM, extraction_id)
    if not detection:
        raise HTTPException(status_code=404, detail=f"Water extraction id={extraction_id} not found")
    if not detection.output_path or not os.path.isfile(detection.output_path):
        raise HTTPException(status_code=404, detail="Output file does not exist")

    import numpy as np
    import rasterio
    from PIL import Image

    with rasterio.open(detection.output_path) as src:
        data = src.read(1)
        transform = src.transform
        height, width = data.shape
        min_lon = transform.c
        max_lon = transform.c + width * transform.a
        max_lat = transform.f
        min_lat = transform.f + height * transform.e

    rgba = np.zeros((data.shape[0], data.shape[1], 4), dtype=np.uint8)
    rgba[data > 0] = [24, 144, 255, 160]

    image = Image.fromarray(rgba, "RGBA")
    max_dim = 1024
    if max(width, height) > max_dim:
        ratio = max_dim / max(width, height)
        image = image.resize((int(width * ratio), int(height * ratio)), Image.NEAREST)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {
        "png_base64": base64.b64encode(buffer.getvalue()).decode(),
        "bounds": {
            "min_lon": min_lon,
            "min_lat": min_lat,
            "max_lon": max_lon,
            "max_lat": max_lat,
        },
    }
