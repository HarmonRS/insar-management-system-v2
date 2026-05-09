from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..config import read_int_env
from ..database import get_db
from ..models import (
    AuthUserORM,
    DinsarTaskBatch,
    DinsarTaskBatchORM,
    DinsarTaskItem,
    DinsarTaskItemORM,
    PsTaskBatch,
    PsTaskBatchORM,
    PsTaskItem,
    PsTaskItemORM,
    RadarData,
    RadarPair,
    TimeseriesStackPlanEdgeORM,
    TimeseriesStackPlanItemORM,
    TimeseriesStackPlanORM,
)
from .dependencies import (
    _add_operation_audit_log,
    _refresh_dinsar_batch_summary,
    _refresh_ps_batch_summary,
    _require_admin,
)

router = APIRouter()

ALLOWED_BATCH_ITEM_STATUSES = {"PENDING", "IN_PROGRESS", "COMPLETED", "FAILED"}
TASK_BATCH_MAX_ITEMS = read_int_env(
    "TASK_BATCH_MAX_ITEMS",
    5000,
    minimum=1,
    maximum=200000,
)
BATCH_TEXT_MAX_LENGTH = read_int_env(
    "TASK_BATCH_TEXT_MAX_LENGTH",
    256,
    minimum=16,
    maximum=2000,
)
BATCH_REMARK_MAX_LENGTH = read_int_env(
    "TASK_BATCH_REMARK_MAX_LENGTH",
    2000,
    minimum=32,
    maximum=20000,
)
TASK_BATCH_LIST_DEFAULT_LIMIT = read_int_env(
    "TASK_BATCH_LIST_DEFAULT_LIMIT",
    200,
    minimum=1,
    maximum=5000,
)
TASK_BATCH_LIST_MAX_LIMIT = read_int_env(
    "TASK_BATCH_LIST_MAX_LIMIT",
    1000,
    minimum=1,
    maximum=20000,
)
TASK_BATCH_LIST_MAX_OFFSET = read_int_env(
    "TASK_BATCH_LIST_MAX_OFFSET",
    500000,
    minimum=0,
    maximum=20000000,
)


def _normalize_batch_item_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    normalized = str(status).strip().upper()
    if not normalized:
        return None
    if normalized not in ALLOWED_BATCH_ITEM_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {status}. Allowed: {sorted(ALLOWED_BATCH_ITEM_STATUSES)}",
        )
    return normalized


def _normalize_list_pagination(limit: int, offset: int) -> tuple[int, int]:
    safe_limit = min(TASK_BATCH_LIST_MAX_LIMIT, max(1, int(limit or TASK_BATCH_LIST_DEFAULT_LIMIT)))
    safe_offset = min(TASK_BATCH_LIST_MAX_OFFSET, max(0, int(offset or 0)))
    return safe_limit, safe_offset


class DinsarBatchCreateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=BATCH_TEXT_MAX_LENGTH)
    pairs: List[RadarPair]

    @field_validator("pairs")
    @classmethod
    def _validate_pairs_size(cls, value: List[RadarPair]) -> List[RadarPair]:
        if len(value) > TASK_BATCH_MAX_ITEMS:
            raise ValueError(
                f"pairs exceeds max item count ({TASK_BATCH_MAX_ITEMS})."
            )
        return value


class PsBatchCreateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=BATCH_TEXT_MAX_LENGTH)
    direction: Optional[str] = Field(default=None, max_length=BATCH_TEXT_MAX_LENGTH)
    plan_id: Optional[str] = Field(default=None, max_length=64)
    stack: List[RadarData]
    planning_context: Optional[Dict[str, Any]] = None

    @field_validator("stack")
    @classmethod
    def _validate_stack_size(cls, value: List[RadarData]) -> List[RadarData]:
        if len(value) > TASK_BATCH_MAX_ITEMS:
            raise ValueError(
                f"stack exceeds max item count ({TASK_BATCH_MAX_ITEMS})."
            )
        if len(value) < 3:
            raise ValueError("SBAS timeseries batch requires at least 3 scenes.")
        return value


class BatchItemUpdateRequest(BaseModel):
    status: Optional[str] = None
    remark: Optional[str] = Field(default=None, max_length=BATCH_REMARK_MAX_LENGTH)


def _normalize_lookup_key(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(text))


