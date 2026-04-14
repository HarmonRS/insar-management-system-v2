#!/usr/bin/env python3
"""
Read-only smoke check for the catalog-first D-InSAR architecture.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

from sqlalchemy import func, select


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = _project_root()
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from backend.app import database
from backend.app.config import ensure_project_env_loaded, settings
from backend.app.db_maintenance import ensure_database_ready
from backend.app.models import AiDiagnosisORM, HazardPointORM, ResultProductORM
from backend.app.services.dinsar_read_service import dinsar_read_service
from backend.app.services.health_service import get_health_status
from backend.app.services.spatial_service import spatial_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a read-only smoke check for D-InSAR catalog/compat integration."
    )
    parser.add_argument(
        "--skip-maintenance",
        action="store_true",
        help="Do not run database self-maintenance before the smoke check.",
    )
    parser.add_argument(
        "--include-external",
        action="store_true",
        help="Include external checks such as nginx and Ollama.",
    )
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Do not force a schema refresh inside health_service.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Print compact JSON instead of pretty JSON.",
    )
    return parser.parse_args()


def _print_json(payload: Dict[str, Any], compact: bool) -> None:
    if compact:
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def _run_smoke(include_external: bool, refresh: bool) -> Dict[str, Any]:
    session_factory = database.AsyncSessionLocal
    if session_factory is None:
        raise RuntimeError("AsyncSessionLocal is not initialized.")

    health = await get_health_status(
        include_external=include_external,
        include_details=True,
        full=True,
        refresh=refresh,
    )

    async with session_factory() as db:
        catalog_records = await dinsar_read_service.list_catalog_records(db)
        compat_count = await dinsar_read_service.count_compat_records(db)
        compat_records = await dinsar_read_service.list_compat_records(db, limit=5, offset=0)

        diagnosis_total = int(
            (await db.execute(select(func.count(AiDiagnosisORM.id)))).scalar_one() or 0
        )
        hazard_id = (
            await db.execute(
                select(HazardPointORM.id).order_by(HazardPointORM.id.asc()).limit(1)
            )
        ).scalar_one_or_none()
        spatial_count = None
        if hazard_id is not None:
            spatial_records = await spatial_service.find_dinsar_results_near_hazard(
                db,
                int(hazard_id),
            )
            spatial_count = len(spatial_records)

        product_count = int(
            (
                await db.execute(
                    select(func.count(ResultProductORM.id)).where(
                        ResultProductORM.catalog_name == "dinsar"
                    )
                )
            ).scalar_one()
            or 0
        )

    return {
        "health_ok": bool(health.get("ok")),
        "database": {
            "ok": bool(health.get("database", {}).get("ok")),
            "schema_ok": bool(health.get("database", {}).get("schema_ok")),
            "postgis_ok": bool(health.get("database", {}).get("postgis_ok")),
        },
        "worker": {
            "ok": bool(health.get("worker", {}).get("ok")),
            "worker_count": int(health.get("worker", {}).get("worker_count") or 0),
        },
        "dinsar_result_catalog": health.get("dinsar_result_catalog", {}),
        "dinsar_bridge": health.get("dinsar_bridge", {}),
        "source_roots": health.get("source_roots", {}),
        "reads": {
            "catalog_records": len(catalog_records),
            "compat_records": compat_count,
            "compat_preview_records": len(compat_records),
            "sample_public_ids": [
                int(record.compat_row.id)
                for record in compat_records
                if getattr(record, "compat_row", None) is not None
            ][:5],
        },
        "diagnosis_total": diagnosis_total,
        "spatial_count_for_first_hazard": spatial_count,
        "product_count_direct": product_count,
    }


def main() -> int:
    args = parse_args()
    ensure_project_env_loaded()
    database_url = settings.DATABASE_URL
    if not database_url:
        print("[ERROR] DATABASE_URL not found in .env")
        return 1

    maintenance = None
    if not args.skip_maintenance:
        maintenance = ensure_database_ready(
            database_url,
            bootstrap_admin=True,
            seed_hazard=True,
        )

    database.init_db()
    if database.AsyncSessionLocal is None:
        print("[ERROR] Failed to initialize async database session factory.")
        return 1

    payload = {
        "maintenance": maintenance,
        "smoke": asyncio.run(
            _run_smoke(
                include_external=args.include_external,
                refresh=not args.no_refresh,
            )
        ),
    }
    _print_json(payload, args.compact)

    smoke = payload["smoke"]
    root_blocked = int(smoke.get("source_roots", {}).get("inaccessible_count") or 0) > 0
    worker_missing = not bool(smoke.get("worker", {}).get("ok"))
    return 2 if root_blocked or worker_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
