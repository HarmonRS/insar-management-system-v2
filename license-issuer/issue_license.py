"""
InSAR license issuer for the LIC2 format.

This module keeps the existing CLI workflow, and also exposes reusable
functions for the desktop GUI.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional


ISSUER_DIR = Path(__file__).resolve().parent
PRIVATE_KEY_FILE = ISSUER_DIR / "private_key.b64"
PUBLIC_KEY_FILE = ISSUER_DIR / "public_key.b64"
BACKEND_LICENSE_SERVICE_FILE = ISSUER_DIR.parent / "backend" / "app" / "license_service.py"
LICENSE_HEADER = b"LIC2"
PUBLIC_KEY_PATTERN = re.compile(r'^_PUBLIC_KEY_B64\s*=\s*"([^"]*)"', re.MULTILINE)


def _load_cryptography():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )

        return Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature, serialization
    except ImportError:
        raise RuntimeError("Missing dependency: pip install cryptography")


def resolve_backend_license_service_file(target: str | Path | None = None) -> Path:
    if target is None:
        env_target = str(os.environ.get("LICENSE_ISSUER_BACKEND_FILE", "")).strip()
        if env_target:
            return Path(env_target).expanduser()
        return BACKEND_LICENSE_SERVICE_FILE
    return Path(target).expanduser()


def _run(args: list[str]) -> str:
    try:
        output = subprocess.check_output(
            args,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
        return output.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _run_ps(command: str) -> str:
    return _run(["powershell", "-NoProfile", "-Command", command])


def _pick_value(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return ""
    values = [value for value in lines[1:] if value.lower() not in {"serialnumber", "uuid"}]
    return values[0] if values else ""


def get_machine_fingerprint() -> str:
    uuid_text = _run(["wmic", "csproduct", "get", "uuid"])
    if not uuid_text:
        uuid_text = _run_ps("(Get-CimInstance Win32_ComputerSystemProduct).UUID")

    disk_text = _run(["wmic", "diskdrive", "get", "serialnumber"])
    if not disk_text:
        disk_text = _run_ps(
            "(Get-CimInstance Win32_DiskDrive | Select-Object -First 1 -ExpandProperty SerialNumber)"
        )

    uuid_value = _pick_value(uuid_text)
    disk_value = _pick_value(disk_text)
    mac_value = f"{uuid.getnode():012x}"
    if not mac_value or mac_value == "000000000000":
        mac_value = _run_ps(
            "(Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
            "Select-Object -First 1 -ExpandProperty MacAddress)"
        ) or ""

    raw = "|".join([uuid_value, disk_value, mac_value])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_public_key_b64(path: Path = PUBLIC_KEY_FILE) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Public key file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def read_private_key_b64(path: Path = PRIVATE_KEY_FILE) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Private key file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def read_backend_public_key_b64(path: str | Path | None = None) -> Optional[str]:
    target_path = resolve_backend_license_service_file(path)
    if not target_path.exists():
        return None
    content = target_path.read_text(encoding="utf-8")
    match = PUBLIC_KEY_PATTERN.search(content)
    return match.group(1) if match else None


def get_key_status(backend_target: str | Path | None = None) -> Dict[str, Any]:
    backend_path = resolve_backend_license_service_file(backend_target)
    public_key_b64 = None
    backend_public_key_b64 = None
    if PUBLIC_KEY_FILE.exists():
        public_key_b64 = read_public_key_b64(PUBLIC_KEY_FILE)
    if backend_path.exists():
        backend_public_key_b64 = read_backend_public_key_b64(backend_path)
    return {
        "private_key_exists": PRIVATE_KEY_FILE.exists(),
        "public_key_exists": PUBLIC_KEY_FILE.exists(),
        "private_key_path": str(PRIVATE_KEY_FILE),
        "public_key_path": str(PUBLIC_KEY_FILE),
        "backend_license_service_path": str(backend_path),
        "backend_license_service_exists": backend_path.exists(),
        "public_key_b64": public_key_b64,
        "backend_public_key_b64": backend_public_key_b64,
        "backend_synced": bool(public_key_b64 and public_key_b64 == backend_public_key_b64),
    }


def _load_private_key():
    Ed25519PrivateKey, _, _, _ = _load_cryptography()
    raw = base64.b64decode(read_private_key_b64(PRIVATE_KEY_FILE))
    return Ed25519PrivateKey.from_private_bytes(raw)


def rotate_key_pair(force: bool = False) -> Dict[str, Any]:
    Ed25519PrivateKey, _, _, serialization = _load_cryptography()

    if PRIVATE_KEY_FILE.exists() and not force:
        raise FileExistsError(
            "Private key already exists. Use force=True only if you really want to rotate it."
        )

    key = Ed25519PrivateKey.generate()
    private_key_b64 = base64.b64encode(
        key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("utf-8")
    public_key_b64 = base64.b64encode(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("utf-8")

    PRIVATE_KEY_FILE.write_text(private_key_b64, encoding="utf-8")
    PUBLIC_KEY_FILE.write_text(public_key_b64, encoding="utf-8")

    return {
        "private_key_path": str(PRIVATE_KEY_FILE),
        "public_key_path": str(PUBLIC_KEY_FILE),
        "public_key_b64": public_key_b64,
        "force": force,
    }


def sync_backend_public_key(
    *,
    public_key_b64: Optional[str] = None,
    target_path: str | Path | None = None,
) -> Dict[str, Any]:
    resolved_target_path = resolve_backend_license_service_file(target_path)
    key_b64 = (public_key_b64 or read_public_key_b64(PUBLIC_KEY_FILE)).strip()
    if not resolved_target_path.exists():
        raise FileNotFoundError(f"Backend license service file not found: {resolved_target_path}")

    content = resolved_target_path.read_text(encoding="utf-8")
    match = PUBLIC_KEY_PATTERN.search(content)
    if not match:
        raise ValueError("Could not find _PUBLIC_KEY_B64 in backend license service.")

    old_key_b64 = match.group(1)
    updated_content = PUBLIC_KEY_PATTERN.sub(
        f'_PUBLIC_KEY_B64 = "{key_b64}"',
        content,
        count=1,
    )
    resolved_target_path.write_text(updated_content, encoding="utf-8")

    return {
        "target_path": str(resolved_target_path),
        "old_public_key_b64": old_key_b64,
        "public_key_b64": key_b64,
        "changed": old_key_b64 != key_b64,
    }


def _normalize_output_path(issued_to: str, output: Optional[str]) -> Path:
    if output:
        return Path(output)
    safe_name = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", issued_to).strip("_")
    safe_name = safe_name[:32] or "license"
    return ISSUER_DIR / f"license_{safe_name}.lic"


def issue_license_file(
    *,
    issued_to: str,
    fingerprint: str,
    days: int = 365,
    output: Optional[str] = None,
) -> Dict[str, Any]:
    if not issued_to.strip():
        raise ValueError("issued_to is required")
    if not fingerprint.strip():
        raise ValueError("fingerprint is required")

    private_key = _load_private_key()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(days=int(days))

    payload = {
        "issued_to": issued_to.strip(),
        "fingerprint": fingerprint.strip(),
        "expires_at": expires_at.isoformat(),
        "issued_at": issued_at.isoformat(),
    }

    payload_b64 = base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    signature = private_key.sign(payload_b64)
    signature_b64 = base64.b64encode(signature)
    blob = LICENSE_HEADER + b"|" + signature_b64 + b"|" + payload_b64

    output_path = _normalize_output_path(payload["issued_to"], output)
    output_path.write_bytes(blob)

    return {
        "output_path": str(output_path),
        "payload": payload,
    }


def verify_license_file(license_file: str | Path) -> Dict[str, Any]:
    _, Ed25519PublicKey, InvalidSignature, _ = _load_cryptography()

    license_path = Path(license_file)
    if not license_path.exists():
        raise FileNotFoundError(f"License file not found: {license_path}")

    public_key_raw = base64.b64decode(read_public_key_b64(PUBLIC_KEY_FILE))
    public_key = Ed25519PublicKey.from_public_bytes(public_key_raw)

    try:
        blob = license_path.read_bytes().strip()
        header, signature_b64, payload_b64 = blob.split(b"|", 2)
        if header != LICENSE_HEADER:
            raise ValueError("Invalid license header")
        public_key.verify(base64.b64decode(signature_b64), payload_b64)
        payload = json.loads(base64.b64decode(payload_b64))
    except (InvalidSignature, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "license_file": str(license_path),
            "reason": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "license_file": str(license_path),
            "reason": f"Failed to parse license: {exc}",
        }

    expires_at_text = str(payload.get("expires_at") or "")
    expires_at = datetime.fromisoformat(expires_at_text)
    expired = datetime.now(timezone.utc) > expires_at

    return {
        "ok": True,
        "license_file": str(license_path),
        "issued_to": payload.get("issued_to"),
        "fingerprint": payload.get("fingerprint"),
        "issued_at": payload.get("issued_at"),
        "expires_at": expires_at_text,
        "expired": expired,
    }


def cmd_rotate_key(args: argparse.Namespace) -> int:
    result = rotate_key_pair(force=bool(args.force))
    print("=" * 60)
    print("New key pair generated")
    print(f"Private key: {result['private_key_path']}")
    print(f"Public key : {result['public_key_path']}")
    print()
    print("Update backend/app/license_service.py with:")
    print(f'_PUBLIC_KEY_B64 = "{result["public_key_b64"]}"')
    print("=" * 60)

    if getattr(args, "sync_backend", False):
        sync_result = sync_backend_public_key(
            public_key_b64=result["public_key_b64"],
            target_path=args.target,
        )
        print(f"Backend public key synced: {sync_result['target_path']}")
    return 0


def cmd_sync_public_key(args: argparse.Namespace) -> int:
    result = sync_backend_public_key(target_path=args.target)
    status = "updated" if result["changed"] else "already synced"
    print(f"Backend public key {status}: {result['target_path']}")
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    result = issue_license_file(
        issued_to=args.to,
        fingerprint=args.fingerprint,
        days=int(args.days),
        output=args.output,
    )
    payload = result["payload"]
    print(f"License file generated: {result['output_path']}")
    print(f"Issued to   : {payload['issued_to']}")
    print(f"Fingerprint : {payload['fingerprint']}")
    print(f"Expires at  : {payload['expires_at']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_license_file(args.license_file)
    if not result["ok"]:
        print(f"[invalid] {result['reason']}")
        return 1
    print("[valid] Signature verified")
    print(f"Issued to   : {result.get('issued_to')}")
    print(f"Fingerprint : {result.get('fingerprint')}")
    print(f"Issued at   : {result.get('issued_at')}")
    print(f"Expires at  : {result.get('expires_at')}")
    print(f"Expired     : {'yes' if result.get('expired') else 'no'}")
    return 0


def cmd_fingerprint(_args: argparse.Namespace) -> int:
    print(get_machine_fingerprint())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="InSAR license issuer")
    sub = parser.add_subparsers(dest="cmd")

    rotate = sub.add_parser("rotate-key", help="Generate a new key pair")
    rotate.add_argument("--force", action="store_true", help="Overwrite existing private/public key files")
    rotate.add_argument(
        "--sync-backend",
        action="store_true",
        help="After rotation, also update backend/app/license_service.py",
    )
    rotate.add_argument("--target", default=None, help="Optional backend/app/license_service.py path")

    sync_public = sub.add_parser("sync-public-key", help="Sync public_key.b64 into backend/app/license_service.py")
    sync_public.add_argument("--target", default=None, help="Optional target file path")

    issue = sub.add_parser("issue", help="Issue a .lic file")
    issue.add_argument("--to", required=True, help="Organization or customer name")
    issue.add_argument("--fingerprint", required=True, help="Target machine fingerprint")
    issue.add_argument("--days", default=365, help="Validity in days")
    issue.add_argument("--output", default=None, help="Output .lic path")

    verify = sub.add_parser("verify", help="Verify a .lic file against public_key.b64")
    verify.add_argument("license_file", help="Path to .lic file")

    sub.add_parser("fingerprint", help="Print the current machine fingerprint")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd == "rotate-key":
            return cmd_rotate_key(args)
        if args.cmd == "sync-public-key":
            return cmd_sync_public_key(args)
        if args.cmd == "issue":
            return cmd_issue(args)
        if args.cmd == "verify":
            return cmd_verify(args)
        if args.cmd == "fingerprint":
            return cmd_fingerprint(args)

        parser.print_help()
        return 0
    except Exception as exc:
        print(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