def _build_plan_context(
    plan: TimeseriesStackPlanORM,
    plan_items: List[TimeseriesStackPlanItemORM],
    plan_edges: Optional[List[TimeseriesStackPlanEdgeORM]] = None,
) -> Dict[str, Any]:
    request_params = plan.request_params_json if isinstance(plan.request_params_json, dict) else {}
    ordered_items = sorted(
        plan_items,
        key=lambda item: (int(item.scene_rank or 0), int(item.id or 0)),
    )
    scenes = [
        {
            "plan_item_id": item.id,
            "scene_id": item.radar_data_ref_id,
            "scene_rank": item.scene_rank,
            "scene_file_path": item.file_path,
            "scene_imaging_date": item.imaging_date,
            "scene_satellite": item.satellite,
            "scene_imaging_mode": item.imaging_mode,
            "scene_polarization": item.polarization,
            "selection_meta": item.selection_meta_json if isinstance(item.selection_meta_json, dict) else None,
        }
        for item in ordered_items
    ]
    ordered_edges = sorted(
        list(plan_edges or []),
        key=lambda item: (int(item.edge_rank or 0), int(item.id or 0)),
    )
    return {
        "source": "timeseries_stack_plan",
        "plan_id": plan.plan_id,
        "strategy": plan.strategy,
        "direction": plan.direction,
        "scene_count": int(plan.scene_count or len(scenes)),
        "stack_key": plan.stack_key,
        "group_key": plan.group_key,
        "request_hash": plan.request_hash,
        "aoi_summary": plan.aoi_summary_json if isinstance(plan.aoi_summary_json, dict) else None,
        "initial_overlap_threshold": request_params.get("initial_overlap_threshold"),
        "final_overlap_threshold": request_params.get("final_overlap_threshold"),
        "time_baseline_min": request_params.get("time_baseline_min"),
        "time_baseline_max": request_params.get("time_baseline_max"),
        "spatial_baseline_max_meters": request_params.get("spatial_baseline_max_meters"),
        "network_overlap_threshold": request_params.get("network_overlap_threshold"),
        "num_connections": request_params.get("num_connections"),
        "network_edge_count": len(ordered_edges),
        "stack_dates": [
            str(item.imaging_date).strip()
            for item in ordered_items
            if str(item.imaging_date or "").strip()
        ],
        "scenes": scenes,
        "network_edges": [
            {
                "edge_id": item.id,
                "edge_rank": item.edge_rank,
                "master_plan_item_ref_id": item.master_plan_item_ref_id,
                "slave_plan_item_ref_id": item.slave_plan_item_ref_id,
                "metric_cache_ref_id": item.metric_cache_ref_id,
                "master_scene_ref_id": item.master_scene_ref_id,
                "slave_scene_ref_id": item.slave_scene_ref_id,
                "master_imaging_date": item.master_imaging_date,
                "slave_imaging_date": item.slave_imaging_date,
                "temporal_baseline_days": item.temporal_baseline_days,
                "spatial_baseline_meters": item.spatial_baseline_meters,
                "perpendicular_baseline_meters": item.perpendicular_baseline_meters,
                "scene_overlap_ratio": item.scene_overlap_ratio,
                "pair_aoi_overlap_ratio": item.pair_aoi_overlap_ratio,
                "selection_reason": item.selection_reason,
                "selection_score": item.selection_score,
                "enabled": bool(item.enabled),
                "selection_meta": item.selection_meta_json if isinstance(item.selection_meta_json, dict) else None,
            }
            for item in ordered_edges
        ],
    }


