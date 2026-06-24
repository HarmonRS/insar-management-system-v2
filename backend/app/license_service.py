"""
授权验证服务 — LIC2 方案
========================
- 公钥硬编码在源码中，无需 .env 配置
- 无本地 state 文件，无时间防回退（去掉脆弱的本地状态）
- 授权文件格式：LIC2|<ed25519签名_b64>|<payload_json_b64>
- 私钥只在签发工具（license-issuer/）中，不随代码部署

私钥丢失处理：
  1. 在 license-issuer/ 运行 rotate-key --force 生成新密钥对
  2. 将新公钥更新到本文件的 _PUBLIC_KEY_B64 常量
  3. 重新部署后端，重新为客户签发授权文件
"""

import base64
import hashlib
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from .config import settings

# ── 公钥（硬编码，与 license-issuer/public_key.b64 对应） ─────────────────────
# 私钥丢失后，用新密钥对重新签发授权，并将此处更新为新公钥。
_PUBLIC_KEY_B64 = "QOpR1c3bONDwOzrj3IVTogE1ZHIphpwxJY8nhWa09yw="

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_APP_DIR)
LICENSE_PATH_DEFAULT = os.path.join(_BACKEND_DIR, "license", "license.lic")
LICENSE_STATUS_CACHE_SECONDS = int(os.getenv("LICENSE_STATUS_CACHE_SECONDS", "30"))
_LICENSE_STATUS_CACHE: Dict[str, Any] = {
    "path": None,
    "mtime": None,
    "checked_at": 0.0,
    "payload": None,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── 机器指纹 ──────────────────────────────────────────────────────────────────

def _run_cmd(args: list) -> str:
    try:
        out = subprocess.check_output(
            args, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, shell=False
        )
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _run_powershell(cmd: str) -> str:
    return _run_cmd(["powershell", "-NoProfile", "-Command", cmd])


def _pick_value(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return ""
    values = [v for v in lines[1:] if v and v.lower() not in ("serialnumber", "uuid")]
    return values[0] if values else ""


def _get_machine_fingerprint() -> str:
    uuid_text = _run_cmd(["wmic", "csproduct", "get", "uuid"])
    if not uuid_text:
        uuid_text = _run_powershell("(Get-CimInstance Win32_ComputerSystemProduct).UUID")
    disk_text = _run_cmd(["wmic", "diskdrive", "get", "serialnumber"])
    if not disk_text:
        disk_text = _run_powershell(
            "(Get-CimInstance Win32_DiskDrive | Select-Object -First 1 -ExpandProperty SerialNumber)"
        )

    uuid_val = _pick_value(uuid_text)
    disk_val = _pick_value(disk_text)
    mac_val  = f"{uuid.getnode():012x}"
    if not mac_val or mac_val == "000000000000":
        mac_val = _run_powershell(
            "(Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object -First 1 -ExpandProperty MacAddress)"
        ) or ""

    raw = "|".join([uuid_val, disk_val, mac_val])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── 授权验证 ──────────────────────────────────────────────────────────────────

def _license_result(result: Dict[str, Any], *, use_cache: bool, path: str, mtime: Optional[float]) -> Dict[str, Any]:
    if use_cache:
        _LICENSE_STATUS_CACHE.update({
            "path": path,
            "mtime": mtime,
            "checked_at": time.monotonic(),
            "payload": dict(result),
        })
    return result


def check_license(license_path: Optional[str] = None) -> Dict[str, Any]:
    use_cache = license_path is None
    license_path = license_path or settings.LICENSE_PATH or LICENSE_PATH_DEFAULT
    mtime = os.path.getmtime(license_path) if os.path.exists(license_path) else None

    if use_cache:
        cached = _LICENSE_STATUS_CACHE.get("payload")
        cache_age = time.monotonic() - float(_LICENSE_STATUS_CACHE.get("checked_at") or 0.0)
        if (
            cached is not None
            and _LICENSE_STATUS_CACHE.get("path") == license_path
            and _LICENSE_STATUS_CACHE.get("mtime") == mtime
            and cache_age <= LICENSE_STATUS_CACHE_SECONDS
        ):
            return dict(cached)

    if not os.path.exists(license_path):
        return _license_result({"ok": False, "reason": "未找到授权文件"}, use_cache=use_cache, path=license_path, mtime=mtime)

    try:
        blob = open(license_path, "rb").read().strip()
        parts = blob.split(b"|", 2)
        if len(parts) != 3 or parts[0] != b"LIC2":
            return _license_result({"ok": False, "reason": "授权文件格式无效"}, use_cache=use_cache, path=license_path, mtime=mtime)

        _, sig_b64, payload_b64 = parts
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(_PUBLIC_KEY_B64))
        pub.verify(base64.b64decode(sig_b64), payload_b64)
        payload = json.loads(base64.b64decode(payload_b64))
    except InvalidSignature:
        return _license_result({"ok": False, "reason": "授权文件签名无效（可能已被篡改）"}, use_cache=use_cache, path=license_path, mtime=mtime)
    except Exception as e:
        return _license_result({"ok": False, "reason": f"授权文件解析失败: {e}"}, use_cache=use_cache, path=license_path, mtime=mtime)

    fp_expected = payload.get("fingerprint")
    fp_actual   = _get_machine_fingerprint()
    if not fp_expected or fp_expected != fp_actual:
        return _license_result({"ok": False, "reason": "机器指纹不匹配"}, use_cache=use_cache, path=license_path, mtime=mtime)

    expires_at = payload.get("expires_at")
    if not expires_at:
        return _license_result({"ok": False, "reason": "授权文件缺少有效期"}, use_cache=use_cache, path=license_path, mtime=mtime)
    try:
        expires_dt = datetime.fromisoformat(expires_at)
    except Exception:
        return _license_result({"ok": False, "reason": "有效期格式错误"}, use_cache=use_cache, path=license_path, mtime=mtime)

    if _now_utc() > expires_dt:
        return _license_result({"ok": False, "reason": "授权已过期"}, use_cache=use_cache, path=license_path, mtime=mtime)

    return _license_result({
        "ok":          True,
        "issued_to":   payload.get("issued_to"),
        "expires_at":  expires_at,
        "fingerprint": fp_actual,
        "license_path": license_path,
    }, use_cache=use_cache, path=license_path, mtime=mtime)
