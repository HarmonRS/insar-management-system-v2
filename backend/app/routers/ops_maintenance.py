from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuthUserORM
from ..services.ops_maintenance_service import ops_maintenance_service
from .dependencies import _add_operation_audit_log, _get_current_user, _require_admin


router = APIRouter(prefix="/ops-maintenance", tags=["ops-maintenance"])


class CleanupRequest(BaseModel):
    confirm: bool = False
    delete_task_records: bool = True
    delete_logs: bool = True
    delete_production_records: bool = True
    delete_result_products: bool = True
    delete_production_dirs: bool = True
    delete_task_pool_dir: bool = True


@router.get("/tasks")
async def list_maintenance_tasks(
    task_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await ops_maintenance_service.list_tasks(
        db,
        task_type=task_type,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get("/tasks/{task_id}/diagnosis")
async def diagnose_maintenance_task(
    task_id: str,
    _user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    diagnosis = await ops_maintenance_service.diagnose_task(db, task_id)
    if diagnosis is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return diagnosis


@router.post("/tasks/{task_id}/cleanup-preview")
async def preview_maintenance_cleanup(
    task_id: str,
    _user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    preview = await ops_maintenance_service.cleanup_preview(db, task_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return preview


@router.post("/tasks/{task_id}/cleanup")
async def cleanup_maintenance_task(
    task_id: str,
    request_body: CleanupRequest,
    request: Request,
    admin_user: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    if not request_body.confirm:
        raise HTTPException(status_code=400, detail="Cleanup confirmation is required.")
    try:
        result = await ops_maintenance_service.cleanup_task(
            db,
            task_id,
            options=request_body.model_dump(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    await _add_operation_audit_log(
        db,
        request,
        action="ops_task_cleanup",
        resource=f"ops-maintenance/tasks/{task_id}",
        detail={
            "task_id": task_id,
            "options": request_body.model_dump(),
            "result": result,
        },
        user=admin_user,
    )
    await db.commit()
    return result
