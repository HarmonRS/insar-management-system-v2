#!/usr/bin/env python3
"""
Database Connection Health Check Script.

Runs before the main system startup to verify PostgreSQL connectivity and authentication.
"""
import os
import socket
import sys
from urllib.parse import urlparse


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = _project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.app.config import ensure_project_env_loaded, settings


def check_database_connection() -> bool:
    ensure_project_env_loaded()
    print("[*] Loaded deployment config.")

    database_url = settings.DATABASE_URL
    if not database_url:
        print("[ERROR] DATABASE_URL not found. Please check .env.")
        return False

    print("[*] Parsing database URL...")
    parsed = urlparse(database_url)

    host = parsed.hostname
    port = parsed.port or 5432
    user = parsed.username
    password = parsed.password
    dbname = parsed.path.strip("/")

    if not host:
        print("[ERROR] Unable to parse database host.")
        return False

    print(f"[*] Connecting to database server {host}:{port} ...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        result = sock.connect_ex((host, port))
        if result != 0:
            print(f"[FAIL] Cannot connect to {host}:{port}. Port closed or blocked.")
            return False
        print(f"[OK]   Port {port} is reachable.")
    except socket.gaierror:
        print(f"[FAIL] DNS lookup failed: {host}")
        return False
    except Exception as exc:
        print(f"[FAIL] Connection error: {exc}")
        return False
    finally:
        sock.close()

    print("[*] Verifying credentials and database access...")
    conn = None
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.fetchone()
        cur.close()
        conn.commit()
        print(f"[OK]   Database {dbname} authenticated successfully.")
        return True

    except ImportError:
        print("[WARN] psycopg2 not installed, trying SQLAlchemy fallback...")
        try:
            from sqlalchemy import create_engine, text

            sync_db_url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
            engine = create_engine(sync_db_url, connect_timeout=5)
            with engine.connect() as conn_sa:
                conn_sa.execute(text("SELECT 1"))
            print(f"[OK]   Database {dbname} authenticated (SQLAlchemy fallback).")
            return True
        except ImportError:
            print("[WARN] No Python database drivers available.")
            print("[INFO] Port check passed; startup will continue.")
            return True
        except Exception as exc:
            print(f"[FAIL] SQLAlchemy validation failed: {exc}")
            return False
        finally:
            if "engine" in locals() and engine:
                engine.dispose()
    except Exception as exc:
        print("\n[FAIL] Database connection failed.")
        print("=" * 50)
        print(f"Error type: {type(exc).__name__}")

        diag_message = str(exc)
        if hasattr(exc, "diag") and exc.diag:
            diag_message = exc.diag.message_primary or str(exc)

        print(f"Details: {diag_message}")
        print("=" * 50)

        if "password authentication failed" in diag_message:
            print("[HINT] Check the password in DATABASE_URL (.env).")
        elif "does not exist" in diag_message:
            print(f"[HINT] Database '{dbname}' does not exist.")
        elif "connection refused" in diag_message:
            print("[HINT] Connection refused. Verify PostgreSQL allows this IP.")
        else:
            print("[HINT] Unknown connection error. Check database configuration.")

        print("\n[CRITICAL] Unable to connect to database. Startup aborted.")
        return False
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    ok = check_database_connection()
    sys.exit(0 if ok else 1)
