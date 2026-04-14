from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from geoalchemy2.functions import ST_Intersects
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..config import read_int_env, settings
from ..database import get_db
from ..models import (
    AuthUserORM,
    RadarData,
    RadarDataORM,
    RadarDataPage,
    RadarPreviewStatusInfo,
    ScanRequest,
)
from ..services.data_service import data_service
from ..services.image_service import image_service
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from .dependencies import (
    _add_operation_audit_log,
    _get_aoi_from_token,
    _parse_aoi_from_files,
    _parse_aoi_geojson_form_value,
    _require_admin,
    _resolve_region_aoi_payload,
    _store_aoi_token,
)

router = APIRouter()
logger = logging.getLogger(__name__)
LIST_QUERY_MAX_LIMIT = read_int_env(
    "LIST_QUERY_MAX_LIMIT",
    2000,
    minimum=1,
    maximum=100000,
)
LIST_QUERY_MAX_OFFSET = read_int_env(
    "LIST_QUERY_MAX_OFFSET",
    200000,
    minimum=0,
    maximum=20000000,
)
LIST_QUERY_MAX_WINDOW = read_int_env(
    "LIST_QUERY_MAX_WINDOW",
    202000,
    minimum=1,
    maximum=50000000,
)
LIST_QUERY_TIMEOUT_MS = read_int_env(
    "LIST_QUERY_TIMEOUT_MS",
    20000,
    minimum=1000,
    maximum=300000,
)
RADAR_SEARCH_OPTIONS_MAX_VALUES = read_int_env(
    "RADAR_SEARCH_OPTIONS_MAX_VALUES",
    5000,
    minimum=100,
    maximum=500000,
)
RADAR_IMAGING_DATES_MAX_VALUES = read_int_env(
    "RADAR_IMAGING_DATES_MAX_VALUES",
    5000,
    minimum=100,
    maximum=500000,
)


class RadarDataSearchPageResponse(BaseModel):
    items: List[RadarData]
    total: int
    limit: int
    offset: int
    has_more: bool
    aoi_token: Optional[str] = None
    normalized_aoi_geojson: Optional[Dict[str, Any]] = None


class RadarDataSearchOptionsResponse(BaseModel):
    satellite: List[str]
    satellite_mode: List[str]
    receiving_station: List[str]
    imaging_mode: List[str]
    orbit_circle: List[str]
    acquisition_time_utc: List[str]
    product_type: List[str]
    polarization: List[str]
    product_level: List[str]
    product_unique_id: List[str]
    orbit_direction: List[str]
    imaging_dates: List[str]


async def _query_distinct_non_empty_values(
    db: AsyncSession,
    column,
    max_values: int = RADAR_SEARCH_OPTIONS_MAX_VALUES,
    satellite_filter: Optional[List[str]] = None,
) -> List[str]:
    safe_max_values = min(RADAR_SEARCH_OPTIONS_MAX_VALUES, max(1, int(max_values or RADAR_SEARCH_OPTIONS_MAX_VALUES)))
    stmt = (
        select(column)
        .where(column.is_not(None), column != "")
    )
    if satellite_filter:
        stmt = stmt.where(RadarDataORM.satellite.in_(satellite_filter))
    stmt = stmt.distinct().order_by(column.asc()).limit(safe_max_values)
    result = await db.execute(stmt)
    return [value for value in result.scalars().all() if isinstance(value, str) and value.strip()]


def _is_postgresql_session(db: AsyncSession) -> bool:
    try:
        bind = db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        return (dialect_name or "").lower() == "postgresql"
    except Exception:
        return False


async def _apply_list_query_statement_timeout(db: AsyncSession) -> None:
    timeout_ms = int(LIST_QUERY_TIMEOUT_MS)
    if timeout_ms <= 0:
        return
    if not _is_postgresql_session(db):
        return
    try:
        # PostgreSQL SET/SET LOCAL does not support bind parameters in this form.
        timeout_ms = int(timeout_ms)
        await db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
    except Exception as exc:
        logger.warning("Failed to apply list query statement_timeout=%sms: %s", timeout_ms, exc)