@router.post("/task-batches/dinsar", response_model=DinsarTaskBatch)
async def create_dinsar_batch_endpoint(
    request: DinsarBatchCreateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    if not request.pairs:
        raise HTTPException(status_code=400, detail="No pairs provided.")

    batch_id = str(uuid.uuid4())
    batch_name = request.name or f"DINSAR_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    batch = DinsarTaskBatchORM(
        batch_id=batch_id,
        name=batch_name,
        status="PENDING",
        total_items=len(request.pairs),
        completed_items=0,
    )
    db.add(batch)

    for pair in request.pairs:
        master = pair.master
        slave = pair.slave
        item = DinsarTaskItemORM(
            batch_id=batch_id,
            task_name=pair.task_name,
            task_alias=pair.task_alias or pair.task_name,
            pair_key=pair.pair_key,
            scene_pair_uid=pair.pair_uid,
            network_run_id=pair.network_run_id,
            network_edge_id=pair.network_edge_id,
            policy_version=pair.policy_version,
            selection_strategy=pair.selection_strategy,
            master_path=master.file_path,
            slave_path=slave.file_path,
            master_satellite=master.satellite,
            master_imaging_date=master.imaging_date,
            master_imaging_mode=master.imaging_mode,
            master_polarization=master.polarization,
            slave_satellite=slave.satellite,
            slave_imaging_date=slave.imaging_date,
            slave_imaging_mode=slave.imaging_mode,
            slave_polarization=slave.polarization,
            time_baseline_days=pair.time_baseline_days,
            spatial_baseline_meters=pair.spatial_baseline_meters,
            scene_center_distance_meters=(
                pair.scene_center_distance_meters
                if pair.scene_center_distance_meters is not None
                else pair.spatial_baseline_meters
            ),
            status="PENDING",
        )
        db.add(item)

    await _add_operation_audit_log(
        db,
        request=http_request,
        action="batch_created",
        resource=f"task-batches/dinsar/{batch_id}",
        detail={
            "batch_name": batch_name,
            "items": len(request.pairs),
            "network_run_ids": sorted(
                {
                    str(pair.network_run_id)
                    for pair in request.pairs
                    if pair.network_run_id
                }
            ),
        },
    )
    await db.commit()
    await db.refresh(batch)
    return DinsarTaskBatch.model_validate(batch)


