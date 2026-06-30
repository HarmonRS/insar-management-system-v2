"""
Shared dependencies, guards, and helper utilities for all routers.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, File, HTTPException, Request, UploadFile
from geoalchemy2.functions import ST_Intersects
from shapely.geometry import mapping, shape
from shapely.ops import unary_union
from sqlalchemy import delete, inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..auth_service import (
    ROLE_ADMIN,
    SESSION_COOKIE_NAME,
    add_audit_log,
    get_user_by_session_token,
)
from ..config import read_int_env, settings

# Trusted proxy IPs whose X-Forwarded-For header is accepted.
# Defaults to localhost (nginx on the same host). Override via TRUSTED_PROXY_IPS env var
# (comma-separated, e.g. "127.0.0.1,10.0.0.1").
_TRUSTED_PROXY_IPS: frozenset[str] = frozenset(
    ip.strip()
    for ip in (settings.TRUSTED_PROXY_IPS or "127.0.0.1").split(",")
    if ip.strip()
)
from ..database import get_db
from ..license_service import check_license
from ..models import AuthRateLimitORM, AuthUserORM, DinsarTaskBatchORM, DinsarTaskItemORM, PsTaskBatchORM, PsTaskItemORM

# ---------------------------------------------------------------------------
# Path classification constants
# ---------------------------------------------------------------------------

_LICENSE_EXEMPT_PATHS = {
    "/api/license/status",
    "/api/license/upload",
    "/api/license/refresh",
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
}

READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}
READ_SAFE_POST_PATHS = {
    "/api/radar-data/search",
}
PUBLIC_AUTH_PATHS = {
    "/api/license/status",
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
}

HIGH_RISK_WRITE_PATH_PREFIXES = (
    "/api/auth/users",
    "/api/license/upload",
    "/api/license/refresh",
    "/api/workflow/runs",
    "/api/task-batches/",
    "/api/tools/",
    "/api/unpack/run",
    "/api/monitor/",
    "/api/scan-data",
    "/api/scan-dinsar-results",
    "/api/dinsar-results/",
    "/api/dinsar-production",
    "/api/sbas-insar-production",
    "/api/ai/",
    "/api/idl/launch-workbench",
    "/api/idl/worker/",
    "/api/hazard-points/scan",
    "/api/radar-data/",
)

# ---------------------------------------------------------------------------
# Login throttle constants & locks
# ---------------------------------------------------------------------------

AUTH_LOGIN_MAX_FAILURES = read_int_env("AUTH_LOGIN_MAX_FAILURES", 5, minimum=1, maximum=100)
AUTH_LOGIN_WINDOW_SECONDS = read_int_env("AUTH_LOGIN_WINDOW_SECONDS", 900, minimum=5, maximum=86400)
AUTH_LOGIN_LOCK_SECONDS = read_int_env("AUTH_LOGIN_LOCK_SECONDS", 900, minimum=5, maximum=86400)
LOGIN_THROTTLE_CLEANUP_INTERVAL_SECONDS = read_int_env(
    "AUTH_LOGIN_CLEANUP_INTERVAL_SECONDS",
    300,
    minimum=30,
    maximum=3600,
)

_LOGIN_THROTTLE_LOCK = asyncio.Lock()
_LOGIN_THROTTLE_LAST_CLEANUP_MONO = 0.0
_LICENSE_UPLOAD_LOCK = asyncio.Lock()
MAX_LICENSE_UPLOAD_BYTES = read_int_env(
    "MAX_LICENSE_UPLOAD_BYTES",
    1024 * 1024,
    minimum=1024,
    maximum=20 * 1024 * 1024,
)

# ---------------------------------------------------------------------------
# Statistics cache
# ---------------------------------------------------------------------------

STATS_CACHE_TTL_SECONDS = read_int_env(
    "STATS_CACHE_TTL_SECONDS",
    120,
    minimum=0,
    maximum=3600,
)
_STATS_CACHE_LOCK = asyncio.Lock()
_STATS_CACHE_DATA: Optional[Dict[str, Any]] = None
_STATS_CACHE_EXPIRES_AT = 0.0
_STATS_CACHE_GENERATED_AT_UTC: Optional[str] = None

DASHBOARD_STATS_CACHE_TTL_SECONDS = read_int_env(
    "DASHBOARD_STATS_CACHE_TTL_SECONDS",
    STATS_CACHE_TTL_SECONDS,
    minimum=0,
    maximum=3600,
)
_DASHBOARD_STATS_CACHE_LOCK = asyncio.Lock()
_DASHBOARD_STATS_CACHE_DATA: Optional[Dict[str, Any]] = None
_DASHBOARD_STATS_CACHE_EXPIRES_AT = 0.0
_DASHBOARD_STATS_CACHE_GENERATED_AT_UTC: Optional[str] = None

# ---------------------------------------------------------------------------
# AOI token store
# ---------------------------------------------------------------------------

_AOI_TOKEN_STORE: Dict[str, Dict[str, Any]] = {}
_AOI_TOKEN_LOCK = asyncio.Lock()
AOI_TOKEN_TTL_SECONDS = read_int_env(
    "AOI_TOKEN_TTL_SECONDS",
    1800,
    minimum=60,
    maximum=24 * 3600,
)
AOI_UPLOAD_MAX_FILES = read_int_env(
    "AOI_UPLOAD_MAX_FILES",
    10,
    minimum=1,
    maximum=200,
)
AOI_UPLOAD_MAX_SINGLE_FILE_BYTES = read_int_env(
    "AOI_UPLOAD_MAX_SINGLE_FILE_BYTES",
    20 * 1024 * 1024,
    minimum=1024,
    maximum=500 * 1024 * 1024,
)
AOI_UPLOAD_MAX_TOTAL_BYTES = max(
    read_int_env(
        "AOI_UPLOAD_MAX_TOTAL_BYTES",
        100 * 1024 * 1024,
        minimum=1024,
        maximum=2 * 1024 * 1024 * 1024,
    ),
    AOI_UPLOAD_MAX_SINGLE_FILE_BYTES,
)
AOI_UPLOAD_STREAM_CHUNK_BYTES = 1024 * 1024
_SHAPEFILE_READ_LOCK = asyncio.Lock()

# ---------------------------------------------------------------------------
# Region index caches
# ---------------------------------------------------------------------------

_REGION_CHILDREN_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None
_REGION_BY_ID_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
_REGION_GEOMETRY_BY_ID_CACHE: Optional[Dict[str, List[Dict[str, Any]]]] = None

# ---------------------------------------------------------------------------
# Export/root-dir path validation
# ---------------------------------------------------------------------------

import logging as _logging

_dep_logger = _logging.getLogger(__name__)

# Optional whitelist of allowed export directories.
# Set ALLOWED_EXPORT_DIRS in .env as comma-separated paths to restrict exports.
_ALLOWED_EXPORT_DIRS_RAW = (settings.ALLOWED_EXPORT_DIRS or "").strip()
ALLOWED_EXPORT_DIRS: Optional[List[str]] = None
if _ALLOWED_EXPORT_DIRS_RAW:
    ALLOWED_EXPORT_DIRS = [
        os.path.normpath(p.strip()) for p in _ALLOWED_EXPORT_DIRS_RAW.split(",") if p.strip()
    ]

_SYSTEM_DIRS_WIN = {
    os.path.normpath(p)
    for p in ["C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)", "C:\\ProgramData"]
}
_SYSTEM_DIRS_UNIX = {"/bin", "/sbin", "/usr", "/etc", "/lib", "/lib64", "/boot", "/proc", "/sys"}


def _is_system_directory(path: str) -> bool:
    """Check if path points to a system directory."""
    normed = os.path.normpath(path)
    for sd in _SYSTEM_DIRS_WIN | _SYSTEM_DIRS_UNIX:
        if normed == sd or normed.startswith(sd + os.sep):
            return True
    return False


def _validate_export_path(path: str, param_name: str = "target_dir") -> str:
    """
    Validate an export/destination path:
    - Rejects path traversal (..)
    - Rejects system directories
    - Optionally checks against ALLOWED_EXPORT_DIRS whitelist
    Returns the normalized path.
    """
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail=f"{param_name} 不能为空")
    normed = os.path.normpath(path.strip())
    if ".." in normed.split(os.sep):
        raise HTTPException(status_code=400, detail=f"{param_name} 包含非法路径遍历")
    if _is_system_directory(normed):
        raise HTTPException(status_code=400, detail=f"{param_name} 指向系统目录，操作被拒绝")
    if ALLOWED_EXPORT_DIRS is not None:
        if not any(normed.startswith(allowed) for allowed in ALLOWED_EXPORT_DIRS):
            raise HTTPException(status_code=400, detail=f"{param_name} 不在允许的导出目录内")
    return normed


def _validate_root_dir(path: str, param_name: str = "root_dir") -> str:
    """
    Validate a root directory parameter:
    - Rejects path traversal (..)
    - Rejects system directories
    - Verifies directory exists
    Returns the normalized path.
    """
    if not path or not path.strip():
        raise HTTPException(status_code=400, detail=f"{param_name} 不能为空")
    normed = os.path.normpath(path.strip())
    if ".." in normed.split(os.sep):
        raise HTTPException(status_code=400, detail=f"{param_name} 包含非法路径遍历")
    if _is_system_directory(normed):
        raise HTTPException(status_code=400, detail=f"{param_name} 指向系统目录，操作被拒绝")
    if not os.path.isdir(normed):
        raise HTTPException(status_code=400, detail=f"{param_name} 目录不存在")
    return normed


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _normalize_request_path(path: str) -> str:
    normalized = (path or "").rstrip("/")
    return normalized or "/"


def _format_size_limit(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _is_public_auth_path(path: str) -> bool:
    return _normalize_request_path(path) in PUBLIC_AUTH_PATHS


def _is_read_safe_post_path(path: str, method: str) -> bool:
    return (method or "").upper() == "POST" and _normalize_request_path(path) in READ_SAFE_POST_PATHS


def _is_read_only_operation(path: str, method: str) -> bool:
    upper_method = (method or "").upper()
    if upper_method in READ_ONLY_METHODS:
        return True
    return _is_read_safe_post_path(path, upper_method)


def _is_high_risk_write_path(path: str, method: str) -> bool:
    normalized = _normalize_request_path(path)
    upper_method = (method or "").upper()
    if _is_read_only_operation(normalized, upper_method):
        return False
    return any(normalized.startswith(prefix) for prefix in HIGH_RISK_WRITE_PATH_PREFIXES)


def _is_user_self_service_write_path(path: str, method: str) -> bool:
    normalized = _normalize_request_path(path)
    upper_method = (method or "").upper()
    return upper_method == "POST" and normalized == "/api/result-deliveries"


def _get_client_ip(request: Request) -> Optional[str]:
    direct_ip = request.client.host if request.client else None
    if direct_ip in _TRUSTED_PROXY_IPS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return direct_ip


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------


async def _add_operation_audit_log(
    db: AsyncSession,
    request: Request,
    action: str,
    resource: str,
    detail: Optional[Dict[str, Any]] = None,
    user: Optional[AuthUserORM] = None,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> None:
    actor = user or getattr(request.state, "current_user", None)
    resolved_user_id = user_id
    resolved_username = username

    if actor is not None:
        if resolved_user_id is None:
            try:
                identity = sa_inspect(actor).identity
                if identity and len(identity) > 0:
                    resolved_user_id = int(identity[0])
            except Exception:
                try:
                    resolved_user_id = actor.id
                except Exception:
                    resolved_user_id = None
        if resolved_username is None:
            try:
                actor_state_dict = sa_inspect(actor).dict
                resolved_username = actor_state_dict.get("username")
            except Exception:
                try:
                    resolved_username = actor.username
                except Exception:
                    resolved_username = None

    await add_audit_log(
        db,
        action=action,
        user=None,
        user_id=resolved_user_id,
        username=resolved_username,
        resource=resource,
        detail=detail or None,
        ip_address=_get_client_ip(request),
    )


# ---------------------------------------------------------------------------
# License guard
# ---------------------------------------------------------------------------


def _require_license(request: Request):
    """
    授权校验：未授权时拒绝所有 API。
    使用精确路径集合匹配，防止 endswith 绕过攻击。
    """
    path = (request.url.path or "").rstrip("/") or "/"
    if path in _LICENSE_EXEMPT_PATHS:
        return
    result = check_license()
    if not result.get("ok"):
        raise HTTPException(status_code=403, detail=f"License required: {result.get('reason')}")


# ---------------------------------------------------------------------------
# Auth guards
# ---------------------------------------------------------------------------


async def _require_auth(request: Request, db: AsyncSession = Depends(get_db)):
    path = _normalize_request_path(request.url.path)
    if _is_public_auth_path(path):
        return

    method = request.method.upper()
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = await get_user_by_session_token(db, token)
    if not user:
        if _is_high_risk_write_path(path, method):
            await add_audit_log(
                db,
                action="write_auth_required",
                resource=path,
                detail={"method": method},
                ip_address=_get_client_ip(request),
            )
            await db.commit()
        raise HTTPException(status_code=401, detail="Authentication required.")

    if (
        (not _is_read_only_operation(path, method))
        and user.role != ROLE_ADMIN
        and not _is_user_self_service_write_path(path, method)
    ):
        if _is_high_risk_write_path(path, method):
            await add_audit_log(
                db,
                action="write_blocked_readonly",
                user=user,
                resource=path,
                detail={"method": method, "role": user.role},
                ip_address=_get_client_ip(request),
            )
            await db.commit()
        raise HTTPException(status_code=403, detail="Read-only account cannot perform this operation.")

    if _is_high_risk_write_path(path, method):
        await add_audit_log(
            db,
            action="write_access_granted",
            user=user,
            resource=path,
            detail={"method": method},
            ip_address=_get_client_ip(request),
        )
        await db.commit()
        refreshed_user = await get_user_by_session_token(db, token)
        if not refreshed_user:
            raise HTTPException(status_code=401, detail="Authentication required.")
        user = refreshed_user

    request.state.current_user = user


async def _get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> AuthUserORM:
    user = getattr(request.state, "current_user", None)
    if user:
        return user

    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = await get_user_by_session_token(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    request.state.current_user = user
    return user


async def _get_optional_session_user(
    request: Request,
    db: AsyncSession,
) -> Optional[AuthUserORM]:
    cached_user = getattr(request.state, "current_user", None)
    if cached_user is not None:
        return cached_user
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = await get_user_by_session_token(db, token)
    if user is not None:
        request.state.current_user = user
    return user


async def _require_admin(current_user: AuthUserORM = Depends(_get_current_user)) -> AuthUserORM:
    if current_user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Admin role required.")
    return current_user


# ---------------------------------------------------------------------------
# Login throttle helpers
# ---------------------------------------------------------------------------


def _build_login_throttle_key(username: str, client_ip: Optional[str]) -> str:
    # Throttle by username only to prevent IP-switching bypass
    normalized_username = (username or "").strip().lower() or "<empty>"
    return f"user:{normalized_username}"


def _remaining_seconds(deadline: datetime, now: datetime) -> int:
    remaining = max(0.0, (deadline - now).total_seconds())
    rounded = int(remaining)
    if rounded < remaining:
        rounded += 1
    return max(1, rounded)


def _login_throttle_retention_seconds() -> int:
    return max(AUTH_LOGIN_WINDOW_SECONDS, AUTH_LOGIN_LOCK_SECONDS) * 2


async def _maybe_cleanup_login_throttle_records(db: AsyncSession) -> None:
    global _LOGIN_THROTTLE_LAST_CLEANUP_MONO

    if LOGIN_THROTTLE_CLEANUP_INTERVAL_SECONDS <= 0:
        return

    now_mono = time.monotonic()
    if (
        _LOGIN_THROTTLE_LAST_CLEANUP_MONO > 0
        and now_mono - _LOGIN_THROTTLE_LAST_CLEANUP_MONO < LOGIN_THROTTLE_CLEANUP_INTERVAL_SECONDS
    ):
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=_login_throttle_retention_seconds())
    await db.execute(
        delete(AuthRateLimitORM).where(
            AuthRateLimitORM.updated_at < cutoff,
            (AuthRateLimitORM.locked_until.is_(None))
            | (AuthRateLimitORM.locked_until <= now),
        )
    )
    _LOGIN_THROTTLE_LAST_CLEANUP_MONO = now_mono


async def _get_login_retry_after_seconds(throttle_key: str, db: AsyncSession) -> int:
    async with _LOGIN_THROTTLE_LOCK:
        await _maybe_cleanup_login_throttle_records(db)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(AuthRateLimitORM).where(AuthRateLimitORM.throttle_key == throttle_key)
        )
        record = result.scalar_one_or_none()
        if not record or not record.locked_until:
            return 0
        if record.locked_until <= now:
            return 0
        return _remaining_seconds(record.locked_until, now)


async def _record_login_failure(throttle_key: str, db: AsyncSession) -> int:
    async with _LOGIN_THROTTLE_LOCK:
        await _maybe_cleanup_login_throttle_records(db)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        failure_cutoff = now - timedelta(seconds=AUTH_LOGIN_WINDOW_SECONDS)

        result = await db.execute(
            select(AuthRateLimitORM).where(AuthRateLimitORM.throttle_key == throttle_key)
        )
        record = result.scalar_one_or_none()

        if record is None:
            record = AuthRateLimitORM(throttle_key=throttle_key, failure_timestamps=[])
            db.add(record)

        timestamps = [ts for ts in (record.failure_timestamps or []) if ts > failure_cutoff.timestamp()]
        timestamps.append(now.timestamp())

        if len(timestamps) >= AUTH_LOGIN_MAX_FAILURES:
            locked_until = now + timedelta(seconds=AUTH_LOGIN_LOCK_SECONDS)
            record.locked_until = locked_until
            record.failure_timestamps = []
            await db.flush()
            return _remaining_seconds(locked_until, now)

        record.failure_timestamps = timestamps
        record.locked_until = None
        await db.flush()
        return 0


async def _clear_login_failure_state(throttle_key: str, db: AsyncSession) -> None:
    async with _LOGIN_THROTTLE_LOCK:
        result = await db.execute(
            select(AuthRateLimitORM).where(AuthRateLimitORM.throttle_key == throttle_key)
        )
        record = result.scalar_one_or_none()
        if record is not None:
            record.failure_timestamps = []
            record.locked_until = None
            await db.flush()


# ---------------------------------------------------------------------------
# Batch summary helpers
# ---------------------------------------------------------------------------


async def _refresh_dinsar_batch_summary(db: AsyncSession, batch_id: str) -> None:
    from sqlalchemy import func
    total_res = await db.execute(
        select(func.count(DinsarTaskItemORM.id)).where(DinsarTaskItemORM.batch_id == batch_id)
    )
    completed_res = await db.execute(
        select(func.count(DinsarTaskItemORM.id)).where(
            DinsarTaskItemORM.batch_id == batch_id,
            func.upper(func.coalesce(DinsarTaskItemORM.status, "")) == "COMPLETED",
        )
    )
    total = total_res.scalar_one() or 0
    completed = completed_res.scalar_one() or 0
    status = "PENDING"
    if total > 0 and completed == total:
        status = "COMPLETED"
    elif completed > 0:
        status = "IN_PROGRESS"

    await db.execute(
        DinsarTaskBatchORM.__table__.update()
        .where(DinsarTaskBatchORM.batch_id == batch_id)
        .values(total_items=total, completed_items=completed, status=status)
    )


async def _refresh_ps_batch_summary(db: AsyncSession, batch_id: str) -> None:
    from sqlalchemy import func
    total_res = await db.execute(
        select(func.count(PsTaskItemORM.id)).where(PsTaskItemORM.batch_id == batch_id)
    )
    completed_res = await db.execute(
        select(func.count(PsTaskItemORM.id)).where(
            PsTaskItemORM.batch_id == batch_id,
            func.upper(func.coalesce(PsTaskItemORM.status, "")) == "COMPLETED",
        )
    )
    total = total_res.scalar_one() or 0
    completed = completed_res.scalar_one() or 0
    status = "PENDING"
    if total > 0 and completed == total:
        status = "COMPLETED"
    elif completed > 0:
        status = "IN_PROGRESS"

    await db.execute(
        PsTaskBatchORM.__table__.update()
        .where(PsTaskBatchORM.batch_id == batch_id)
        .values(total_items=total, completed_items=completed, status=status)
    )


# ---------------------------------------------------------------------------
# AOI helpers
# ---------------------------------------------------------------------------


def _infer_region_level(tree_id: str) -> str:
    depth = len((tree_id or "").split("-"))
    if depth == 1:
        return "country"
    if depth == 2:
        return "province"
    if depth == 3:
        return "city"
    if depth == 4:
        return "district"
    return "unknown"


def _normalize_region_node(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    tree_id = str(raw.get("treeID") or "").strip()
    if not tree_id:
        return None
    parent_raw = raw.get("parent")
    parent_tree_id = str(parent_raw).strip() if parent_raw is not None else None
    if parent_tree_id == "":
        parent_tree_id = None
    name = str(raw.get("name") or tree_id).strip()
    return {
        "tree_id": tree_id,
        "parent_tree_id": parent_tree_id,
        "name": name,
        "level": _infer_region_level(tree_id),
    }


def _load_region_index() -> None:
    global _REGION_CHILDREN_CACHE, _REGION_BY_ID_CACHE
    if _REGION_CHILDREN_CACHE is not None and _REGION_BY_ID_CACHE is not None:
        return

    index_path = Path(settings.AOI_REGION_INDEX_FILE or "")
    if not index_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"行政区索引文件不存在，请检查 AOI_REGION_INDEX_FILE: {index_path}",
        )

    try:
        raw_data = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"加载行政区索引失败: {exc}") from exc

    if not isinstance(raw_data, list):
        raise HTTPException(status_code=500, detail="行政区索引文件格式错误：应为 JSON 数组。")

    children_cache: Dict[str, List[Dict[str, Any]]] = {}
    node_cache: Dict[str, Dict[str, Any]] = {}
    for item in raw_data:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_region_node(item)
        if not normalized:
            continue
        tree_id = normalized["tree_id"]
        parent_tree_id = normalized["parent_tree_id"]
        node_cache[tree_id] = normalized
        if parent_tree_id:
            children_cache.setdefault(parent_tree_id, []).append(normalized)

    for parent_tree_id in children_cache:
        children_cache[parent_tree_id].sort(key=lambda row: row["tree_id"])

    _REGION_CHILDREN_CACHE = children_cache
    _REGION_BY_ID_CACHE = node_cache


def _load_region_geometry_index() -> None:
    global _REGION_GEOMETRY_BY_ID_CACHE
    if _REGION_GEOMETRY_BY_ID_CACHE is not None:
        return

    geometry_path = Path(settings.AOI_REGION_GEOJSON_FILE or "")
    if not geometry_path.is_file():
        raise HTTPException(
            status_code=503,
            detail=(
                "行政区边界文件不存在，请准备标准 GeoJSON（FeatureCollection），"
                f"并配置 AOI_REGION_GEOJSON_FILE: {geometry_path}"
            ),
        )

    try:
        raw_data = json.loads(geometry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"加载行政区边界数据失败: {exc}") from exc

    if isinstance(raw_data, dict) and raw_data.get("type") == "FeatureCollection":
        features = raw_data.get("features", [])
    elif isinstance(raw_data, list):
        features = raw_data
    else:
        raise HTTPException(status_code=500, detail="行政区边界文件格式错误：应为 FeatureCollection 或 Feature 数组。")

    feature_index: Dict[str, List[Dict[str, Any]]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        if feature.get("type") != "Feature":
            continue
        properties = feature.get("properties") or {}
        tree_id = str(
            properties.get("treeID")
            or properties.get("tree_id")
            or properties.get("treeId")
            or ""
        ).strip()
        if not tree_id:
            continue
        feature_index.setdefault(tree_id, []).append(feature)

    if not feature_index:
        raise HTTPException(
            status_code=500,
            detail="行政区边界文件中未找到可用 treeID 字段（支持 treeID/tree_id/treeId）。",
        )

    _REGION_GEOMETRY_BY_ID_CACHE = feature_index


def _normalize_geojson_to_feature_collection(payload: Any) -> Dict[str, Any]:
    if not payload:
        raise HTTPException(status_code=400, detail="AOI GeoJSON 不能为空。")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="AOI GeoJSON 必须是对象。")

    payload_type = payload.get("type")
    if payload_type == "FeatureCollection":
        features = payload.get("features") or []
        if not isinstance(features, list):
            raise HTTPException(status_code=400, detail="FeatureCollection.features 必须是数组。")
        return {"type": "FeatureCollection", "features": features}

    if payload_type == "Feature":
        return {"type": "FeatureCollection", "features": [payload]}

    if payload_type in {"Polygon", "MultiPolygon", "LineString", "MultiLineString", "Point", "MultiPoint"}:
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": payload}]}

    raise HTTPException(status_code=400, detail=f"不支持的 AOI GeoJSON 类型: {payload_type}")


def _feature_collection_to_union_geometry(feature_collection: Dict[str, Any]):
    features = feature_collection.get("features") or []
    geometries = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            geom = shape(geometry)
        except Exception:
            continue
        if geom.is_empty:
            continue
        geometries.append(geom)

    if not geometries:
        raise HTTPException(status_code=400, detail="AOI GeoJSON 中未解析到有效几何。")

    merged = unary_union(geometries)
    if merged.is_empty:
        raise HTTPException(status_code=400, detail="AOI 几何为空，无法用于筛选。")
    return merged


def _parse_aoi_geojson_form_value(aoi_geojson: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not aoi_geojson:
        return None
    try:
        payload = json.loads(aoi_geojson)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"AOI GeoJSON 解析失败: {exc}") from exc

    feature_collection = _normalize_geojson_to_feature_collection(payload)
    merged_geometry = _feature_collection_to_union_geometry(feature_collection)
    return merged_geometry.wkt, feature_collection


def _read_aoi_shapefile_with_restore_shx(shp_path: str):
    import geopandas as gpd

    previous_restore_shx = os.environ.get("SHAPE_RESTORE_SHX")
    os.environ["SHAPE_RESTORE_SHX"] = "YES"
    try:
        return gpd.read_file(shp_path, engine="pyogrio")
    finally:
        if previous_restore_shx is None:
            os.environ.pop("SHAPE_RESTORE_SHX", None)
        else:
            os.environ["SHAPE_RESTORE_SHX"] = previous_restore_shx


async def _parse_aoi_from_files(files: Optional[List[UploadFile]]) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not files:
        return None

    valid_files = [file for file in files if file and file.filename and str(file.filename).strip()]
    if not valid_files:
        return None
    if len(valid_files) > AOI_UPLOAD_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"AOI 上传文件数量超限，最多允许 {AOI_UPLOAD_MAX_FILES} 个文件。",
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        shp_path: Optional[str] = None
        geojson_payload: Optional[Dict[str, Any]] = None
        total_bytes = 0

        for index, file in enumerate(valid_files):
            raw_name = str(file.filename).strip()
            base_name = os.path.basename(raw_name) or f"upload_{index}"
            dest_name = f"{index:02d}_{base_name}"
            dest_path = os.path.join(temp_dir, dest_name)
            file_bytes = 0

            try:
                with open(dest_path, "wb") as buffer:
                    while True:
                        chunk = await file.read(AOI_UPLOAD_STREAM_CHUNK_BYTES)
                        if not chunk:
                            break
                        chunk_size = len(chunk)
                        file_bytes += chunk_size
                        total_bytes += chunk_size
                        if file_bytes > AOI_UPLOAD_MAX_SINGLE_FILE_BYTES:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"AOI 文件 {base_name} 超过单文件大小限制（"
                                    f"{_format_size_limit(AOI_UPLOAD_MAX_SINGLE_FILE_BYTES)}）。"
                                ),
                            )
                        if total_bytes > AOI_UPLOAD_MAX_TOTAL_BYTES:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    "AOI 上传文件总大小超限（"
                                    f"{_format_size_limit(AOI_UPLOAD_MAX_TOTAL_BYTES)}）。"
                                ),
                            )
                        buffer.write(chunk)
            finally:
                await file.close()

            lower_dest = dest_path.lower()
            if lower_dest.endswith(".shp"):
                shp_path = dest_path
            elif lower_dest.endswith(".geojson") or lower_dest.endswith(".json"):
                try:
                    geojson_payload = json.loads(Path(dest_path).read_text(encoding="utf-8"))
                except UnicodeDecodeError:
                    geojson_payload = json.loads(Path(dest_path).read_text(encoding="gbk"))

        if shp_path:
            try:
                async with _SHAPEFILE_READ_LOCK:
                    gdf = await asyncio.to_thread(_read_aoi_shapefile_with_restore_shx, shp_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "AOI Shapefile 读取失败。系统已尝试自动恢复缺失的 .shx 索引；"
                        f"请确认已上传 .shp/.dbf/.prj/.shx 或可恢复的标准 Shapefile。原始错误: {exc}"
                    ),
                ) from exc
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)
            feature_collection = json.loads(gdf.to_json())
            merged_geometry = _feature_collection_to_union_geometry(feature_collection)
            return merged_geometry.wkt, feature_collection

        if geojson_payload:
            feature_collection = _normalize_geojson_to_feature_collection(geojson_payload)
            merged_geometry = _feature_collection_to_union_geometry(feature_collection)
            return merged_geometry.wkt, feature_collection

    return None


def _cleanup_expired_aoi_tokens(now: float) -> None:
    expired_tokens = [
        token
        for token, payload in _AOI_TOKEN_STORE.items()
        if float(payload.get("expires_at", 0.0)) <= now
    ]
    for token in expired_tokens:
        _AOI_TOKEN_STORE.pop(token, None)


async def _store_aoi_token(aoi_wkt: str, feature_collection: Dict[str, Any]) -> str:
    token = uuid.uuid4().hex
    now = time.monotonic()
    async with _AOI_TOKEN_LOCK:
        _cleanup_expired_aoi_tokens(now)
        _AOI_TOKEN_STORE[token] = {
            "aoi_wkt": aoi_wkt,
            "aoi_geojson": feature_collection,
            "expires_at": now + AOI_TOKEN_TTL_SECONDS,
        }
    return token


async def _get_aoi_from_token(aoi_token: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not aoi_token:
        return None
    normalized_token = str(aoi_token).strip()
    if not normalized_token:
        return None
    now = time.monotonic()
    async with _AOI_TOKEN_LOCK:
        _cleanup_expired_aoi_tokens(now)
        payload = _AOI_TOKEN_STORE.get(normalized_token)
        if not payload:
            return None
        payload["expires_at"] = now + AOI_TOKEN_TTL_SECONDS
        return payload.get("aoi_wkt"), payload.get("aoi_geojson")


def _resolve_region_aoi_payload(tree_id: str) -> Dict[str, Any]:
    normalized_tree_id = (tree_id or "").strip()
    if not normalized_tree_id:
        raise HTTPException(status_code=400, detail="tree_id 不能为空。")

    _load_region_index()
    _load_region_geometry_index()

    node = (_REGION_BY_ID_CACHE or {}).get(normalized_tree_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"未找到行政区: {normalized_tree_id}")

    geometry_index = _REGION_GEOMETRY_BY_ID_CACHE or {}

    def _collect_features_by_tree_id(target_tree_id: str) -> Tuple[List[Dict[str, Any]], str]:
        exact_features = list(geometry_index.get(target_tree_id) or [])
        if exact_features:
            return exact_features, "exact"

        descendant_features: List[Dict[str, Any]] = []
        prefix = f"{target_tree_id}-"
        for feature_tree_id, feature_list in geometry_index.items():
            if feature_tree_id.startswith(prefix):
                descendant_features.extend(feature_list)
        if descendant_features:
            return descendant_features, "descendants"
        return [], "none"

    matched_tree_id = normalized_tree_id
    features, source = _collect_features_by_tree_id(normalized_tree_id)

    if not features:
        current_tree_id = node.get("parent_tree_id")
        while current_tree_id:
            ancestor_features, ancestor_source = _collect_features_by_tree_id(current_tree_id)
            if ancestor_features:
                features = ancestor_features
                matched_tree_id = current_tree_id
                source = f"ancestor_{ancestor_source}"
                break
            current_node = (_REGION_BY_ID_CACHE or {}).get(current_tree_id)
            current_tree_id = current_node.get("parent_tree_id") if current_node else None

    if not features:
        raise HTTPException(
            status_code=404,
            detail=(
                f"行政区 {normalized_tree_id} 未匹配到边界数据。"
                "请检查 AOI_REGION_GEOJSON_FILE 中 features[*].properties.treeID。"
            ),
        )

    raw_feature_collection = {
        "type": "FeatureCollection",
        "features": features,
    }
    merged_geometry = _feature_collection_to_union_geometry(raw_feature_collection)
    merged_feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "treeID": normalized_tree_id,
                    "matched_treeID": matched_tree_id,
                    "name": node.get("name"),
                    "level": node.get("level"),
                    "source": source,
                },
                "geometry": mapping(merged_geometry),
            }
        ],
    }

    return {
        "tree_id": normalized_tree_id,
        "name": node.get("name"),
        "level": node.get("level"),
        "source": source,
        "matched_tree_id": matched_tree_id,
        "feature_count": len(features),
        "aoi_geojson": merged_feature_collection,
    }

