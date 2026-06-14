from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM, SystemTaskORM
from ..services.job_queue_service import job_queue_service
from ..services.sbas_insar_catalog_service import (
    JOB_TYPE_REBUILD_SBAS_INSAR_CATALOG,
    TASK_TYPE_REBUILD_SBAS_INSAR_CATALOG,
    sbas_insar_catalog_service,
)
from ..services.task_service import task_service
from .dependencies import _add_operation_audit_log, _get_current_user, _require_admin


router = APIRouter()
STATIC_ASSET_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


class SbasInsarCatalogRebuildRequest(BaseModel):
    full_rebuild: bool = True


class SbasInsarPointTimeseriesRequest(BaseModel):
    lon: float
    lat: float


async def _get_active_sbas_catalog_rebuild_task(db: AsyncSession) -> SystemTaskORM | None:
    result = await db.execute(
        select(SystemTaskORM)
        .where(
            SystemTaskORM.task_type == TASK_TYPE_REBUILD_SBAS_INSAR_CATALOG,
            SystemTaskORM.status.in_(["PENDING", "RUNNING"]),
        )
        .order_by(SystemTaskORM.created_at.desc(), SystemTaskORM.id.desc())
        .limit(1)
    )
    return result.scalars().first()


@router.get("/sbas-insar-products/catalog-status")
async def get_sbas_insar_catalog_status(
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await sbas_insar_catalog_service.get_catalog_status(db)


@router.post("/sbas-insar-products/rebuild", status_code=202)
async def queue_sbas_insar_catalog_rebuild(
    request: SbasInsarCatalogRebuildRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    existing_task = await _get_active_sbas_catalog_rebuild_task(db)
    if existing_task is not None:
        await _add_operation_audit_log(
            db,
            request=http_request,
            action="sbas_insar_catalog_rebuild_already_running",
            resource="sbas-insar-products/rebuild",
            detail={"task_id": existing_task.task_id, "full_rebuild": request.full_rebuild},
        )
        await db.commit()
        return {
            "message": "SBAS-InSAR result catalog rebuild is already queued or running.",
            "task_id": existing_task.task_id,
            "already_running": True,
        }

    try:
        task_id = await task_service.create_task(
            TASK_TYPE_REBUILD_SBAS_INSAR_CATALOG,
            "SBAS-InSAR result catalog rebuild",
            params={"full_rebuild": request.full_rebuild},
            db=db,
        )
    except ValueError as exc:
        await db.rollback()
        existing_task = await _get_active_sbas_catalog_rebuild_task(db)
        if existing_task is not None:
            return {
                "message": "SBAS-InSAR result catalog rebuild is already queued or running.",
                "task_id": existing_task.task_id,
                "already_running": True,
            }
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await job_queue_service.create_job(
        JOB_TYPE_REBUILD_SBAS_INSAR_CATALOG,
        payload={"full_rebuild": request.full_rebuild},
        task_id=task_id,
        db=db,
    )
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="sbas_insar_catalog_rebuild_queued",
        resource="sbas-insar-products/rebuild",
        detail={"task_id": task_id, "full_rebuild": request.full_rebuild},
    )
    await db.commit()
    return {
        "message": "SBAS-InSAR result catalog rebuild has been queued.",
        "task_id": task_id,
    }


@router.get("/sbas-insar-products")
async def list_sbas_insar_products(
    limit: int = 100,
    offset: int = 0,
    status: str | None = None,
    query: str | None = None,
    admin_region: str | None = None,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await sbas_insar_catalog_service.list_products(
        db,
        limit=limit,
        offset=offset,
        status=status,
        query=query,
        admin_region=admin_region,
    )


@router.get("/sbas-insar-products/{product_db_id}")
async def get_sbas_insar_product_detail(
    product_db_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    detail = await sbas_insar_catalog_service.get_product_detail(db, product_db_id=product_db_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="SBAS-InSAR product not found")
    return detail


@router.post("/sbas-insar-products/{product_db_id}/point-timeseries")
async def query_sbas_insar_point_timeseries(
    product_db_id: int,
    request: SbasInsarPointTimeseriesRequest,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    try:
        detail = await sbas_insar_catalog_service.query_point_timeseries(
            db,
            product_db_id=product_db_id,
            lon=request.lon,
            lat=request.lat,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="SBAS-InSAR product not found")
    return detail


@router.get("/sbas-insar-products/{product_db_id}/preview")
async def get_sbas_insar_product_preview(
    product_db_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    detail = await sbas_insar_catalog_service.get_product_detail(db, product_db_id=product_db_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="SBAS-InSAR product not found")
    preview_path = str(detail.get("preview_path") or "").strip()
    if not preview_path or not os.path.isfile(preview_path):
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(preview_path, media_type="image/png", headers=STATIC_ASSET_CACHE_HEADERS)


@router.get("/sbas-insar-products/{product_db_id}/assets/{asset_id}")
async def get_sbas_insar_product_asset(
    product_db_id: int,
    asset_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    asset = await sbas_insar_catalog_service.get_asset(db, product_db_id=product_db_id, asset_id=asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="SBAS-InSAR product asset not found")
    if not asset.absolute_path or not os.path.isfile(asset.absolute_path):
        raise HTTPException(status_code=404, detail="Asset file not found")
    return FileResponse(
        asset.absolute_path,
        media_type=asset.media_type or "application/octet-stream",
        filename=asset.asset_name or os.path.basename(asset.absolute_path),
        headers=STATIC_ASSET_CACHE_HEADERS,
    )
