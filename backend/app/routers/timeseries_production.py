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
    notes: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("batch_id", mode="before")
    @classmethod
    def _validate_batch_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("batch_id is required")
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
            notes=request.notes,
            created_by=getattr(current_user, "username", None),
            db=db,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "冲突" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


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
