from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import read_int_env, settings
from ..database import get_db
from ..license_service import check_license
from ..models import AuthUserORM
from .dependencies import (
    _add_operation_audit_log,
    _get_optional_session_user,
    _require_admin,
    _LICENSE_UPLOAD_LOCK,
    MAX_LICENSE_UPLOAD_BYTES,
)
from ..auth_service import ROLE_ADMIN

router = APIRouter()


def _serialize_license_status(raw_status: Dict[str, Any], include_details: bool) -> Dict[str, Any]:
    public_payload = {
        "ok": bool(raw_status.get("ok")),
        "reason": raw_status.get("reason"),
        "expires_at": raw_status.get("expires_at"),
        "issued_to": raw_status.get("issued_to"),
    }
    if include_details:
        return {
            **public_payload,
            "fingerprint": raw_status.get("fingerprint"),
            "license_path": raw_status.get("license_path"),
        }
    return public_payload


@router.get("/license/status")
async def license_status(request: Request, db: AsyncSession = Depends(get_db)):
    """
    授权状态查询（无需授权）。
    默认返回脱敏字段；管理员会话可看到额外调试字段。
    """
    status = check_license()
    session_user = await _get_optional_session_user(request, db)
    include_details = bool(session_user and session_user.role == ROLE_ADMIN)
    return _serialize_license_status(status, include_details=include_details)


@router.post("/license/upload")
async def license_upload(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    上传授权文件（覆盖后立即生效）。
    """
    filename = (file.filename or "").strip()
    if not filename.lower().endswith('.lic'):
        raise HTTPException(status_code=400, detail='License file must end with .lic')

    license_path = settings.LICENSE_PATH
    if not license_path:
        license_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "license", "license.lic")
        license_path = os.path.abspath(license_path)

    license_dir = os.path.dirname(license_path)
    os.makedirs(license_dir, exist_ok=True)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="License file is empty")
    if len(content) > MAX_LICENSE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"License file too large (max {MAX_LICENSE_UPLOAD_BYTES} bytes)",
        )

    upload_status: Optional[Dict[str, Any]] = None
    upload_error: Optional[str] = None
    tmp_path: Optional[str] = None
    async with _LICENSE_UPLOAD_LOCK:
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=license_dir,
                prefix="license_upload_",
                suffix=".lic.tmp",
            ) as temp_file:
                temp_file.write(content)
                temp_file.flush()
                tmp_path = temp_file.name

            upload_status = check_license(license_path=tmp_path)
            if not upload_status.get("ok"):
                upload_error = upload_status.get("reason") or "license validation failed"
            else:
                os.replace(tmp_path, license_path)
                tmp_path = None
                upload_status = check_license()
                if not upload_status.get("ok"):
                    upload_error = upload_status.get("reason") or "license validation failed after activation"
        except Exception as exc:
            upload_error = str(exc)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    if upload_error:
        await _add_operation_audit_log(
            db,
            request=request,
            action="license_upload_failed",
            user=admin_user,
            resource="license/upload",
            detail={"filename": filename, "reason": upload_error},
        )
        await db.commit()
        raise HTTPException(status_code=400, detail=f"License invalid: {upload_error}")

    await _add_operation_audit_log(
        db,
        request=request,
        action="license_uploaded",
        user=admin_user,
        resource="license/upload",
        detail={"filename": filename, "size": len(content)},
    )
    await db.commit()
    return {"message": "License uploaded", "status": upload_status}


@router.post("/license/refresh")
async def license_refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    刷新授权状态。
    """
    status = check_license()
    await _add_operation_audit_log(
        db,
        request=request,
        action="license_refreshed",
        user=admin_user,
        resource="license/refresh",
        detail={"license_ok": bool(status.get("ok")), "reason": status.get("reason")},
    )
    await db.commit()
    return status
