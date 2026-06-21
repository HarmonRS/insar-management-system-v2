from __future__ import annotations

from datetime import datetime
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
    families: List[str] = Field(default_factory=list)
    bind_orbits: bool = True
    build_previews: bool = True


class ArchiveIntegrityAuditRequest(BaseModel):
    families: List[str] = Field(default_factory=list)
    source_formats: List[str] = Field(default_factory=list)
    asset_ids: List[int] = Field(default_factory=list)
    force: bool = False
    limit: Optional[int] = Field(default=None, ge=0)


class S1UnpackRequest(BaseModel):
    target_root: Optional[str] = None
    overwrite: bool = False
    min_disk_space_gb: Optional[float] = Field(default=None, ge=0)


class S1BatchUnpackRequest(BaseModel):
    target_root: Optional[str] = None
    overwrite: bool = False
    min_disk_space_gb: Optional[float] = Field(default=None, ge=0)
    scan_before_unpack: bool = True


class SourceMaterializeRequest(BaseModel):
    target_root: Optional[str] = None
    overwrite: bool = False


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
        families=payload.get("families") or None,
        bind_orbits=bool(payload.get("bind_orbits", True)),
        build_previews=bool(payload.get("build_previews", True)),
    )


@router.post("/inventory/archive-integrity-audit", status_code=202)
async def run_archive_integrity_audit(
    request: Optional[ArchiveIntegrityAuditRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    payload = (request or ArchiveIntegrityAuditRequest()).model_dump()
    try:
        task_id = await task_service.create_task(
            "AUDIT_SOURCE_ARCHIVE_INTEGRITY",
            "Source archive integrity audit",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            "AUDIT_SOURCE_ARCHIVE_INTEGRITY",
            payload=payload,
            task_id=task_id,
        )
        return {"message": "Source archive integrity audit queued", "task_id": task_id, "job_id": job_id}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/inventory/archive-integrity-audit-now")
async def run_archive_integrity_audit_now(
    request: Optional[ArchiveIntegrityAuditRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = admin_user
    payload = (request or ArchiveIntegrityAuditRequest()).model_dump()
    return await asset_inventory_service.audit_source_archive_integrity(
        db,
        families=payload.get("families") or None,
        source_formats=payload.get("source_formats") or None,
        asset_ids=payload.get("asset_ids") or None,
        force=bool(payload.get("force", False)),
        limit=payload.get("limit"),
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


@router.post("/sources/{asset_id}/materialize")
async def materialize_source_asset(
    asset_id: int,
    request: Optional[SourceMaterializeRequest] = None,
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = admin_user
    asset = await db.get(SourceProductAssetORM, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Source product asset not found.")
    request_data = request or SourceMaterializeRequest()
    try:
        result = asset_inventory_service.materialize_source_asset(
            asset,
            target_root=request_data.target_root,
            overwrite=bool(request_data.overwrite),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    metadata = dict(asset.metadata_json or {})
    metadata["last_materialized_dir"] = result.get("safe_dir") or result.get("target_dir")
    metadata["last_materialized_at"] = datetime.utcnow().isoformat()
    metadata["last_materialized_status"] = result.get("status")
    asset.metadata_json = metadata
    await db.commit()
    return {"message": "Source asset materialized", "result": result}


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
