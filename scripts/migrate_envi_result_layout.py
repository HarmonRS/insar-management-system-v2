from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import ensure_project_env_loaded

ensure_project_env_loaded()

from backend.app import database
from backend.app.services.dinsar_layout_migration_service import dinsar_layout_migration_service
from backend.app.services.manifest_inventory_service import manifest_inventory_service
from backend.app.services.result_catalog_service import result_catalog_service
from backend.app.services.root_registry_service import root_registry_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize managed ENVI D-InSAR run layouts under DINSAR_PRODUCT_DIR."
    )
    parser.add_argument(
        "--root-dir",
        default=None,
        help="Managed D-InSAR publish root. Defaults to settings.DINSAR_PRODUCT_DIR.",
    )
    parser.add_argument(
        "--pair-key",
        action="append",
        default=None,
        help="Only migrate the specified pair_key. Repeatable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after inspecting this many run directories.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect and report only. Do not move files or rewrite metadata.",
    )
    parser.add_argument(
        "--skip-catalog-sync",
        action="store_true",
        help="Skip manifest inventory sync and catalog rebuild after migration.",
    )
    return parser.parse_args()


async def _sync_catalog() -> Dict[str, Any]:
    database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")

    root_sync = await root_registry_service.sync_from_settings()
    async with database.AsyncSessionLocal() as db:
        inventory_result = await manifest_inventory_service.sync_manifest_roots(db)
        rebuild_result = await result_catalog_service.rebuild_catalog(
            db,
            full_rebuild=True,
        )
    return {
        "root_registry": root_sync,
        "manifest_inventory": inventory_result,
        "catalog_rebuild": rebuild_result,
    }


async def main_async() -> int:
    args = parse_args()
    result = dinsar_layout_migration_service.migrate_managed_envi_runs(
        root_dir=args.root_dir,
        pair_keys=args.pair_key,
        limit=args.limit,
        dry_run=args.dry_run,
    )

    should_sync_catalog = (
        not args.dry_run
        and not args.skip_catalog_sync
        and int(result.get("migrated_count", 0) or 0) + int(result.get("rewritten_count", 0) or 0) > 0
        and int(result.get("failed_count", 0) or 0) == 0
    )
    if should_sync_catalog:
        result["post_sync"] = await _sync_catalog()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if int(result.get("failed_count", 0) or 0) == 0 else 1


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
