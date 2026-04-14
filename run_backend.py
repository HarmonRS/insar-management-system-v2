import os

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    # Load environment variables
    load_dotenv()

    # Fix PROJ database version conflict
    # PostgreSQL's PROJ database is outdated (v2), but GDAL/pyproj expects v3+
    # Priority: 1. PROJ_LIB env var (if set in .env)
    #           2. pyproj's bundled PROJ data (auto-detect)
    print("[*] Configuring PROJ database...")

    proj_lib_env = os.getenv("PROJ_LIB")

    if proj_lib_env:
        # User explicitly set PROJ_LIB in .env
        if os.path.exists(proj_lib_env):
            os.environ["PROJ_LIB"] = proj_lib_env
            print(f"[*] Using PROJ_LIB from .env: {proj_lib_env}")
        else:
            print(f"[WARN] PROJ_LIB path does not exist: {proj_lib_env}")
            print(f"[WARN] Will try to auto-detect pyproj PROJ data...")
            proj_lib_env = None  # Fall back to auto-detect

    if not proj_lib_env:
        # Auto-detect pyproj's PROJ data directory
        try:
            import pyproj
            pyproj_proj_dir = pyproj.datadir.get_data_dir()
            if pyproj_proj_dir and os.path.exists(pyproj_proj_dir):
                # Force override PROJ_LIB to use pyproj's PROJ data
                os.environ["PROJ_LIB"] = pyproj_proj_dir
                print(f"[*] Auto-detected PROJ_LIB: {pyproj_proj_dir}")
                print(f"[*] PROJ version: {pyproj.proj_version_str}")
            else:
                print(f"[WARN] pyproj PROJ data directory not found: {pyproj_proj_dir}")
        except ImportError:
            print("[WARN] pyproj not installed, PROJ configuration skipped")
        except Exception as e:
            print(f"[WARN] Could not configure PROJ_LIB: {e}")

    port = int(os.getenv("PORT", 8000))
    display_host = os.getenv("SERVER_HOST") or "localhost"
    bind_host = os.getenv("BACKEND_BIND_HOST", "127.0.0.1")

    print("[*] Starting InSAR backend...")
    print(f"[*] Backend bind address: {bind_host}:{port}")
    print(f"[*] Frontend URL (via Nginx): http://{display_host}")
    print(f"[*] Backend URL (internal): http://{bind_host}:{port}")
    print(f"[*] API Docs (internal): http://{bind_host}:{port}/docs")

    # Bind locally for safety
    log_level = os.getenv("UVICORN_LOG_LEVEL", "info").strip().lower()
    uvicorn.run("backend.app.main:app", host=bind_host, port=port, reload=False, log_level=log_level)


if __name__ == "__main__":
    main()
