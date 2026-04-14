from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth_service import ROLE_ADMIN
from ..database import get_db
from ..services.health_service import get_health_status, _check_dinsar_engines
from .dependencies import _get_optional_session_user, _require_admin
from ..models import AuthUserORM

router = APIRouter()


@router.get("/health")
async def get_health(
    request: Request,
    full: bool = Query(False),
    refresh: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """
    运维自检接口（无需授权）。
    """
    session_user = await _get_optional_session_user(request, db)
    include_details = bool(full and session_user and session_user.role == ROLE_ADMIN)
    return await get_health_status(
        include_details=include_details,
        full=include_details,
        refresh=refresh,
    )


@router.get("/health/dinsar-engines")
async def get_dinsar_engines_health(
    current_user: AuthUserORM = Depends(_require_admin),
):
    """D-InSAR 引擎专项健康检查（管理员）。"""
    return await _check_dinsar_engines()
