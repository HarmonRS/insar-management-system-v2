from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..services.manifest_inventory_service import manifest_inventory_service
from ..services.root_registry_service import root_registry_service
from .dependencies import _get_current_user, _require_admin


router = APIRouter()


@router.get("/managed-roots")
async def get_managed_roots(
    include_disabled: bool = False,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await root_registry_service.get_status(db, include_disabled=include_disabled)


@router.get("/managed-roots/summary")
async def get_managed_roots_summary(
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    return await root_registry_service.get_summary(db)


@router.get("/managed-roots/{root_id}/inventory")
async def get_managed_root_inventory(
    root_id: int,
    include_removed: bool = False,
    limit: int = 200,
    offset: int = 0,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _ = current_user
    try:
        return await manifest_inventory_service.list_inventory(
            db,
            root_id=root_id,
            limit=limit,
            offset=offset,
            include_removed=include_removed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/managed-roots/rescan-manifests")
async def rescan_manifest_roots(
    root_id: int | None = None,
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    _ = admin_user
    return await manifest_inventory_service.sync_manifest_roots(db, root_id=root_id)
