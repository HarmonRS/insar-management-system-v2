from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..services.job_queue_service import job_queue_service
from ..services.psinsar_catalog_service import (
    JOB_TYPE_REBUILD_PSINSAR_CATALOG,
    TASK_TYPE_REBUILD_PSINSAR_CATALOG,
    psinsar_catalog_service,
)
from ..services.task_service import task_service
from .dependencies import (
    _add_operation_audit_log,
    _get_current_user,
    _require_admin,
    _validate_export_path,
)


router = APIRouter()


class PsinsarCatalogRebuildRequest(BaseModel):
    publish_root: Optional[str] = None
    full_rebuild: bool = True

    @field_validator("publish_root", mode="before")
    @classmethod
    def _validate_publish_root(cls, value):
        if value is None:
            return None
        path = str(value).strip()
        return path or None


@router.get("/ps-products/catalog-status")
async def get_psinsar_catalog_status(
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await psinsar_catalog_service.get_catalog_status(db)


@router.post("/ps-products/rebuild", status_code=202)
async def queue_psinsar_catalog_rebuild(
    request: PsinsarCatalogRebuildRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    publish_root = request.publish_root
    if publish_root:
        publish_root = _validate_export_path(publish_root, "publish_root")

    task_id = await task_service.create_task(
        TASK_TYPE_REBUILD_PSINSAR_CATALOG,
        "PS-InSAR 结果目录重建",
        params={
            "publish_root": publish_root,
            "full_rebuild": request.full_rebuild,
        },
        db=db,
    )
    await job_queue_service.create_job(
        JOB_TYPE_REBUILD_PSINSAR_CATALOG,
        payload={
            "publish_root": publish_root,
            "full_rebuild": request.full_rebuild,
        },
        task_id=task_id,
        db=db,
    )
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="psinsar_catalog_rebuild_queued",
        resource="ps-products/rebuild",
        detail={
            "task_id": task_id,
            "full_rebuild": request.full_rebuild,
        },
    )
    await db.commit()
    return {
        "message": "PS-InSAR 结果目录重建任务已入队",
        "task_id": task_id,
    }


@router.get("/ps-products")
async def list_psinsar_products(
    limit: int = 100,
    offset: int = 0,
    status: Optional[str] = None,
    query: Optional[str] = None,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await psinsar_catalog_service.list_products(
        db,
        limit=limit,
        offset=offset,
        status=status,
        query=query,
    )


@router.get("/ps-products/{product_db_id}")
async def get_psinsar_product_detail(
    product_db_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    detail = await psinsar_catalog_service.get_product_detail(db, product_db_id=product_db_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="PS-InSAR product not found")
    return detail


@router.get("/ps-products/{product_db_id}/preview")
async def get_psinsar_product_preview(
    product_db_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    detail = await psinsar_catalog_service.get_product_detail(db, product_db_id=product_db_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="PS-InSAR product not found")
    preview_path = str(detail.get("preview_path") or "").strip()
    if not preview_path or not os.path.isfile(preview_path):
        raise HTTPException(status_code=404, detail="Preview not found")
    return FileResponse(
        preview_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
