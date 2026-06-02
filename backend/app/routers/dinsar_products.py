from __future__ import annotations

import mimetypes
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import read_int_env
from ..database import get_db
from ..models import AuthUserORM
from ..services.job_queue_service import job_queue_service
from ..services.result_catalog_service import (
    JOB_TYPE_PUBLISH_DINSAR_PRODUCTS,
    JOB_TYPE_REBUILD_DINSAR_CATALOG,
    TASK_TYPE_PUBLISH_DINSAR_PRODUCTS,
    TASK_TYPE_REBUILD_DINSAR_CATALOG,
    result_catalog_service,
)
from ..services.task_service import task_service
from .dependencies import (
    _add_operation_audit_log,
    _get_current_user,
    _require_admin,
    _validate_export_path,
    _validate_root_dir,
)


router = APIRouter()

MAX_SCAN_DIRECTORY_COUNT = read_int_env(
    "MAX_SCAN_DIRECTORY_COUNT",
    64,
    minimum=1,
    maximum=500,
)
MAX_SCAN_PATH_LENGTH = read_int_env(
    "MAX_SCAN_PATH_LENGTH",
    2048,
    minimum=64,
    maximum=32767,
)


class DinsarProductPublishRequest(BaseModel):
    source_directories: List[str]
    publish_root: Optional[str] = None
    rebuild_catalog: bool = True

    @field_validator("source_directories", mode="before")
    @classmethod
    def _validate_source_directories(cls, value):
        if value is None or not isinstance(value, list):
            raise ValueError("source_directories must be a list")
        normalized: List[str] = []
        for raw in value:
            path = str(raw or "").strip()
            if not path:
                continue
            if len(path) > MAX_SCAN_PATH_LENGTH:
                raise ValueError(
                    f"source_directories contains a path longer than {MAX_SCAN_PATH_LENGTH} characters."
                )
            if path not in normalized:
                normalized.append(path)
        if not normalized:
            raise ValueError("source_directories cannot be empty")
        if len(normalized) > MAX_SCAN_DIRECTORY_COUNT:
            raise ValueError(
                f"source_directories exceeds max directory count ({MAX_SCAN_DIRECTORY_COUNT})."
            )
        return normalized

    @field_validator("publish_root", mode="before")
    @classmethod
    def _validate_publish_root(cls, value):
        if value is None:
            return None
        path = str(value).strip()
        if not path:
            return None
        if len(path) > MAX_SCAN_PATH_LENGTH:
            raise ValueError(f"publish_root exceeds max length ({MAX_SCAN_PATH_LENGTH})")
        return path


class DinsarCatalogRebuildRequest(BaseModel):
    publish_root: Optional[str] = None
    full_rebuild: bool = True

    @field_validator("publish_root", mode="before")
    @classmethod
    def _validate_publish_root(cls, value):
        if value is None:
            return None
        path = str(value).strip()
        if not path:
            return None
        if len(path) > MAX_SCAN_PATH_LENGTH:
            raise ValueError(f"publish_root exceeds max length ({MAX_SCAN_PATH_LENGTH})")
        return path


@router.get("/dinsar-products/catalog-status")
async def get_dinsar_catalog_status(
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await result_catalog_service.get_catalog_status(db)


@router.post("/dinsar-products/publish", status_code=202)
async def queue_dinsar_product_publish(
    request: DinsarProductPublishRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    source_dirs = [_validate_root_dir(path, "source_directories") for path in request.source_directories]
    publish_root = request.publish_root
    if publish_root:
        publish_root = _validate_export_path(publish_root, "publish_root")

    task_id = await task_service.create_task(
        TASK_TYPE_PUBLISH_DINSAR_PRODUCTS,
        "D-InSAR 结果发布",
        params={
            "source_directories": source_dirs,
            "publish_root": publish_root,
            "rebuild_catalog": request.rebuild_catalog,
        },
        db=db,
    )
    await job_queue_service.create_job(
        JOB_TYPE_PUBLISH_DINSAR_PRODUCTS,
        payload={
            "source_directories": source_dirs,
            "publish_root": publish_root,
            "rebuild_catalog": request.rebuild_catalog,
        },
        task_id=task_id,
        db=db,
    )
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="dinsar_products_publish_queued",
        resource="dinsar-products/publish",
        detail={
            "task_id": task_id,
            "directory_count": len(source_dirs),
            "rebuild_catalog": request.rebuild_catalog,
        },
    )
    await db.commit()
    return {
        "message": "D-InSAR 结果发布任务已入队",
        "task_id": task_id,
    }


@router.post("/dinsar-products/rebuild", status_code=202)
async def queue_dinsar_catalog_rebuild(
    request: DinsarCatalogRebuildRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    _ = admin_user
    publish_root = request.publish_root
    if publish_root:
        publish_root = _validate_export_path(publish_root, "publish_root")

    task_id = await task_service.create_task(
        TASK_TYPE_REBUILD_DINSAR_CATALOG,
        "D-InSAR 结果目录重建",
        params={
            "publish_root": publish_root,
            "full_rebuild": request.full_rebuild,
        },
        db=db,
    )
    await job_queue_service.create_job(
        JOB_TYPE_REBUILD_DINSAR_CATALOG,
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
        action="dinsar_catalog_rebuild_queued",
        resource="dinsar-products/rebuild",
        detail={
            "task_id": task_id,
            "full_rebuild": request.full_rebuild,
        },
    )
    await db.commit()
    return {
        "message": "D-InSAR 结果目录重建任务已入队",
        "task_id": task_id,
    }


@router.get("/dinsar-products")
async def list_dinsar_products(
    limit: int = 100,
    offset: int = 0,
    engine_code: Optional[str] = None,
    status: Optional[str] = None,
    query: Optional[str] = None,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await result_catalog_service.list_products(
        db,
        limit=limit,
        offset=offset,
        engine_code=engine_code,
        status=status,
        query=query,
    )


@router.get("/dinsar-products/{product_db_id}")
async def get_dinsar_product_detail(
    product_db_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    detail = await result_catalog_service.get_product_detail(db, product_db_id=product_db_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Result product not found")
    return detail


@router.get("/dinsar-products/{product_db_id}/preview")
async def get_dinsar_product_preview(
    product_db_id: int,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    detail = await result_catalog_service.get_product_detail(db, product_db_id=product_db_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Result product not found")
    preview_path = str(detail.get("preview_path") or "").strip()
    if not preview_path or not os.path.isfile(preview_path):
        raise HTTPException(status_code=404, detail="Preview not found")
    media_type = mimetypes.guess_type(preview_path)[0] or "application/octet-stream"
    return FileResponse(preview_path, media_type=media_type)
