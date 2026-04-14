#!/usr/bin/env python3
"""
Database initialization and auto-maintenance entrypoint.
"""
import os
import sys


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = _project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from backend.app.config import ensure_project_env_loaded, settings


def main() -> int:
    ensure_project_env_loaded()

    database_url = settings.DATABASE_URL
    if not database_url:
        print("[ERROR] DATABASE_URL not found in .env")
        return 1

    from backend.app.db_maintenance import ensure_database_ready

    print("[*] Validating and maintaining database schema...")
    result = ensure_database_ready(database_url, bootstrap_admin=True, seed_hazard=True)

    print(f"[*] Database host: {result.get('host')}")
    print(f"[*] Database name: {result.get('database')}")
    if result.get("mismatch_detected"):
        print("[*] Schema mismatch detected and handled.")
        for reason in result.get("mismatch_reasons", []):
            print(f"    - {reason}")
    else:
        print("[OK] Database schema already matches ORM metadata.")

    added_columns = result.get("added_columns", [])
    if added_columns:
        print(f"[OK] Added missing columns ({len(added_columns)}): {added_columns}")

    if result.get("schema_reset"):
        print("[WARN] Schema was reset because DB_SCHEMA_RESET_ON_MISMATCH and DB_SCHEMA_RESET_CONFIRM are enabled.")

    if result.get("applied_sql_files"):
        print(f"[OK] Applied SQL maintenance files: {', '.join(result['applied_sql_files'])}")

    admin_status = result.get("admin") or {}
    if admin_status.get("message"):
        print(f"[OK] {admin_status['message']}")

    hazard_status = result.get("hazard_seed") or {}
    if hazard_status.get("message"):
        prefix = "[OK]" if hazard_status.get("seeded") or hazard_status.get("count") else "[INFO]"
        print(f"{prefix} {hazard_status['message']}")

    print("\n========================================")
    print(" Database Initialization Complete! ")
    print("========================================")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"\n[CRITICAL ERROR] {exc}")
        raise SystemExit(1)
