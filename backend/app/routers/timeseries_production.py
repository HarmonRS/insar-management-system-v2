from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..services.timeseries_service import timeseries_service
from .dependencies import _get_current_user, _require_admin


router = APIRouter(prefix="/timeseries-production", tags=["timeseries-production"])


class TimeseriesRunCreateRequest(BaseModel):
    batch_id: str = Field(..., description="PS batch id")
    run_name: Optional[str] = Field(default=None, max_length=255)
    reference_date: Optional[str] = Field(default=None, pattern=r"^\d{8}$|^$")
    water_mask_mode: str = Field(default="synthetic_fallback", max_length=64)
    processor_code: str = Field(default="isce2_stack_mintpy", max_length=64)
    execution_mode: Optional[str] = Field(default=None, max_length=32)
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("batch_id", mode="before")
    @classmethod
    def _validate_batch_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("batch_id is required")
        return text


class TimeseriesWslCheckRequest(BaseModel):
    distro: Optional[str] = Field(default=None, max_length=128)
    smoke_test: bool = Field(default=False)

    @field_validator("distro", mode="before")
    @classmethod
    def _normalize_distro(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class TimeseriesPreflightRequest(BaseModel):
    batch_id: str = Field(..., description="PS batch id")
    reference_date: Optional[str] = Field(default=None, pattern=r"^\d{8}$|^$")
    water_mask_mode: str = Field(default="synthetic_fallback", max_length=64)

    @field_validator("batch_id", mode="before")
    @classmethod
    def _validate_batch_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("batch_id is required")
        return text


class SarscapeSbasPreflightRequest(BaseModel):
    batch_id: str = Field(..., description="PS batch id")
    reference_date: Optional[str] = Field(default=None, pattern=r"^\d{8}$|^$")
    include_task_discovery: bool = True
    discovery_timeout_seconds: int = Field(default=120, ge=10, le=600)

    @field_validator("batch_id", mode="before")
    @classmethod
    def _validate_batch_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("batch_id is required")
        return text


class TimeseriesRetryStepRequest(BaseModel):
    step_id: str = Field(..., max_length=128)

    @field_validator("step_id", mode="before")
    @classmethod
    def _validate_step_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("step_id is required")
        return text


@router.post("/runs", status_code=202)
async def create_timeseries_run(
    request: TimeseriesRunCreateRequest,
    current_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await timeseries_service.create_run(
            batch_id=request.batch_id,
            run_name=request.run_name,
            reference_date=request.reference_date,
            water_mask_mode=request.water_mask_mode,
            processor_code=request.processor_code,
            execution_mode=request.execution_mode,
            notes=request.notes,
            created_by=getattr(current_user, "username", None),
            db=db,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "冲突" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/wsl-check")
async def run_timeseries_wsl_check(
    request: TimeseriesWslCheckRequest,
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    try:
        return await timeseries_service.get_runtime_report(
            distro=request.distro,
            smoke_test=request.smoke_test,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/preflight")
async def run_timeseries_preflight(
    request: TimeseriesPreflightRequest,
    current_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    try:
        return await timeseries_service.get_preflight_report(
            batch_id=request.batch_id,
            reference_date=request.reference_date,
            water_mask_mode=request.water_mask_mode,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sarscape-sbas/preflight")
async def run_sarscape_sbas_preflight(
    request: SarscapeSbasPreflightRequest,
    current_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    try:
        return await timeseries_service.get_sarscape_sbas_preflight_report(
            batch_id=request.batch_id,
            reference_date=request.reference_date,
            include_task_discovery=request.include_task_discovery,
            discovery_timeout_seconds=request.discovery_timeout_seconds,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs")
async def list_timeseries_runs(
    limit: int = 50,
    offset: int = 0,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await timeseries_service.list_runs(db, limit=limit, offset=offset)


@router.get("/runs/{run_id}")
async def get_timeseries_run_detail(
    run_id: str,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    detail = await timeseries_service.get_run_detail(db, run_id=run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Timeseries run not found")
    return detail


@router.get("/runs/{run_id}/prepared-stack")
async def get_timeseries_run_prepared_stack(
    run_id: str,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    summary = await timeseries_service.get_prepared_stack_summary(db, run_id=run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Timeseries run not found")
    return summary


@router.post("/runs/{run_id}/retry-step", status_code=202)
async def retry_timeseries_run_step(
    run_id: str,
    request: TimeseriesRetryStepRequest,
    current_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    try:
        return await timeseries_service.retry_step(
            run_id,
            step_id=request.step_id,
            db=db,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "cannot be retried" in message or "running steps" in message else 400
        if "not found" in message:
            status_code = 404
        raise HTTPException(status_code=status_code, detail=message) from exc
