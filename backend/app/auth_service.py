import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth_utils import (
    generate_session_token,
    hash_password,
    hash_session_token,
    normalize_username,
    validate_password,
    validate_username,
    verify_password,
)
from .config import read_bool_env, read_int_env, settings
from .models import AuthAuditLogORM, AuthSessionORM, AuthUserORM


ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"
VALID_ROLES = {ROLE_ADMIN, ROLE_VIEWER}


SESSION_COOKIE_NAME = settings.AUTH_SESSION_COOKIE_NAME
SESSION_TTL_HOURS = read_int_env("SESSION_TTL_HOURS", 12)
COOKIE_SECURE = read_bool_env("AUTH_COOKIE_SECURE", True)
COOKIE_SAMESITE = (settings.AUTH_COOKIE_SAMESITE or "lax").strip().lower()
if COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    COOKIE_SAMESITE = "lax"


def get_cookie_options() -> Dict[str, Any]:
    max_age = SESSION_TTL_HOURS * 3600
    return {
        "key": SESSION_COOKIE_NAME,
        "httponly": True,
        "secure": COOKIE_SECURE,
        "samesite": COOKIE_SAMESITE,
        "max_age": max_age,
        "path": "/",
    }


def _normalize_role(role: Optional[str]) -> str:
    value = (role or ROLE_VIEWER).strip().lower()
    if value not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")
    return value


async def get_user_by_username(db: AsyncSession, username: str) -> Optional[AuthUserORM]:
    normalized = normalize_username(username)
    if not normalized:
        return None
    result = await db.execute(
        select(AuthUserORM).where(AuthUserORM.username == normalized)
    )
    return result.scalar_one_or_none()


async def list_users(db: AsyncSession) -> List[AuthUserORM]:
    result = await db.execute(
        select(AuthUserORM).order_by(AuthUserORM.created_at.desc(), AuthUserORM.id.desc())
    )
    return result.scalars().all()


async def create_user(
    db: AsyncSession,
    username: str,
    password: str,
    role: str = ROLE_VIEWER,
    is_active: bool = True,
    created_by: Optional[str] = None,
) -> AuthUserORM:
    ok, reason = validate_username(username)
    if not ok:
        raise ValueError(reason)
    ok, reason = validate_password(password)
    if not ok:
        raise ValueError(reason)

    normalized = normalize_username(username)
    normalized_role = _normalize_role(role)
    exists = await get_user_by_username(db, normalized)
    if exists:
        raise ValueError("用户名已存在。")

    user = AuthUserORM(
        username=normalized,
        password_hash=hash_password(password),
        role=normalized_role,
        is_active=bool(is_active),
        created_by=created_by,
    )
    db.add(user)
    await db.flush()
    return user


async def update_user(
    db: AsyncSession,
    user: AuthUserORM,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    password: Optional[str] = None,
) -> AuthUserORM:
    if role is not None:
        user.role = _normalize_role(role)
    if is_active is not None:
        user.is_active = bool(is_active)
    if password is not None:
        ok, reason = validate_password(password)
        if not ok:
            raise ValueError(reason)
        user.password_hash = hash_password(password)
    await db.flush()
    return user


async def authenticate_user(
    db: AsyncSession,
    username: str,
    password: str,
) -> Optional[AuthUserORM]:
    user = await get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def create_session(
    db: AsyncSession,
    user: AuthUserORM,
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> Tuple[str, datetime]:
    token = generate_session_token()
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=SESSION_TTL_HOURS)

    session = AuthSessionORM(
        token_hash=hash_session_token(token),
        user_id=user.id,
        expires_at=expires_at,
        ip_address=(ip_address or "")[:64] or None,
        user_agent=(user_agent or "")[:512] or None,
    )
    user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(session)
    await db.flush()
    return token, expires_at


async def get_user_by_session_token(db: AsyncSession, token: Optional[str]) -> Optional[AuthUserORM]:
    if not token:
        return None
    token_hash = hash_session_token(token)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(
        select(AuthUserORM)
        .join(AuthSessionORM, AuthSessionORM.user_id == AuthUserORM.id)
        .where(
            AuthSessionORM.token_hash == token_hash,
            AuthSessionORM.is_revoked == False,
            AuthSessionORM.expires_at > now,
            AuthUserORM.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def revoke_session(db: AsyncSession, token: Optional[str]) -> bool:
    if not token:
        return False
    token_hash = hash_session_token(token)
    result = await db.execute(
        select(AuthSessionORM).where(
            AuthSessionORM.token_hash == token_hash,
            AuthSessionORM.is_revoked == False,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        return False
    session.is_revoked = True
    await db.flush()
    return True


async def add_audit_log(
    db: AsyncSession,
    action: str,
    user: Optional[AuthUserORM] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    resource: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> None:
    resolved_user_id = user_id
    resolved_username = username

    if user is not None:
        if resolved_user_id is None:
            try:
                resolved_user_id = user.id
            except Exception:
                resolved_user_id = None
        if resolved_username is None:
            try:
                resolved_username = user.username
            except Exception:
                resolved_username = None

    entry = AuthAuditLogORM(
        user_id=resolved_user_id,
        username=resolved_username,
        action=action,
        resource=resource,
        detail=detail or None,
        ip_address=(ip_address or "")[:64] or None,
    )
    db.add(entry)
    await db.flush()


def to_user_payload(user: AuthUserORM) -> Dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }
