from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..models.orm import SourceProductAssetORM
from ..services.asset_inventory_service import asset_inventory_service
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from .dependencies import _get_current_user, _require_admin


router = APIRouter(prefix="/assets", tags=["assets"])


class AssetScanRequest(BaseModel):
    inventory_types: List[str] = Field(default_factory=list)
    root_ids: List[int] = Field(default_factory=list)
    bind_orbits: bool = True


class S1UnpackRequest(BaseModel):
    target_root: Optional[str] = None
    overwrite: bool = False
    min_disk_space_gb: Optional[float] = Field(default=None, ge=0)
    delete_archive: Optional[bool] = None


class S1BatchUnpackRequest(BaseModel):
    target_root: Optional[str] = None
    overwrite: bool = False
    min_disk_space_gb: Optional[float] = Field(default=None, ge=0)
    delete_archive: Optional[bool] = None
    scan_before_unpack: bool = True


@router.get("/inventory/status")
async def get_asset_inventory_status(
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await asset_inventory_service.get_status(db)


@router.post("/inventory/scan", status_code=202)
async def run_asset_inventory_scan(
    request: Optional[AssetScanRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    payload = (request or AssetScanRequest()).model_dump()
    try:
        task_id = await task_service.create_task(
            "SCAN_ASSET_INVENTORY",
            "Source/orbit asset inventory scan",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            "SCAN_ASSET_INVENTORY",
            payload=payload,
            task_id=task_id,
        )
        return {"message": "Asset inventory scan queued", "task_id": task_id, "job_id": job_id}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/inventory/scan-now")
async def run_asset_inventory_scan_now(
    request: Optional[AssetScanRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = admin_user
    payload = (request or AssetScanRequest()).model_dump()
    return await asset_inventory_service.scan_configured_roots(
        db,
        inventory_types=payload.get("inventory_types") or None,
        root_ids=payload.get("root_ids") or None,
        bind_orbits=bool(payload.get("bind_orbits", True)),
    )


@router.get("/sources")
async def list_source_product_assets(
    satellite_family: Optional[str] = None,
    satellite: Optional[str] = None,
    source_format: Optional[str] = None,
    parse_status: Optional[str] = None,
    include_inactive: bool = False,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await asset_inventory_service.list_source_products(
        db,
        satellite_family=satellite_family,
        satellite=satellite,
        source_format=source_format,
        parse_status=parse_status,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/orbits")
async def list_orbit_assets(
    satellite_family: Optional[str] = None,
    satellite: Optional[str] = None,
    orbit_type: Optional[str] = None,
    parse_status: Optional[str] = None,
    include_inactive: bool = False,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await asset_inventory_service.list_orbits(
        db,
        satellite_family=satellite_family,
        satellite=satellite,
        orbit_type=orbit_type,
        parse_status=parse_status,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )


@router.get("/issues")
async def list_asset_inventory_issues(
    status: str = "OPEN",
    severity: Optional[str] = None,
    issue_code: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await asset_inventory_service.list_issues(
        db,
        status=status,
        severity=severity,
        issue_code=issue_code,
        limit=limit,
        offset=offset,
    )


@router.post("/sources/{asset_id}/unpack-sentinel1")
async def unpack_sentinel1_source_asset(
    asset_id: int,
    request: Optional[S1UnpackRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = admin_user
    asset = await db.get(SourceProductAssetORM, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Source product asset not found.")
    if asset.source_format != "S1_ZIP":
        raise HTTPException(status_code=400, detail="Only Sentinel-1 ZIP assets can be unpacked by this endpoint.")

    request_data = request or S1UnpackRequest()
    payload = {
        "asset_id": asset_id,
        "target_root": request_data.target_root,
        "overwrite": bool(request_data.overwrite),
    }
    if request_data.min_disk_space_gb is not None:
        payload["min_disk_space_gb"] = request_data.min_disk_space_gb
    if request_data.delete_archive is not None:
        payload["delete_archive"] = request_data.delete_archive
    try:
        task_id = await task_service.create_task(
            "UNPACK_SENTINEL1",
            "Sentinel-1 unpack",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            "UNPACK_SENTINEL1",
            payload=payload,
            task_id=task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return {"message": "Sentinel-1 unpack task queued", "task_id": task_id, "job_id": job_id}


@router.post("/inventory/unpack-sentinel1", status_code=202)
async def run_sentinel1_unpack_batch(
    request: Optional[S1BatchUnpackRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    payload = (request or S1BatchUnpackRequest()).model_dump()
    try:
        task_id = await task_service.create_task(
            "UNPACK_SENTINEL1",
            "Sentinel-1 batch unpack",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            "UNPACK_SENTINEL1",
            payload=payload,
            task_id=task_id,
        )
        return {"message": "Sentinel-1 batch unpack task queued", "task_id": task_id, "job_id": job_id}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
