"""Build WebP preview caches for archive-managed LT1 and Sentinel-1 assets."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import database  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.services.asset_inventory_service import asset_inventory_service  # noqa: E402


def _normalize_families(values: Optional[Iterable[str]]) -> List[str]:
    families: List[str] = []
    for value in values or ["LT1", "S1"]:
        for item in str(value or "").split(","):
            family = item.strip().upper()
            if family and family not in families:
                families.append(family)
    invalid = [item for item in families if item not in {"LT1", "S1"}]
    if invalid:
        raise SystemExit(f"Unsupported family: {', '.join(invalid)}")
    return families or ["LT1", "S1"]


async def _run(args: argparse.Namespace) -> dict:
    families = _normalize_families(args.family)
    database.init_db(settings.DATABASE_URL)
    summary = await asset_inventory_service.build_archive_preview_caches(
        families=families,
        limit=args.limit,
        force=args.force,
        apply=bool(args.apply),
        progress_start=0,
        progress_end=100,
    )
    return {"apply": bool(args.apply), **summary}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build LT1/Sentinel-1 archive preview WebP caches.")
    parser.add_argument("--apply", action="store_true", help="write preview cache files and update database rows")
    parser.add_argument("--family", action="append", help="family to process: LT1, S1, or comma-separated values")
    parser.add_argument("--limit", type=int, default=0, help="maximum rows to build; 0 means all pending rows")
    parser.add_argument("--force", action="store_true", help="rebuild even if preview_cache_status is READY")
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
