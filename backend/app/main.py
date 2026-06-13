import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import ensure_project_env_loaded, settings

ensure_project_env_loaded()

from . import database
from .api import router as api_router
from .db_maintenance import ensure_database_ready
from .scheduler import scheduler_manager
from .services.health_service import get_health_status
from .services.manifest_inventory_service import manifest_inventory_service
from .services.pairing_state_service import pairing_state_service
from .services.psinsar_catalog_service import psinsar_catalog_service
from .services.result_catalog_service import result_catalog_service
from .services.root_registry_service import root_registry_service
from .services.sbas_insar_catalog_service import sbas_insar_catalog_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理：启动时初始化数据库、空间扩展与表结构；关闭时停止调度器。
    """
    settings.ensure_dirs()
    maintenance_result = await asyncio.to_thread(
        ensure_database_ready,
        settings.DATABASE_URL,
        bootstrap_admin=True,
        seed_hazard=True,
    )
    database.init_db()
    try:
        root_registry_bootstrap = await root_registry_service.sync_from_settings()
    except Exception as exc:
        root_registry_bootstrap = {
            "synced": 0,
            "created": 0,
            "updated": 0,
            "disabled": 0,
            "cursor_created_or_updated": 0,
            "summary": {},
            "error": str(exc),
        }
    try:
        manifest_inventory_bootstrap = await manifest_inventory_service.sync_manifest_roots()
    except Exception as exc:
        manifest_inventory_bootstrap = {
            "scanned_roots": 0,
            "skipped_roots": 0,
            "manifest_count": 0,
            "created": 0,
            "updated": 0,
            "removed": 0,
            "invalid": 0,
            "errors": [{"error": str(exc)}],
            "results": [],
            "skipped": [],
        }
    try:
        catalog_bootstrap = await result_catalog_service.bootstrap_catalog_on_startup_clean()
    except Exception as exc:
        catalog_bootstrap = {
            "storage_root": settings.DINSAR_PRODUCT_DIR,
            "manifest_count": 0,
            "db_count": 0,
            "needs_rebuild": False,
            "queued": False,
            "error": str(exc),
        }
    try:
        ps_catalog_bootstrap = await psinsar_catalog_service.bootstrap_catalog_on_startup_clean()
    except Exception as exc:
        ps_catalog_bootstrap = {
            "storage_root": settings.TIMESERIES_PRODUCT_DIR,
            "manifest_count": 0,
            "db_count": 0,
            "needs_rebuild": False,
            "queued": False,
            "error": str(exc),
        }
    try:
        sbas_catalog_bootstrap = await sbas_insar_catalog_service.bootstrap_catalog_on_startup_clean()
    except Exception as exc:
        sbas_catalog_bootstrap = {
            "storage_root": settings.GAMMA_SBAS_WORK_ROOT,
            "manifest_count": 0,
            "db_count": 0,
            "needs_rebuild": False,
            "rebuilt": False,
            "queued": False,
            "error": str(exc),
        }
    try:
        pairing_bootstrap = await pairing_state_service.bootstrap_pairing_cache_state()
    except Exception as exc:
        pairing_bootstrap = {
            "state_present": False,
            "status": "ERROR",
            "scene_count": 0,
            "pair_count": 0,
            "dirty_scene_count": 0,
            "metric_version": pairing_state_service.metric_version,
            "needs_rebuild": True,
            "cache_ready": False,
            "error": str(exc),
        }

    print(
        ">>> [DB] host={0} db={1} mismatch={2} reset={3}".format(
            maintenance_result.get("host") or "?",
            maintenance_result.get("database") or "?",
            "YES" if maintenance_result.get("mismatch_detected") else "NO",
            "YES" if maintenance_result.get("schema_reset") else "NO",
        )
    )
    if maintenance_result.get("added_columns"):
        print(f">>> [DB] Added columns: {maintenance_result['added_columns']}")
    if maintenance_result.get("created_indexes"):
        print(f">>> [DB] Created indexes: {maintenance_result['created_indexes']}")
    if maintenance_result.get("alembic_version"):
        alembic_status = maintenance_result["alembic_version"]
        print(f">>> [DB] Alembic version marker: {alembic_status.get('revision')}")
    if maintenance_result.get("admin", {}).get("message"):
        print(f">>> [DB] {maintenance_result['admin']['message']}")
    if maintenance_result.get("hazard_seed", {}).get("message"):
        print(f">>> [DB] {maintenance_result['hazard_seed']['message']}")
    print(
        ">>> [Roots] synced={0} created={1} updated={2} disabled={3} cursors={4} total={5} missing={6}".format(
            root_registry_bootstrap.get("synced", 0),
            root_registry_bootstrap.get("created", 0),
            root_registry_bootstrap.get("updated", 0),
            root_registry_bootstrap.get("disabled", 0),
            root_registry_bootstrap.get("cursor_created_or_updated", 0),
            root_registry_bootstrap.get("summary", {}).get("total_roots", 0),
            root_registry_bootstrap.get("summary", {}).get("missing_roots", 0),
        )
    )
    if root_registry_bootstrap.get("error"):
        print(f">>> [Roots] Startup sync failed: {root_registry_bootstrap['error']}")
    print(
        ">>> [ManifestInventory] roots={0} skipped={1} manifests={2} created={3} updated={4} removed={5} invalid={6} errors={7}".format(
            manifest_inventory_bootstrap.get("scanned_roots", 0),
            manifest_inventory_bootstrap.get("skipped_roots", 0),
            manifest_inventory_bootstrap.get("manifest_count", 0),
            manifest_inventory_bootstrap.get("created", 0),
            manifest_inventory_bootstrap.get("updated", 0),
            manifest_inventory_bootstrap.get("removed", 0),
            manifest_inventory_bootstrap.get("invalid", 0),
            len(manifest_inventory_bootstrap.get("errors") or []),
        )
    )
    if manifest_inventory_bootstrap.get("errors"):
        print(f">>> [ManifestInventory] Errors: {manifest_inventory_bootstrap['errors']}")
    print(
        ">>> [Catalog] root={0} manifests={1} db={2} rebuild={3} queued={4}".format(
            catalog_bootstrap.get("storage_root") or "?",
            catalog_bootstrap.get("manifest_count", 0),
            catalog_bootstrap.get("db_count", 0),
            "YES" if catalog_bootstrap.get("needs_rebuild") else "NO",
            "YES" if catalog_bootstrap.get("queued") else "NO",
        )
    )
    if catalog_bootstrap.get("error"):
        print(f">>> [Catalog] Startup bootstrap failed: {catalog_bootstrap['error']}")
    if catalog_bootstrap.get("compat_sync"):
        compat_sync = catalog_bootstrap["compat_sync"]
        print(
            ">>> [Catalog Compat] compat={0} created={1} updated={2} mirrored={3}".format(
                compat_sync.get("compat_count", 0),
                compat_sync.get("created", 0),
                compat_sync.get("updated", 0),
                compat_sync.get("mirrored_product_fields", 0),
            )
        )
    if catalog_bootstrap.get("compat_error"):
        print(f">>> [Catalog Compat] Startup sync failed: {catalog_bootstrap['compat_error']}")
    print(
        ">>> [Timeseries Catalog] root={0} manifests={1} db={2} rebuild={3} queued={4}".format(
            ps_catalog_bootstrap.get("storage_root") or "?",
            ps_catalog_bootstrap.get("manifest_count", 0),
            ps_catalog_bootstrap.get("db_count", 0),
            "YES" if ps_catalog_bootstrap.get("needs_rebuild") else "NO",
            "YES" if ps_catalog_bootstrap.get("queued") else "NO",
        )
    )
    if ps_catalog_bootstrap.get("error"):
        print(f">>> [Timeseries Catalog] Startup bootstrap failed: {ps_catalog_bootstrap['error']}")
    print(
        ">>> [SBAS Catalog] root={0} runs={1} db={2} rebuild={3} rebuilt={4}".format(
            sbas_catalog_bootstrap.get("storage_root") or "?",
            sbas_catalog_bootstrap.get("manifest_count", 0),
            sbas_catalog_bootstrap.get("db_count", 0),
            "YES" if sbas_catalog_bootstrap.get("needs_rebuild") else "NO",
            "YES" if sbas_catalog_bootstrap.get("rebuilt") else "NO",
        )
    )
    if sbas_catalog_bootstrap.get("error"):
        print(f">>> [SBAS Catalog] Startup bootstrap failed: {sbas_catalog_bootstrap['error']}")
    print(
        ">>> [Pairing] status={0} scenes={1} pairs={2} dirty={3} metric={4} rebuild={5}".format(
            pairing_bootstrap.get("status") or "?",
            pairing_bootstrap.get("scene_count", 0),
            pairing_bootstrap.get("pair_count", 0),
            pairing_bootstrap.get("dirty_scene_count", 0),
            pairing_bootstrap.get("metric_version") or "?",
            "YES" if pairing_bootstrap.get("needs_rebuild") else "NO",
        )
    )
    if pairing_bootstrap.get("error"):
        print(f">>> [Pairing] Startup bootstrap failed: {pairing_bootstrap['error']}")
    print(
        ">>> [Gamma SBAS] enabled={0} runtime={1} distro={2} python={3} work_root={4} product_root={5}".format(
            "YES" if settings.GAMMA_SBAS_ENABLED else "NO",
            settings.GAMMA_SBAS_RUNTIME_ID or "?",
            settings.GAMMA_SBAS_WSL_DISTRO or "?",
            settings.GAMMA_SBAS_PYTHON or "?",
            settings.GAMMA_SBAS_WORK_ROOT or "?",
            settings.GAMMA_SBAS_PRODUCT_ROOT or "?",
        )
    )

    # 当前项目默认 Manual-only 扫描模式，保留调度器关闭状态。
    # scheduler_manager.start()

    try:
        health = await get_health_status(include_external=False)
        db_ok = health.get("database", {}).get("ok")
        schema_ok = health.get("database", {}).get("schema_ok")
        worker_ok = health.get("worker", {}).get("ok")
        dinsar_catalog_ok = health.get("dinsar_result_catalog", {}).get("ok")
        psinsar_catalog_ok = (
            health.get("timeseries_result_catalog", {})
            or health.get("psinsar_result_catalog", {})
        ).get("ok")
        sbas_catalog_ok = health.get("sbas_insar_result_catalog", {}).get("ok")
        pairing_ok = health.get("pairing_system", {}).get("ok")
        idl_ok = health.get("idl", {}).get("ok")
        product_packages_ok = health.get("product_packages", {}).get("ok")
        wsl_runtime_ok = health.get("wsl_runtime", {}).get("ok")
        print(
            ">>> [Health] DB:{0} Schema:{1} Worker:{2} DInSAR-Catalog:{3} Timeseries-Catalog:{4} SBAS-Catalog:{5} Packages:{6} WSL:{7} Pairing:{8} IDL:{9}".format(
                "OK" if db_ok else "FAIL",
                "OK" if schema_ok else "FAIL",
                "OK" if worker_ok else "FAIL",
                "OK" if dinsar_catalog_ok else "FAIL",
                "OK" if psinsar_catalog_ok else "FAIL",
                "OK" if sbas_catalog_ok else "FAIL",
                "OK" if product_packages_ok else "FAIL",
                "OK" if wsl_runtime_ok else "FAIL",
                "OK" if pairing_ok else "FAIL",
                "OK" if idl_ok else "FAIL",
            )
        )
    except Exception as exc:
        print(f">>> [Health] Startup check failed: {exc}")

    yield

    scheduler_manager.shutdown()


app = FastAPI(title="雷达数据管理系统 API", lifespan=lifespan)


def _parse_cors_origins() -> list[str]:
    raw_value = settings.CORS_ORIGINS or "*"
    origins = [origin.strip() for origin in raw_value.split(",") if origin.strip()]
    return origins or ["*"]


CORS_ORIGINS = _parse_cors_origins()
CORS_ALLOW_CREDENTIALS = settings.CORS_ALLOW_CREDENTIALS
CORS_STRICT_MODE = settings.CORS_STRICT_MODE

if "*" in CORS_ORIGINS and CORS_ALLOW_CREDENTIALS:
    if CORS_STRICT_MODE:
        raise RuntimeError(
            "Invalid CORS config: wildcard origin '*' cannot be used with credentials. "
            "Set CORS_ORIGINS to explicit origins or disable credentials."
        )
    print(">>> [CORS] Detected wildcard origin with credentials; force disable credentials.")
    CORS_ALLOW_CREDENTIALS = False

if "*" in CORS_ORIGINS:
    logging.warning(
        "[CORS] CORS_ORIGINS is set to wildcard '*'. "
        "This is insecure in production; set CORS_ORIGINS to explicit allowed origins."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")


@app.get("/")
def read_root():
    return {"message": "欢迎使用雷达数据管理系统 API"}