@router.get("/task-batches/dinsar", response_model=List[DinsarTaskBatch])
async def list_dinsar_batches_endpoint(
    limit: int = TASK_BATCH_LIST_DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    safe_limit, safe_offset = _normalize_list_pagination(limit, offset)
    result = await db.execute(
        select(DinsarTaskBatchORM)
        .order_by(DinsarTaskBatchORM.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    return [DinsarTaskBatch.model_validate(b) for b in result.scalars().all()]


@router.get("/task-batches/dinsar/{batch_id}/items", response_model=List[DinsarTaskItem])
async def list_dinsar_batch_items_endpoint(
    batch_id: str,
    limit: int = TASK_BATCH_LIST_DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    safe_limit, safe_offset = _normalize_list_pagination(limit, offset)
    result = await db.execute(
        select(DinsarTaskItemORM).where(DinsarTaskItemORM.batch_id == batch_id)
        .order_by(DinsarTaskItemORM.id.asc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    return [DinsarTaskItem.model_validate(i) for i in result.scalars().all()]


@router.patch("/task-batches/dinsar/{batch_id}/complete-all", response_model=DinsarTaskBatch)
async def complete_dinsar_batch_endpoint(
    batch_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    await db.execute(
        DinsarTaskItemORM.__table__.update()
        .where(DinsarTaskItemORM.batch_id == batch_id)
        .values(status="COMPLETED")
    )
    await _refresh_dinsar_batch_summary(db, batch_id)
    await db.commit()
    batch = await db.execute(select(DinsarTaskBatchORM).where(DinsarTaskBatchORM.batch_id == batch_id))
    batch_obj = batch.scalar_one_or_none()
    if not batch_obj:
        raise HTTPException(status_code=404, detail="Batch not found.")
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="batch_marked_complete",
        resource=f"task-batches/dinsar/{batch_id}",
        detail={"status": "COMPLETED", "items_completed": batch_obj.completed_items},
    )
    await db.commit()
    await db.refresh(batch_obj)
    return DinsarTaskBatch.model_validate(batch_obj)


@router.patch("/task-batches/dinsar/items/{item_id}", response_model=DinsarTaskItem)
async def update_dinsar_item_endpoint(
    item_id: int,
    request: BatchItemUpdateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    result = await db.execute(select(DinsarTaskItemORM).where(DinsarTaskItemORM.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    normalized_status = _normalize_batch_item_status(request.status)
    if normalized_status is not None:
        item.status = normalized_status
    if request.remark is not None:
        item.remark = request.remark

    await _refresh_dinsar_batch_summary(db, item.batch_id)
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="batch_item_updated",
        resource=f"task-batches/dinsar/items/{item_id}",
        detail={"batch_id": item.batch_id, "status": item.status},
    )
    await db.commit()
    await db.refresh(item)
    return DinsarTaskItem.model_validate(item)


@router.post("/task-batches/ps", response_model=PsTaskBatch)
async def create_ps_batch_endpoint(
    request: PsBatchCreateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    if not request.stack:
        raise HTTPException(status_code=400, detail="No PS items provided.")

    request_plan_id = (
        request.planning_context.get("plan_id")
        if isinstance(request.planning_context, dict)
        else None
    )
    explicit_plan_id = str(request.plan_id or request_plan_id or "").strip() or None
    inferred_plan_ids = sorted(
        {
            str(item.stack_plan_id or "").strip()
            for item in request.stack
            if str(item.stack_plan_id or "").strip()
        }
    )
    if len(inferred_plan_ids) > 1:
        raise HTTPException(status_code=400, detail="PS stack items belong to multiple stack plans.")
    if explicit_plan_id and inferred_plan_ids and explicit_plan_id != inferred_plan_ids[0]:
        raise HTTPException(status_code=400, detail="request.plan_id does not match stack scene plan metadata.")

    effective_plan_id = explicit_plan_id or (inferred_plan_ids[0] if inferred_plan_ids else None)
    plan: Optional[TimeseriesStackPlanORM] = None
    plan_items: List[TimeseriesStackPlanItemORM] = []
    plan_edges: List[TimeseriesStackPlanEdgeORM] = []
    plan_item_by_id: Dict[int, TimeseriesStackPlanItemORM] = {}
    plan_item_by_scene_id: Dict[int, TimeseriesStackPlanItemORM] = {}
    plan_item_by_path: Dict[str, TimeseriesStackPlanItemORM] = {}
    planning_context = request.planning_context if isinstance(request.planning_context, dict) else None

    if effective_plan_id:
        plan_result = await db.execute(
            select(TimeseriesStackPlanORM).where(TimeseriesStackPlanORM.plan_id == effective_plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            raise HTTPException(status_code=404, detail=f"Timeseries stack plan not found: {effective_plan_id}")
        if (
            str(request.direction or "").strip()
            and str(plan.direction or "").strip()
            and str(request.direction).strip().upper() != str(plan.direction).strip().upper()
        ):
            raise HTTPException(status_code=400, detail="request.direction does not match the referenced stack plan.")

        items_result = await db.execute(
            select(TimeseriesStackPlanItemORM)
            .where(TimeseriesStackPlanItemORM.plan_ref_id == plan.id)
            .order_by(TimeseriesStackPlanItemORM.scene_rank.asc(), TimeseriesStackPlanItemORM.id.asc())
        )
        plan_items = items_result.scalars().all()
        edges_result = await db.execute(
            select(TimeseriesStackPlanEdgeORM)
            .where(TimeseriesStackPlanEdgeORM.plan_ref_id == plan.id)
            .order_by(TimeseriesStackPlanEdgeORM.edge_rank.asc(), TimeseriesStackPlanEdgeORM.id.asc())
        )
        plan_edges = edges_result.scalars().all()
        plan_item_by_id = {int(item.id): item for item in plan_items if item.id is not None}
        plan_item_by_scene_id = {
            int(item.radar_data_ref_id): item
            for item in plan_items
            if item.radar_data_ref_id is not None
        }
        plan_item_by_path = {
            _normalize_lookup_key(item.file_path): item
            for item in plan_items
            if _normalize_lookup_key(item.file_path)
        }
        plan_context = _build_plan_context(plan, plan_items, plan_edges)
        if not planning_context:
            planning_context = plan_context
        else:
            merged_context = {
                **plan_context,
                **planning_context,
            }
            if "scenes" not in planning_context:
                merged_context["scenes"] = plan_context.get("scenes") or []
            if "network_edges" not in planning_context:
                merged_context["network_edges"] = plan_context.get("network_edges") or []
            planning_context = merged_context

    batch_id = str(uuid.uuid4())
    batch_name = request.name or f"PS_{(request.direction or 'STACK')}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    batch = PsTaskBatchORM(
        batch_id=batch_id,
        name=batch_name,
        direction=request.direction,
        plan_id=plan.plan_id if plan is not None else effective_plan_id,
        plan_strategy=(
            (plan.strategy if plan is not None else None)
            or (planning_context or {}).get("strategy")
        ),
        status="PENDING",
        total_items=len(request.stack),
        completed_items=0,
    )
    db.add(batch)

    for img in request.stack:
        matched_plan_item: Optional[TimeseriesStackPlanItemORM] = None
        if img.stack_plan_item_id is not None and int(img.stack_plan_item_id) in plan_item_by_id:
            matched_plan_item = plan_item_by_id[int(img.stack_plan_item_id)]
        elif img.id is not None and int(img.id) in plan_item_by_scene_id:
            matched_plan_item = plan_item_by_scene_id[int(img.id)]
        else:
            matched_plan_item = plan_item_by_path.get(_normalize_lookup_key(img.file_path))
        if batch.plan_id and matched_plan_item is None:
            raise HTTPException(
                status_code=400,
                detail=f"PS stack scene is not part of referenced stack plan: {img.file_path}",
            )

        remark_payload = None
        if planning_context:
            planning_summary = {
                key: value
                for key, value in planning_context.items()
                if key not in {"scenes", "network_edges"}
            }
            remark_payload = {
                **planning_summary,
                "plan_id": batch.plan_id,
                "plan_item_id": int(matched_plan_item.id) if matched_plan_item and matched_plan_item.id is not None else None,
                "scene_id": img.id,
                "scene_file_path": img.file_path,
                "scene_imaging_date": img.imaging_date,
                "scene_satellite": img.satellite,
            }
        item = PsTaskItemORM(
            batch_id=batch_id,
            plan_item_ref_id=(
                int(matched_plan_item.id)
                if matched_plan_item is not None and matched_plan_item.id is not None
                else None
            ),
            file_path=img.file_path,
            satellite=img.satellite,
            imaging_date=img.imaging_date,
            polarization=img.polarization,
            has_orbit_data=bool(img.has_orbit_data),
            status="PENDING",
            remark=json.dumps(remark_payload, ensure_ascii=False) if remark_payload else None,
        )
        db.add(item)

    await _add_operation_audit_log(
        db,
        request=http_request,
        action="batch_created",
        resource=f"task-batches/ps/{batch_id}",
        detail={
            "batch_name": batch_name,
            "items": len(request.stack),
            "direction": request.direction,
            "plan_id": batch.plan_id,
            "plan_strategy": batch.plan_strategy,
            "planning_context": planning_context,
        },
    )
    await db.commit()
    await db.refresh(batch)
    return PsTaskBatch.model_validate(batch)


@router.get("/task-batches/ps", response_model=List[PsTaskBatch])
async def list_ps_batches_endpoint(
    limit: int = TASK_BATCH_LIST_DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    safe_limit, safe_offset = _normalize_list_pagination(limit, offset)
    result = await db.execute(
        select(PsTaskBatchORM)
        .order_by(PsTaskBatchORM.created_at.desc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    return [PsTaskBatch.model_validate(b) for b in result.scalars().all()]


@router.get("/task-batches/ps/{batch_id}/items", response_model=List[PsTaskItem])
async def list_ps_batch_items_endpoint(
    batch_id: str,
    limit: int = TASK_BATCH_LIST_DEFAULT_LIMIT,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    safe_limit, safe_offset = _normalize_list_pagination(limit, offset)
    result = await db.execute(
        select(PsTaskItemORM).where(PsTaskItemORM.batch_id == batch_id)
        .order_by(PsTaskItemORM.id.asc())
        .offset(safe_offset)
        .limit(safe_limit)
    )
    return [PsTaskItem.model_validate(i) for i in result.scalars().all()]


@router.patch("/task-batches/ps/{batch_id}/complete-all", response_model=PsTaskBatch)
async def complete_ps_batch_endpoint(
    batch_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    await db.execute(
        PsTaskItemORM.__table__.update()
        .where(PsTaskItemORM.batch_id == batch_id)
        .values(status="COMPLETED")
    )
    await _refresh_ps_batch_summary(db, batch_id)
    await db.commit()
    batch = await db.execute(select(PsTaskBatchORM).where(PsTaskBatchORM.batch_id == batch_id))
    batch_obj = batch.scalar_one_or_none()
    if not batch_obj:
        raise HTTPException(status_code=404, detail="Batch not found.")
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="batch_marked_complete",
        resource=f"task-batches/ps/{batch_id}",
        detail={"status": "COMPLETED", "items_completed": batch_obj.completed_items},
    )
    await db.commit()
    await db.refresh(batch_obj)
    return PsTaskBatch.model_validate(batch_obj)


@router.patch("/task-batches/ps/items/{item_id}", response_model=PsTaskItem)
async def update_ps_item_endpoint(
    item_id: int,
    request: BatchItemUpdateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    result = await db.execute(select(PsTaskItemORM).where(PsTaskItemORM.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found.")

    normalized_status = _normalize_batch_item_status(request.status)
    if normalized_status is not None:
        item.status = normalized_status
    if request.remark is not None:
        item.remark = request.remark

    await _refresh_ps_batch_summary(db, item.batch_id)
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="batch_item_updated",
        resource=f"task-batches/ps/items/{item_id}",
        detail={"batch_id": item.batch_id, "status": item.status},
    )
    await db.commit()
    await db.refresh(item)
    return PsTaskItem.model_validate(item)
