from __future__ import annotations

import mimetypes
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..services.result_delivery_service import result_delivery_service
from .dependencies import _add_operation_audit_log, _get_current_user


router = APIRouter()


class ResultDeliveryCreateRequest(BaseModel):
    channel: str
    product_ids: Optional[List[int]] = None
    compat_result_ids: Optional[List[int]] = None
    item_ids: Optional[List[int]] = None
    package_mode: str = "directory"
    include_checksums: Optional[bool] = None

    @field_validator("channel", mode="before")
    @classmethod
    def _validate_channel(cls, value):
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError("channel is required")
        return text

    @field_validator("package_mode", mode="before")
    @classmethod
    def _validate_package_mode(cls, value):
        text = str(value or "directory").strip().lower()
        if text not in {"directory", "zip"}:
            raise ValueError("package_mode must be directory or zip")
        return text


@router.get("/result-deliveries/channels")
async def get_result_delivery_channels(
    current_user: AuthUserORM = Depends(_get_current_user),
):
    _ = current_user
    return {"items": result_delivery_service.channels()}


@router.post("/result-deliveries", status_code=202)
async def create_result_delivery(
    request: ResultDeliveryCreateRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    try:
        delivery = await result_delivery_service.create_delivery(
            db,
            user=current_user,
            channel=request.channel,
            product_ids=request.product_ids,
            compat_result_ids=request.compat_result_ids,
            item_ids=request.item_ids,
            package_mode=request.package_mode,
            include_checksums=request.include_checksums,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _add_operation_audit_log(
        db,
        request=http_request,
        action="result_delivery_created",
        resource="result-deliveries",
        detail={
            "delivery_id": delivery.delivery_id,
            "channel": delivery.channel,
            "package_mode": delivery.package_mode,
            "item_count": delivery.item_count,
            "task_id": delivery.task_id,
            "job_id": delivery.job_id,
        },
        user=current_user,
    )
    await db.commit()
    return result_delivery_service.serialize_delivery(delivery)


@router.get("/result-deliveries/catalog/{channel}")
async def list_result_delivery_catalog(
    channel: str,
    limit: int = 100,
    offset: int = 0,
    query: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    _ = current_user
    try:
        return await result_delivery_service.list_channel_catalog(
            db,
            channel=channel,
            limit=limit,
            offset=offset,
            query=query,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/result-deliveries")
async def list_result_deliveries(
    mine: bool = True,
    include_all: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    allow_all = bool(include_all) and str(current_user.role or "").lower() == "admin"
    if mine:
        allow_all = False
    return await result_delivery_service.list_deliveries(
        db,
        user=current_user,
        include_all=allow_all,
        include_items=True,
        item_limit=5,
        limit=limit,
        offset=offset,
    )


@router.get("/result-deliveries/{delivery_id}")
async def get_result_delivery(
    delivery_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    delivery = await result_delivery_service.get_delivery(
        db,
        delivery_id=delivery_id,
        user=current_user,
        include_items=True,
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery not found")
    return result_delivery_service.serialize_delivery(delivery, include_items=True)


@router.get("/result-deliveries/{delivery_id}/manifest")
async def download_result_delivery_manifest(
    delivery_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    try:
        path = await result_delivery_service.resolve_manifest_path(
            db,
            delivery_id=delivery_id,
            user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, filename=os.path.basename(path), media_type="application/json")


@router.get("/result-deliveries/{delivery_id}/archive/download")
async def download_result_delivery_archive(
    delivery_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    try:
        path = await result_delivery_service.resolve_archive_path(
            db,
            delivery_id=delivery_id,
            user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path, filename=os.path.basename(path), media_type="application/zip")


@router.get("/result-deliveries/{delivery_id}/files/{item_id}/download")
async def download_result_delivery_item(
    delivery_id: str,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUserORM = Depends(_get_current_user),
):
    try:
        path = await result_delivery_service.resolve_item_path(
            db,
            delivery_id=delivery_id,
            item_id=item_id,
            user=current_user,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return FileResponse(path, filename=os.path.basename(path), media_type=media_type)
