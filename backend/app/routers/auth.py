from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from datetime import datetime

from sqlalchemy import delete

from ..auth_service import (
    ROLE_ADMIN,
    VALID_ROLES,
    SESSION_COOKIE_NAME,
    add_audit_log,
    authenticate_user,
    create_session,
    create_user,
    get_cookie_options,
    get_user_by_session_token,
    list_users,
    revoke_session,
    to_user_payload,
    update_user,
)
from ..database import get_db
from ..models import AuthAuditLogInfo, AuthAuditLogORM, AuthSessionORM, AuthUserInfo, AuthUserORM
from .dependencies import (
    _add_operation_audit_log,
    _build_login_throttle_key,
    _clear_login_failure_state,
    _get_client_ip,
    _get_current_user,
    _get_login_retry_after_seconds,
    _record_login_failure,
    _require_admin,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    is_active: bool = True


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


@router.post("/auth/login")
async def auth_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    client_ip = _get_client_ip(request)
    submitted_username = (payload.username or "").strip()
    throttle_key = _build_login_throttle_key(submitted_username, client_ip)

    retry_after = await _get_login_retry_after_seconds(throttle_key, db)
    if retry_after > 0:
        await add_audit_log(
            db,
            action="login_blocked_rate_limit",
            resource="auth/login",
            detail={"username": submitted_username, "retry_after_seconds": retry_after},
            ip_address=client_ip,
        )
        await db.commit()
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    user = await authenticate_user(db, payload.username, payload.password)
    if not user:
        lock_retry_after = await _record_login_failure(throttle_key, db)
        audit_action = "login_failed"
        error_status = 401
        error_detail = "Invalid username or password."
        audit_detail = {"username": submitted_username}

        if lock_retry_after > 0:
            audit_action = "login_rate_limited"
            error_status = 429
            error_detail = f"Too many failed login attempts. Try again in {lock_retry_after} seconds."
            audit_detail["retry_after_seconds"] = lock_retry_after

        await add_audit_log(
            db,
            action=audit_action,
            resource="auth/login",
            detail=audit_detail,
            ip_address=client_ip,
        )
        await db.commit()
        if lock_retry_after > 0:
            raise HTTPException(
                status_code=error_status,
                detail=error_detail,
                headers={"Retry-After": str(lock_retry_after)},
            )
        raise HTTPException(status_code=error_status, detail=error_detail)

    await _clear_login_failure_state(throttle_key, db)

    token, expires_at = await create_session(
        db,
        user,
        ip_address=client_ip,
        user_agent=request.headers.get("user-agent"),
    )
    await add_audit_log(
        db,
        action="login_success",
        user=user,
        resource="auth/login",
        ip_address=client_ip,
    )
    user_payload = to_user_payload(user)
    await db.commit()

    cookie_opts = get_cookie_options()
    response.set_cookie(value=token, **cookie_opts)
    return {
        "message": "Login successful.",
        "user": user_payload,
        "expires_at": expires_at,
    }


@router.post("/auth/logout")
async def auth_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = await get_user_by_session_token(db, token) if token else None

    revoked = await revoke_session(db, token)
    if user:
        await add_audit_log(
            db,
            action="logout",
            user=user,
            resource="auth/logout",
            ip_address=_get_client_ip(request),
        )
    await db.commit()

    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"message": "Logged out.", "revoked": revoked}


@router.get("/auth/me", response_model=AuthUserInfo)
async def auth_me(current_user: AuthUserORM = Depends(_get_current_user)):
    return AuthUserInfo.model_validate(current_user)


@router.get("/auth/users", response_model=List[AuthUserInfo])
async def auth_list_users(
    _admin: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    users = await list_users(db)
    return [AuthUserInfo.model_validate(user) for user in users]


@router.get("/auth/audit-logs", response_model=List[AuthAuditLogInfo])
async def auth_list_audit_logs(
    limit: int = 200,
    _admin: AuthUserORM = Depends(_require_admin),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(limit, 1000))
    result = await db.execute(
        select(AuthAuditLogORM)
        .order_by(AuthAuditLogORM.created_at.desc(), AuthAuditLogORM.id.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [AuthAuditLogInfo.model_validate(item) for item in logs]


@router.post("/auth/users", response_model=AuthUserInfo, status_code=201)
async def auth_create_user(
    payload: CreateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of: {sorted(VALID_ROLES)}")
    try:
        user = await create_user(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            is_active=payload.is_active,
            created_by=admin_user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await add_audit_log(
        db,
        action="user_created",
        user=admin_user,
        resource=f"auth/users/{user.id}",
        detail={"username": user.username, "role": user.role, "is_active": user.is_active},
        ip_address=_get_client_ip(request),
    )
    await db.commit()
    await db.refresh(user)
    return AuthUserInfo.model_validate(user)


@router.patch("/auth/users/{user_id}", response_model=AuthUserInfo)
async def auth_update_user(
    user_id: int,
    payload: UpdateUserRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    user_result = await db.execute(
        select(AuthUserORM).where(AuthUserORM.id == user_id)
    )
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    if payload.role is not None and payload.role not in VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"role must be one of: {sorted(VALID_ROLES)}")

    if target_user.id == admin_user.id:
        if payload.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate current admin account.")
        if payload.role is not None and payload.role != ROLE_ADMIN:
            raise HTTPException(status_code=400, detail="Cannot remove admin role from current account.")

    try:
        updated = await update_user(
            db,
            target_user,
            role=payload.role,
            is_active=payload.is_active,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    await add_audit_log(
        db,
        action="user_updated",
        user=admin_user,
        resource=f"auth/users/{updated.id}",
        detail={
            "role": updated.role,
            "is_active": updated.is_active,
            "password_updated": payload.password is not None,
        },
        ip_address=_get_client_ip(request),
    )
    await db.commit()
    await db.refresh(updated)
    return AuthUserInfo.model_validate(updated)


@router.post("/auth/cleanup-sessions")
async def auth_cleanup_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    now = datetime.utcnow()
    result = await db.execute(
        delete(AuthSessionORM).where(
            (AuthSessionORM.expires_at <= now) | (AuthSessionORM.is_revoked == True)
        )
    )
    deleted_count = result.rowcount or 0
    await add_audit_log(
        db,
        action="sessions_cleanup",
        user=admin_user,
        resource="auth/cleanup-sessions",
        detail={"deleted_count": deleted_count},
        ip_address=_get_client_ip(request),
    )
    await db.commit()
    return {"message": f"已清理 {deleted_count} 条过期/已撤销会话。", "deleted_count": deleted_count}
