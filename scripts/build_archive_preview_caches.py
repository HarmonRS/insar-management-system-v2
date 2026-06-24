"""Build WebP preview caches for archive-managed LT1 and Sentinel-1 assets."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
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

    def _print_progress(payload: dict) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        event = payload.get("event")
        if event == "planned":
            print(
                "[{}] planned records_seen={} candidates={} skipped_ready={} families={}".format(
                    stamp,
                    payload.get("records_seen", 0),
                    payload.get("candidate_count", 0),
                    payload.get("skipped_ready", 0),
                    ",".join(payload.get("families") or []),
                ),
                flush=True,
            )
            return
        if event == "item":
            print(
                (
                    "[{}] {}/{} ready={} failed={} missing={} raw_failed={} "
                    "skipped_ready={} status={} product={}"
                ).format(
                    stamp,
                    payload.get("processed", 0),
                    payload.get("total", 0),
                    payload.get("ready", 0),
                    payload.get("failed", 0),
                    payload.get("missing_source", 0),
                    payload.get("raw_failed", 0),
                    payload.get("skipped_ready", 0),
                    payload.get("status") or "",
                    payload.get("product_name") or "",
                ),
                flush=True,
            )
            return
        if event == "completed":
            print(
                "[{}] completed ready={} cached={} skipped_ready={} failed={} missing={}".format(
                    stamp,
                    payload.get("ready", 0),
                    payload.get("cached", 0),
                    payload.get("skipped_ready", 0),
                    payload.get("failed", 0),
                    payload.get("missing_source", 0),
                ),
                flush=True,
            )

    summary = await asset_inventory_service.build_archive_preview_caches(
        families=families,
        limit=args.limit,
        force=args.force,
        apply=bool(args.apply),
        progress_start=0,
        progress_end=100,
        progress_callback=_print_progress if args.progress else None,
        progress_interval=args.progress_interval,
    )
    return {"apply": bool(args.apply), **summary}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build LT1/Sentinel-1 archive preview WebP caches.")
    parser.add_argument("--apply", action="store_true", help="write preview cache files and update database rows")
    parser.add_argument("--family", action="append", help="family to process: LT1, S1, or comma-separated values")
    parser.add_argument("--limit", type=int, default=0, help="maximum rows to build; 0 means all pending rows")
    parser.add_argument("--force", action="store_true", help="rebuild even if preview_cache_status is READY")
    parser.add_argument("--progress", action="store_true", help="print command-line progress while building")
    parser.add_argument("--progress-interval", type=int, default=10, help="print every N processed candidates")
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
