#!/usr/bin/env python3
"""
Deployment configuration validation entrypoint.
"""
import os
import sys


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = _project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from backend.app.config import ensure_project_env_loaded, validate_runtime_config


def main() -> int:
    ensure_project_env_loaded()
    result = validate_runtime_config()

    print("[*] Validating deployment configuration...")
    for item in result.get("info", []):
        print(f"[INFO] {item}")
    for item in result.get("warnings", []):
        print(f"[WARN] {item}")
    for item in result.get("errors", []):
        print(f"[ERROR] {item}")

    if result.get("ok"):
        print("[OK] Deployment configuration check passed.")
        return 0

    print("[FAIL] Deployment configuration check failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