def _normalize_list_pagination(limit: int, offset: int) -> Tuple[int, int]:
    safe_limit = min(LIST_QUERY_MAX_LIMIT, max(1, int(limit or 1)))
    safe_offset = min(LIST_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    if safe_limit + safe_offset > LIST_QUERY_MAX_WINDOW:
        safe_offset = max(0, LIST_QUERY_MAX_WINDOW - safe_limit)
    return safe_limit, safe_offset


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _validate_imaging_date(value: Optional[str], field_name: str) -> Optional[str]:
    normalized = _normalize_optional_text(value)
    if not normalized:
        return None
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} 格式错误，应为 YYYY-MM-DD。",
        ) from exc
    return normalized


def _safe_file_mtime(path: str) -> float:
    try:
        return max(os.path.getmtime(path), os.path.getctime(path))
    except OSError:
        return 0.0


def _radar_preview_paths(record: RadarDataORM) -> Tuple[str, str]:
    unique_id = record.unique_id or record.file_path
    raw_cache_path = data_service.get_radar_raw_cache_path(unique_id, record.file_path)
    geo_cache_path = data_service.get_radar_geo_cache_path(unique_id, record.file_path)
    return raw_cache_path, geo_cache_path


def _build_radar_preview_status(
    record: RadarDataORM,
    source_found: bool,
    has_geo_cache: bool,
    has_raw_cache: bool,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> RadarPreviewStatusInfo:
    if has_geo_cache:
        status = "READY"
        default_message = "地理纠正预览缓存可用。"
    elif has_raw_cache:
        status = "FAILED"
        default_message = "地理纠正缓存不可用，已回退到原图缓存。"
    elif source_found:
        status = "FAILED"
        default_message = "已找到源图，但地理纠正缓存尚未可用。"
    else:
        status = "NONE"
        default_message = "未找到可用源图，无法生成预览缓存。"

    return RadarPreviewStatusInfo(
        radar_id=record.id,
        status=status,
        cache_version=record.preview_cache_version or settings.RADAR_GEO_CACHE_VERSION,
        cache_updated_at=record.preview_cache_updated_at,
        has_geo_cache=has_geo_cache,
        has_raw_cache=has_raw_cache,
        source_found=source_found,
        fallback_in_use=(not has_geo_cache and has_raw_cache),
        message=message or default_message,
        error=error if error is not None else record.preview_cache_error,
    )


async def _build_radar_preview_cache(
    record: RadarDataORM,
    db: AsyncSession,
    force: bool = False,
) -> RadarPreviewStatusInfo:
    raw_cache_path, geo_cache_path = _radar_preview_paths(record)
    has_geo_cache = os.path.exists(geo_cache_path)
    has_raw_cache = os.path.exists(raw_cache_path)

    preview_source = await asyncio.to_thread(data_service.find_radar_preview_source, record.file_path)
    source_found = bool(preview_source)
    geo_error: Optional[str] = None
    raw_error: Optional[str] = None
    performed_build = False
    previous_state = (
        record.preview_cache_status,
        record.preview_cache_version,
        record.preview_cache_path,
        record.preview_cache_error,
    )

    if source_found:
        source_mtime = _safe_file_mtime(preview_source or "")
        geo_mtime = _safe_file_mtime(geo_cache_path)
        raw_mtime = _safe_file_mtime(raw_cache_path)

        skip_rebuild_after_failed = (
            (not force)
            and (record.preview_cache_status or "NONE") == "FAILED"
            and (record.preview_cache_version or "") == settings.RADAR_GEO_CACHE_VERSION
            and has_raw_cache
        )

        need_geo_rebuild = (
            force
            or (not has_geo_cache)
            or geo_mtime < source_mtime
            or (record.preview_cache_version or "") != settings.RADAR_GEO_CACHE_VERSION
        )
        if need_geo_rebuild and not skip_rebuild_after_failed:
            coverage_polygon = data_service._normalize_coverage_polygon(record.coverage_polygon)
            if not coverage_polygon:
                geo_error = "invalid_coverage_polygon"
                has_geo_cache = False
            else:
                try:
                    bbox = (
                        float(record.min_lon),
                        float(record.min_lat),
                        float(record.max_lon),
                        float(record.max_lat),
                    )
                except (TypeError, ValueError):
                    bbox = None

                if not bbox:
                    geo_error = "invalid_bbox"
                    has_geo_cache = False
                else:
                    source_corner_mapping = await asyncio.to_thread(
                        data_service.get_radar_source_corner_mapping,
                        record.file_path,
                    )
                    ok_geo, geo_error = await asyncio.to_thread(
                        image_service.create_geocorrected_radar_cached_image,
                        preview_source,
                        geo_cache_path,
                        coverage_polygon,
                        bbox,
                        source_corner_mapping,
                        (settings.RADAR_THUMBNAIL_MAX_SIZE, settings.RADAR_THUMBNAIL_MAX_SIZE),
                        settings.RADAR_GEO_CACHE_QUALITY,
                    )
                    performed_build = True
                    has_geo_cache = bool(ok_geo and os.path.exists(geo_cache_path))

        need_raw_rebuild = force or (not has_raw_cache) or raw_mtime < source_mtime
        if need_raw_rebuild:
            ok_raw = await asyncio.to_thread(
                image_service.create_radar_cached_image,
                preview_source,
                raw_cache_path,
                (settings.RADAR_THUMBNAIL_MAX_SIZE, settings.RADAR_THUMBNAIL_MAX_SIZE),
            )
            performed_build = True
            has_raw_cache = bool(ok_raw and os.path.exists(raw_cache_path))
            if not ok_raw:
                raw_error = "raw_cache_build_failed"

    if has_geo_cache:
        record.preview_cache_status = "READY"
        record.preview_cache_path = geo_cache_path
        record.preview_cache_error = None
    elif source_found:
        record.preview_cache_status = "FAILED"
        record.preview_cache_path = None
        record.preview_cache_error = geo_error or raw_error or "geo_cache_not_ready"
    else:
        record.preview_cache_status = "NONE"
        record.preview_cache_path = None
        record.preview_cache_error = "preview_source_not_found"

    record.preview_cache_version = settings.RADAR_GEO_CACHE_VERSION
    new_state = (
        record.preview_cache_status,
        record.preview_cache_version,
        record.preview_cache_path,
        record.preview_cache_error,
    )
    state_changed = new_state != previous_state
    if performed_build or state_changed:
        record.preview_cache_updated_at = datetime.utcnow()

    status_obj = _build_radar_preview_status(
        record=record,
        source_found=source_found,
        has_geo_cache=has_geo_cache,
        has_raw_cache=has_raw_cache,
        error=record.preview_cache_error,
    )
    if performed_build or state_changed:
        db.add(record)
        await db.commit()
    return status_obj


async def _get_cached_radar_preview(data_id: int, db: AsyncSession):
    result = await db.execute(select(RadarDataORM).where(RadarDataORM.id == data_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"ID为 {data_id} 的源数据不存在。")

    raw_cache_path, geo_cache_path = _radar_preview_paths(record)
    if os.path.exists(geo_cache_path):
        return FileResponse(
            geo_cache_path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=31536000"},
        )

    if settings.RADAR_PREVIEW_BUILD_ON_DEMAND:
        build_status = await _build_radar_preview_cache(record, db, force=False)
        if build_status.has_geo_cache and os.path.exists(geo_cache_path):
            return FileResponse(
                geo_cache_path,
                media_type="image/webp",
                headers={"Cache-Control": "public, max-age=31536000"},
            )

    if os.path.exists(raw_cache_path):
        return FileResponse(
            raw_cache_path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=31536000"},
        )

    raise HTTPException(status_code=404, detail="未找到可用源影像缓存，请先执行数据扫描或重建缓存。")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/scan-data", status_code=202)
async def scan_data_endpoint(
    scan_request: ScanRequest,
    background_tasks: BackgroundTasks,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    触发数据扫描的API端点，支持雷达、精轨和 D-InSAR 结果。
    """
    try:
        params = {
            "radar_dirs": scan_request.radar_data_directories,
            "orbit_dir": scan_request.orbit_data_directory,
            "dinsar_dirs": scan_request.dinsar_results_directories
        }
        task_id = await task_service.create_task("SCAN_DATA", "系统数据同步扫描", params=params)

        payload = {
            "use_monitor_config": False,
            "radar_dirs": scan_request.radar_data_directories,
            "orbit_dir": scan_request.orbit_data_directory,
            "dinsar_dirs": scan_request.dinsar_results_directories,
        }
        await job_queue_service.create_job("SCAN_DATA", payload=payload, task_id=task_id)
        return {"message": "数据扫描任务已进入队列", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("数据扫描任务创建失败")
        raise HTTPException(status_code=500, detail="数据扫描任务创建失败，请查看后端日志")


@router.get("/radar-data/search/options", response_model=RadarDataSearchOptionsResponse)
async def get_radar_data_search_options_endpoint(
    satellite: Optional[List[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _apply_list_query_statement_timeout(db)
    sat_filter = [s for s in (satellite or []) if s.strip()] or None
    return RadarDataSearchOptionsResponse(
        satellite=await _query_distinct_non_empty_values(db, RadarDataORM.satellite),
        satellite_mode=await _query_distinct_non_empty_values(db, RadarDataORM.satellite_mode, satellite_filter=sat_filter),
        receiving_station=await _query_distinct_non_empty_values(db, RadarDataORM.receiving_station, satellite_filter=sat_filter),
        imaging_mode=await _query_distinct_non_empty_values(db, RadarDataORM.imaging_mode, satellite_filter=sat_filter),
        orbit_circle=await _query_distinct_non_empty_values(db, RadarDataORM.orbit_circle, satellite_filter=sat_filter),
        acquisition_time_utc=await _query_distinct_non_empty_values(db, RadarDataORM.acquisition_time_utc, satellite_filter=sat_filter),
        product_type=await _query_distinct_non_empty_values(db, RadarDataORM.product_type, satellite_filter=sat_filter),
        polarization=await _query_distinct_non_empty_values(db, RadarDataORM.polarization, satellite_filter=sat_filter),
        product_level=await _query_distinct_non_empty_values(db, RadarDataORM.product_level, satellite_filter=sat_filter),
        product_unique_id=await _query_distinct_non_empty_values(db, RadarDataORM.product_unique_id, satellite_filter=sat_filter),
        orbit_direction=await _query_distinct_non_empty_values(db, RadarDataORM.orbit_direction, satellite_filter=sat_filter),
        imaging_dates=sorted(
            await _query_distinct_non_empty_values(db, RadarDataORM.imaging_date, satellite_filter=sat_filter),
            reverse=True,
        ),
    )


@router.post("/radar-data/search", response_model=RadarDataSearchPageResponse)
async def search_radar_data_endpoint(
    limit: int = Form(500),
    offset: int = Form(0),
    satellite: Optional[str] = Form(None),
    satellite_mode: Optional[str] = Form(None),
    receiving_station: Optional[str] = Form(None),
    imaging_mode: Optional[str] = Form(None),
    orbit_circle: Optional[str] = Form(None),
    acquisition_time_utc: Optional[str] = Form(None),
    product_type: Optional[str] = Form(None),
    polarization: Optional[str] = Form(None),
    product_level: Optional[str] = Form(None),
    product_unique_id: Optional[str] = Form(None),
    orbit_direction: Optional[str] = Form(None),
    has_orbit_data: Optional[bool] = Form(None),
    is_envi_processed: Optional[bool] = Form(None),
    imaging_date_from: Optional[str] = Form(None),
    imaging_date_to: Optional[str] = Form(None),
    region_tree_id: Optional[str] = Form(None),
    aoi_token: Optional[str] = Form(None),
    aoi_geojson: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    db: AsyncSession = Depends(get_db),
):
    limit, offset = _normalize_list_pagination(limit, offset)
    await _apply_list_query_statement_timeout(db)

    n_satellite_raw = _normalize_optional_text(satellite)
    n_satellite_list: Optional[List[str]] = None
    if n_satellite_raw and "," in n_satellite_raw:
        n_satellite_list = [s.strip() for s in n_satellite_raw.split(",") if s.strip()]
    n_satellite_mode = _normalize_optional_text(satellite_mode)
    n_receiving_station = _normalize_optional_text(receiving_station)
    n_imaging_mode = _normalize_optional_text(imaging_mode)
    n_orbit_circle = _normalize_optional_text(orbit_circle)
    n_acquisition_time = _normalize_optional_text(acquisition_time_utc)
    n_product_type = _normalize_optional_text(product_type)
    n_polarization = _normalize_optional_text(polarization)
    n_product_level = _normalize_optional_text(product_level)
    n_product_unique_id = _normalize_optional_text(product_unique_id)
    n_orbit_direction = _normalize_optional_text(orbit_direction)
    n_date_from = _validate_imaging_date(imaging_date_from, "imaging_date_from")
    n_date_to = _validate_imaging_date(imaging_date_to, "imaging_date_to")
    if n_date_from and n_date_to and n_date_from > n_date_to:
        raise HTTPException(status_code=400, detail="imaging_date_from 不能晚于 imaging_date_to。")

    resolved_aoi_wkt: Optional[str] = None
    resolved_aoi_geojson: Optional[Dict[str, Any]] = None
    resolved_aoi_token: Optional[str] = None
    n_region_tree_id = _normalize_optional_text(region_tree_id)

    if files:
        parsed_aoi = await _parse_aoi_from_files(files)
        if parsed_aoi is None:
            raise HTTPException(status_code=400, detail="上传文件中必须包含 .shp 或 GeoJSON。")
        resolved_aoi_wkt, resolved_aoi_geojson = parsed_aoi
    elif aoi_geojson:
        parsed_aoi = _parse_aoi_geojson_form_value(aoi_geojson)
        if parsed_aoi:
            resolved_aoi_wkt, resolved_aoi_geojson = parsed_aoi
    elif n_region_tree_id:
        region_payload = _resolve_region_aoi_payload(n_region_tree_id)
        parsed_aoi = _parse_aoi_geojson_form_value(json.dumps(region_payload.get("aoi_geojson")))
        if parsed_aoi:
            resolved_aoi_wkt, resolved_aoi_geojson = parsed_aoi
    else:
        parsed_aoi = await _get_aoi_from_token(aoi_token)
        if parsed_aoi:
            resolved_aoi_wkt, resolved_aoi_geojson = parsed_aoi
            resolved_aoi_token = _normalize_optional_text(aoi_token)

    if resolved_aoi_wkt and resolved_aoi_geojson and not resolved_aoi_token:
        resolved_aoi_token = await _store_aoi_token(resolved_aoi_wkt, resolved_aoi_geojson)

    filters = []
    if n_satellite_list:
        filters.append(RadarDataORM.satellite.in_(n_satellite_list))
    elif n_satellite_raw:
        filters.append(RadarDataORM.satellite.ilike(f"%{n_satellite_raw}%"))
    if n_satellite_mode:
        filters.append(RadarDataORM.satellite_mode.ilike(f"%{n_satellite_mode}%"))
    if n_receiving_station:
        filters.append(RadarDataORM.receiving_station.ilike(f"%{n_receiving_station}%"))
    if n_imaging_mode:
        filters.append(RadarDataORM.imaging_mode.ilike(f"%{n_imaging_mode}%"))
    if n_orbit_circle:
        filters.append(RadarDataORM.orbit_circle.ilike(f"%{n_orbit_circle}%"))
    if n_acquisition_time:
        filters.append(RadarDataORM.acquisition_time_utc.ilike(f"%{n_acquisition_time}%"))
    if n_product_type:
        filters.append(RadarDataORM.product_type.ilike(f"%{n_product_type}%"))
    if n_polarization:
        filters.append(RadarDataORM.polarization.ilike(f"%{n_polarization}%"))
    if n_product_level:
        filters.append(RadarDataORM.product_level.ilike(f"%{n_product_level}%"))
    if n_product_unique_id:
        filters.append(RadarDataORM.product_unique_id.ilike(f"%{n_product_unique_id}%"))
    if n_orbit_direction:
        filters.append(RadarDataORM.orbit_direction.ilike(f"%{n_orbit_direction}%"))
    if has_orbit_data is not None:
        filters.append(RadarDataORM.has_orbit_data == has_orbit_data)
    if is_envi_processed is not None:
        filters.append(RadarDataORM.is_envi_processed == is_envi_processed)
    if n_date_from:
        filters.append(RadarDataORM.imaging_date >= n_date_from)
    if n_date_to:
        filters.append(RadarDataORM.imaging_date <= n_date_to)
    if resolved_aoi_wkt:
        aoi_geom = func.ST_GeomFromText(resolved_aoi_wkt, 4326)
        filters.append(ST_Intersects(RadarDataORM.geom, aoi_geom))

    total_stmt = select(func.count(RadarDataORM.id))
    data_stmt = (
        select(RadarDataORM)
        .order_by(RadarDataORM.id.desc())
        .offset(offset)
        .limit(limit)
    )
    for condition in filters:
        total_stmt = total_stmt.where(condition)
        data_stmt = data_stmt.where(condition)

    total_res = await db.execute(total_stmt)
    total = int(total_res.scalar_one() or 0)
    result = await db.execute(data_stmt)
    items = result.scalars().all()

    return RadarDataSearchPageResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(items)) < total,
        aoi_token=resolved_aoi_token,
        normalized_aoi_geojson=resolved_aoi_geojson,
    )


@router.get("/radar-data", response_model=RadarDataPage)
async def get_all_data_endpoint(
    limit: int = 500,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    从数据库获取所有雷达数据的API端点。
    """
    from sqlalchemy.orm import defer
    limit, offset = _normalize_list_pagination(limit, offset)
    await _apply_list_query_statement_timeout(db)
    total_res = await db.execute(select(func.count(RadarDataORM.id)))
    total = int(total_res.scalar_one() or 0)
    result = await db.execute(
        select(RadarDataORM)
        .options(defer(RadarDataORM.geom))  # 延迟加载 geom 字段
        .order_by(RadarDataORM.id.desc())
        .offset(offset)
        .limit(limit)
    )
    items = result.scalars().all()
    return RadarDataPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(items)) < total,
    )


@router.get("/radar-data/imaging-dates")
async def list_radar_imaging_dates(db: AsyncSession = Depends(get_db)):
    """
    返回所有可用的成像日期（去重），用于配对筛选。
    """
    await _apply_list_query_statement_timeout(db)
    result = await db.execute(
        select(RadarDataORM.imaging_date)
        .where(RadarDataORM.imaging_date.is_not(None))
        .distinct()
        .limit(RADAR_IMAGING_DATES_MAX_VALUES)
    )
    dates = sorted(
        [value for value in result.scalars().all() if value],
        reverse=True,
    )
    return {"dates": dates}

@router.get("/radar-data/available-satellites")
async def list_available_satellites(db: AsyncSession = Depends(get_db)):
    """
    返回数据库中所有可用的卫星列表（去重），用于多卫星配对。
    """
    await _apply_list_query_statement_timeout(db)
    result = await db.execute(
        select(RadarDataORM.satellite)
        .where(RadarDataORM.satellite.is_not(None))
        .where(RadarDataORM.satellite != "")
        .distinct()
    )
    satellites = sorted([value for value in result.scalars().all() if value])
    return {"satellites": satellites}


@router.get("/radar-data/{data_id}/thumb")
async def get_radar_data_thumb_endpoint(data_id: int, db: AsyncSession = Depends(get_db)):
    """
    获取源雷达数据的预览缓存图（WebP）。
    """
    return await _get_cached_radar_preview(data_id, db)


@router.get("/radar-data/{data_id}/preview-status", response_model=RadarPreviewStatusInfo)
async def get_radar_preview_status_endpoint(data_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RadarDataORM).where(RadarDataORM.id == data_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"ID为 {data_id} 的源数据不存在。")

    raw_cache_path, geo_cache_path = _radar_preview_paths(record)
    has_geo_cache = os.path.exists(geo_cache_path)
    has_raw_cache = os.path.exists(raw_cache_path)
    source_found = bool(await asyncio.to_thread(data_service.find_radar_preview_source, record.file_path))
    return _build_radar_preview_status(
        record=record,
        source_found=source_found,
        has_geo_cache=has_geo_cache,
        has_raw_cache=has_raw_cache,
    )


@router.post("/radar-data/{data_id}/rebuild-preview-cache", response_model=RadarPreviewStatusInfo)
async def rebuild_radar_preview_cache_endpoint(
    data_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    admin_user_id = admin_user.id
    admin_username = admin_user.username

    result = await db.execute(select(RadarDataORM).where(RadarDataORM.id == data_id))
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"ID为 {data_id} 的源数据不存在。")

    status = await _build_radar_preview_cache(record, db, force=True)
    await _add_operation_audit_log(
        db,
        request=request,
        action="radar_preview_rebuild",
        resource=f"/api/radar-data/{data_id}/rebuild-preview-cache",
        detail={
            "status": status.status,
            "has_geo_cache": status.has_geo_cache,
            "has_raw_cache": status.has_raw_cache,
        },
        user_id=admin_user_id,
        username=admin_username,
    )
    await db.commit()
    return status
