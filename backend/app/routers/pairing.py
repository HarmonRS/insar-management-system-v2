from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import (
    AuthUserORM,
    PairingNetworkEdgeORM,
    PairingNetworkRunORM,
    PairingRequest,
    PairingResponse,
    PsRequest,
    RadarData,
    TimeseriesStackPlan,
    TimeseriesStackPlanDetail,
    TimeseriesStackPlanEdge,
    TimeseriesStackPlanEdgeORM,
    TimeseriesStackPlanItem,
    TimeseriesStackPlanItemORM,
    TimeseriesStackPlanORM,
)
from ..services.pairing_cache_service import pairing_cache_service
from ..services.spatial_service import spatial_service
from .dependencies import (
    _parse_aoi_from_files,
    _parse_aoi_geojson_form_value,
    _require_admin,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_pairing_request_from_form(
    time_baseline_min: int = Form(1),
    time_baseline_max: int = Form(30),
    overlap_threshold: float = Form(0.5),
    spatial_baseline_max_meters: int = Form(5000),
    limit_footprint_center_distance: bool = Form(True),
    max_temporal_baseline_days: Optional[int] = Form(None),
    pair_footprint_overlap_min_ratio: Optional[float] = Form(None),
    footprint_center_distance_max_meters: Optional[int] = Form(None),
    coverage_diversity_penalty: float = Form(0.3),
    require_same_imaging_mode: bool = Form(True),
    require_same_polarization: bool = Form(True),
    aoi_overlap_threshold: Optional[float] = Form(None),
    start_date: Optional[str] = Form(None),
    # === 新增参数 ===
    master_date_from: Optional[str] = Form(None),
    master_date_to: Optional[str] = Form(None),
    slave_date_from: Optional[str] = Form(None),
    slave_date_to: Optional[str] = Form(None),
    strategy: str = Form("dinsar_production"),
    num_connections: int = Form(1),
    reference_image_id: Optional[int] = Form(None),
    allowed_satellites: Optional[str] = Form(None),  # JSON string
    cross_satellite_pairing: bool = Form(False),
) -> PairingRequest:
    # Parse allowed_satellites from JSON string
    satellites_list = None
    if allowed_satellites:
        try:
            import json
            satellites_list = json.loads(allowed_satellites)
        except Exception:
            satellites_list = None

    try:
        return PairingRequest(
            time_baseline_min=time_baseline_min,
            time_baseline_max=time_baseline_max,
            overlap_threshold=overlap_threshold,
            spatial_baseline_max_meters=spatial_baseline_max_meters,
            limit_footprint_center_distance=limit_footprint_center_distance,
            max_temporal_baseline_days=max_temporal_baseline_days,
            pair_footprint_overlap_min_ratio=pair_footprint_overlap_min_ratio,
            footprint_center_distance_max_meters=footprint_center_distance_max_meters,
            coverage_diversity_penalty=coverage_diversity_penalty,
            require_same_imaging_mode=require_same_imaging_mode,
            require_same_polarization=require_same_polarization,
            aoi_overlap_threshold=aoi_overlap_threshold,
            start_date=start_date,
            master_date_from=master_date_from,
            master_date_to=master_date_to,
            slave_date_from=slave_date_from,
            slave_date_to=slave_date_to,
            strategy="dinsar_production",
            num_connections=num_connections,
            reference_image_id=reference_image_id,
            allowed_satellites=satellites_list,
            cross_satellite_pairing=cross_satellite_pairing,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def get_ps_request_from_form(
    initial_overlap_threshold: float = Form(0.3),
    final_overlap_threshold: float = Form(0.95),
    time_baseline_min: int = Form(1),
    time_baseline_max: int = Form(90),
    spatial_baseline_max_meters: int = Form(3000),
    network_overlap_threshold: float = Form(0.5),
    num_connections: int = Form(1),
) -> PsRequest:
    return PsRequest(
        initial_overlap_threshold=initial_overlap_threshold,
        final_overlap_threshold=final_overlap_threshold,
        time_baseline_min=time_baseline_min,
        time_baseline_max=time_baseline_max,
        spatial_baseline_max_meters=spatial_baseline_max_meters,
        network_overlap_threshold=network_overlap_threshold,
        num_connections=num_connections,
    )


@router.get("/pairing/health")
async def get_pairing_health_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    return await pairing_cache_service.get_admin_summary(db)


@router.post("/pairing/rebuild-cache")
async def rebuild_pairing_cache_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    return await pairing_cache_service.rebuild_metric_cache(db, commit=True)


@router.post("/pairing/reconcile-dirty")
async def reconcile_dirty_pairing_endpoint(
    force_full: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    return await pairing_cache_service.reconcile_dirty_scenes(
        db,
        force_full=force_full,
        commit=True,
    )


@router.get("/pairing/networks/{network_run_id}")
async def get_pairing_network_run_endpoint(
    network_run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    run_result = await db.execute(
        select(PairingNetworkRunORM).where(PairingNetworkRunORM.network_run_id == network_run_id)
    )
    run = run_result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Pairing network run not found.")

    edge_result = await db.execute(
        select(PairingNetworkEdgeORM)
        .where(PairingNetworkEdgeORM.network_run_ref_id == run.id)
        .order_by(PairingNetworkEdgeORM.edge_rank.asc(), PairingNetworkEdgeORM.id.asc())
    )
    edges = edge_result.scalars().all()
    return {
        "network_run_id": run.network_run_id,
        "strategy": run.strategy,
        "policy_version": run.policy_version,
        "request_hash": run.request_hash,
        "request_params_json": run.request_params_json,
        "aoi_source": run.aoi_source,
        "aoi_hash": run.aoi_hash,
        "aoi_summary_json": run.aoi_summary_json,
        "candidate_count": int(run.candidate_count or 0),
        "selected_edge_count": int(run.selected_edge_count or 0),
        "warning_count": int(run.warning_count or 0),
        "status": run.status,
        "fallback_used": bool(run.fallback_used),
        "created_by": run.created_by,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "edges": [
            {
                "id": edge.id,
                "metric_cache_ref_id": edge.metric_cache_ref_id,
                "edge_rank": int(edge.edge_rank or 0),
                "selection_reason": edge.selection_reason,
                "selection_score": edge.selection_score,
                "selection_meta_json": edge.selection_meta_json,
                "is_reference_edge": bool(edge.is_reference_edge),
                "created_at": edge.created_at,
            }
            for edge in edges
        ],
    }


@router.get("/timeseries-plans/{plan_id}", response_model=TimeseriesStackPlanDetail)
async def get_timeseries_stack_plan_endpoint(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    normalized_plan_id = str(plan_id or "").strip()
    if not normalized_plan_id:
        raise HTTPException(status_code=400, detail="plan_id is required.")

    plan_result = await db.execute(
        select(TimeseriesStackPlanORM).where(TimeseriesStackPlanORM.plan_id == normalized_plan_id)
    )
    plan = plan_result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="Timeseries stack plan not found.")

    items_result = await db.execute(
        select(TimeseriesStackPlanItemORM)
        .where(TimeseriesStackPlanItemORM.plan_ref_id == plan.id)
        .order_by(TimeseriesStackPlanItemORM.scene_rank.asc(), TimeseriesStackPlanItemORM.id.asc())
    )
    edges_result = await db.execute(
        select(TimeseriesStackPlanEdgeORM)
        .where(TimeseriesStackPlanEdgeORM.plan_ref_id == plan.id)
        .order_by(TimeseriesStackPlanEdgeORM.edge_rank.asc(), TimeseriesStackPlanEdgeORM.id.asc())
    )
    payload = TimeseriesStackPlan.model_validate(plan).model_dump()
    payload["items"] = [
        TimeseriesStackPlanItem.model_validate(item)
        for item in items_result.scalars().all()
    ]
    payload["edges"] = [
        TimeseriesStackPlanEdge.model_validate(edge)
        for edge in edges_result.scalars().all()
    ]
    return TimeseriesStackPlanDetail.model_validate(payload)


@router.post("/find-pairs", response_model=PairingResponse)
async def find_pairs_endpoint(
    params: PairingRequest = Depends(get_pairing_request_from_form),
    files: Optional[List[UploadFile]] = File(None),
    aoi_geojson: Optional[str] = Form(None),
    require_orbit_data: bool = Form(True),
    db: AsyncSession = Depends(get_db)
):
    """
    根据参数查找干涉对，数据源为数据库。
    """
    try:
        resolved_aoi = await _parse_aoi_from_files(files)
        if resolved_aoi is None:
            resolved_aoi = _parse_aoi_geojson_form_value(aoi_geojson)

        if files and resolved_aoi is None:
            raise HTTPException(status_code=400, detail="上传文件中必须包含 .shp 或 GeoJSON。")

        aoi_wkt = resolved_aoi[0] if resolved_aoi else None
        response_aoi_geojson = resolved_aoi[1] if resolved_aoi else None
        pairs, runtime_warnings, pairing_metadata = await spatial_service.find_dinsar_pairs(
            db,
            params,
            aoi_wkt=aoi_wkt,
            require_orbit_data=require_orbit_data,
        )
        return PairingResponse(
            pairs=pairs,
            aoi_geojson=response_aoi_geojson,
            warnings=runtime_warnings,
            fallback_used=bool(pairing_metadata.get("fallback_used")),
            degraded=bool(pairing_metadata.get("degraded")),
            policy_version=pairing_metadata.get("policy_version"),
            network_run_id=pairing_metadata.get("network_run_id"),
            candidate_count=int(pairing_metadata.get("candidate_count") or 0),
            selected_edge_count=int(pairing_metadata.get("selected_edge_count") or 0),
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        if isinstance(e, RuntimeError):
            raise HTTPException(status_code=409, detail=str(e))
        logger.exception("处理 AOI 或查找干涉对时发生错误")
        raise HTTPException(status_code=500, detail="处理 AOI 或查找干涉对时发生错误，请查看后端日志")


@router.post("/find-ps-timeseries", response_model=Dict[str, List[RadarData]])
async def find_ps_timeseries_endpoint(
    params: PsRequest = Depends(get_ps_request_from_form),
    files: Optional[List[UploadFile]] = File(None),
    aoi_geojson: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """
    为 PS-InSAR 查找时序影像栈。
    """
    if not files and not aoi_geojson:
        raise HTTPException(status_code=400, detail="必须提供 AOI（SHP 或 GeoJSON）。")

    try:
        resolved_aoi = await _parse_aoi_from_files(files)
        if resolved_aoi is None:
            resolved_aoi = _parse_aoi_geojson_form_value(aoi_geojson)

        if resolved_aoi is None:
            raise HTTPException(status_code=400, detail="未解析到有效 AOI，请检查 SHP 或 GeoJSON。")

        aoi_wkt = resolved_aoi[0]
        ps_stacks = await spatial_service.find_ps_timeseries_data(db, params, aoi_wkt=aoi_wkt)
        return ps_stacks
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"处理 AOI 或查找时序影像时发生严重错误: {e}")
