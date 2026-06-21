"""Reset LT1/Sentinel-1 source catalog rows and radar preview caches.

This maintenance script is intentionally scoped to source-scene registration.
It does not delete source archives, unpacked source directories, orbit assets,
D-InSAR/SBAS/GF3 product catalogs, task logs, users, or flood products.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from sqlalchemy import delete, func, or_, select, update


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import database  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.models import (  # noqa: E402
    AssetInventoryIssueORM,
    AssetInventoryStateORM,
    PairingCacheStateORM,
    PairingDirtySceneORM,
    PairingMetricCacheORM,
    PairingNetworkEdgeORM,
    PairingNetworkRunORM,
    RadarDataORM,
    SARSceneGeoORM,
    SceneOrbitBindingORM,
    ScanStateORM,
    SourceProductAssetORM,
    TimeseriesStackPlanEdgeORM,
    TimeseriesStackPlanItemORM,
)


TARGET_FAMILIES = ("LT1", "S1")
TARGET_SOURCE_FORMATS = ("LT1_DIR", "LT1_ARCHIVE", "S1_SAFE_DIR", "S1_ZIP")
RADAR_CACHE_DIRS = (
    settings.RADAR_RAW_CACHE_DIR,
    settings.RADAR_GEO_CACHE_DIR,
    os.path.join(settings.CACHE_DIR, "radar_archive_preview_sources"),
)


def _target_radar_filter() -> Any:
    return or_(
        RadarDataORM.satellite_family.in_(TARGET_FAMILIES),
        RadarDataORM.source_format.in_(TARGET_SOURCE_FORMATS),
    )


def _target_asset_filter() -> Any:
    return or_(
        SourceProductAssetORM.satellite_family.in_(TARGET_FAMILIES),
        SourceProductAssetORM.source_format.in_(TARGET_SOURCE_FORMATS),
    )


async def _count(db, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one() or 0)


def _cache_dir_summary(paths: Iterable[str]) -> List[Dict[str, Any]]:
    payload = []
    for raw_path in paths:
        path = Path(raw_path)
        files = 0
        bytes_total = 0
        if path.exists():
            for item in path.rglob("*"):
                if not item.is_file():
                    continue
                try:
                    files += 1
                    bytes_total += item.stat().st_size
                except OSError:
                    continue
        payload.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "files": files,
                "bytes": bytes_total,
            }
        )
    return payload


def _clear_cache_dirs(paths: Iterable[str]) -> List[Dict[str, Any]]:
    results = []
    for raw_path in paths:
        path = Path(raw_path)
        before = _cache_dir_summary([str(path)])[0]
        if path.exists():
            for item in path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        path.mkdir(parents=True, exist_ok=True)
        after = _cache_dir_summary([str(path)])[0]
        results.append({"before": before, "after": after})
    return results


async def _summary(db) -> Dict[str, Any]:
    radar_filter = _target_radar_filter()
    asset_filter = _target_asset_filter()
    radar_ids = select(RadarDataORM.id).where(radar_filter)
    asset_ids = select(SourceProductAssetORM.id).where(asset_filter)

    by_source_format = {}
    radar_format_rows = await db.execute(
        select(RadarDataORM.source_format, func.count(RadarDataORM.id))
        .where(radar_filter)
        .group_by(RadarDataORM.source_format)
        .order_by(RadarDataORM.source_format.asc())
    )
    for source_format, count in radar_format_rows.all():
        by_source_format[str(source_format or "NULL")] = int(count or 0)

    asset_by_source_format = {}
    asset_format_rows = await db.execute(
        select(SourceProductAssetORM.source_format, func.count(SourceProductAssetORM.id))
        .where(asset_filter)
        .group_by(SourceProductAssetORM.source_format)
        .order_by(SourceProductAssetORM.source_format.asc())
    )
    for source_format, count in asset_format_rows.all():
        asset_by_source_format[str(source_format or "NULL")] = int(count or 0)

    return {
        "radar_data_count": await _count(db, select(func.count(RadarDataORM.id)).where(radar_filter)),
        "radar_data_by_source_format": by_source_format,
        "source_product_asset_count": await _count(db, select(func.count(SourceProductAssetORM.id)).where(asset_filter)),
        "source_product_assets_by_source_format": asset_by_source_format,
        "scene_orbit_binding_count": await _count(
            db,
            select(func.count(SceneOrbitBindingORM.id)).where(SceneOrbitBindingORM.radar_data_id.in_(radar_ids)),
        ),
        "asset_inventory_issue_count": await _count(
            db,
            select(func.count(AssetInventoryIssueORM.id)).where(
                or_(
                    AssetInventoryIssueORM.radar_data_id.in_(radar_ids),
                    AssetInventoryIssueORM.asset_ref_id.in_(asset_ids),
                )
            ),
        ),
        "sar_scene_geo_blocker_count": await _count(
            db,
            select(func.count(SARSceneGeoORM.id)).where(SARSceneGeoORM.radar_data_id.in_(radar_ids)),
        ),
        "timeseries_plan_item_refs": await _count(
            db,
            select(func.count(TimeseriesStackPlanItemORM.id)).where(
                TimeseriesStackPlanItemORM.radar_data_ref_id.in_(radar_ids)
            ),
        ),
        "timeseries_plan_edge_refs": await _count(
            db,
            select(func.count(TimeseriesStackPlanEdgeORM.id)).where(
                or_(
                    TimeseriesStackPlanEdgeORM.master_scene_ref_id.in_(radar_ids),
                    TimeseriesStackPlanEdgeORM.slave_scene_ref_id.in_(radar_ids),
                )
            ),
        ),
        "pairing_metric_cache_count": await _count(db, select(func.count(PairingMetricCacheORM.id))),
        "pairing_dirty_scene_count": await _count(db, select(func.count(PairingDirtySceneORM.id))),
        "pairing_network_run_count": await _count(db, select(func.count(PairingNetworkRunORM.id))),
        "pairing_network_edge_count": await _count(db, select(func.count(PairingNetworkEdgeORM.id))),
        "radar_cache_dirs": _cache_dir_summary(RADAR_CACHE_DIRS),
    }


async def _apply_reset(db, *, allow_sar_scene_geo: bool) -> Dict[str, Any]:
    radar_filter = _target_radar_filter()
    asset_filter = _target_asset_filter()
    radar_ids = select(RadarDataORM.id).where(radar_filter)
    asset_ids = select(SourceProductAssetORM.id).where(asset_filter)

    sar_blockers = await _count(
        db,
        select(func.count(SARSceneGeoORM.id)).where(SARSceneGeoORM.radar_data_id.in_(radar_ids)),
    )
    if sar_blockers and not allow_sar_scene_geo:
        raise RuntimeError(
            f"Refusing to delete radar_data: {sar_blockers} SAR analysis rows reference LT1/S1 scenes. "
            "Re-run with --allow-sar-scene-geo only if you intend to clear those analysis rows."
        )

    counts: Dict[str, int] = {}

    if allow_sar_scene_geo:
        result = await db.execute(delete(SARSceneGeoORM).where(SARSceneGeoORM.radar_data_id.in_(radar_ids)))
        counts["sar_scene_geo_deleted"] = int(result.rowcount or 0)

    result = await db.execute(delete(SceneOrbitBindingORM).where(SceneOrbitBindingORM.radar_data_id.in_(radar_ids)))
    counts["scene_orbit_bindings_deleted"] = int(result.rowcount or 0)

    result = await db.execute(
        delete(AssetInventoryIssueORM).where(
            or_(
                AssetInventoryIssueORM.radar_data_id.in_(radar_ids),
                AssetInventoryIssueORM.asset_ref_id.in_(asset_ids),
            )
        )
    )
    counts["asset_inventory_issues_deleted"] = int(result.rowcount or 0)

    await db.execute(
        update(TimeseriesStackPlanItemORM)
        .where(TimeseriesStackPlanItemORM.radar_data_ref_id.in_(radar_ids))
        .values(radar_data_ref_id=None)
    )
    await db.execute(
        update(TimeseriesStackPlanEdgeORM)
        .where(
            or_(
                TimeseriesStackPlanEdgeORM.master_scene_ref_id.in_(radar_ids),
                TimeseriesStackPlanEdgeORM.slave_scene_ref_id.in_(radar_ids),
            )
        )
        .values(master_scene_ref_id=None, slave_scene_ref_id=None, metric_cache_ref_id=None)
    )

    result = await db.execute(delete(PairingNetworkEdgeORM))
    counts["pairing_network_edges_deleted"] = int(result.rowcount or 0)
    result = await db.execute(delete(PairingNetworkRunORM))
    counts["pairing_network_runs_deleted"] = int(result.rowcount or 0)
    result = await db.execute(delete(PairingMetricCacheORM))
    counts["pairing_metric_cache_deleted"] = int(result.rowcount or 0)
    result = await db.execute(delete(PairingDirtySceneORM))
    counts["pairing_dirty_scenes_deleted"] = int(result.rowcount or 0)

    result = await db.execute(delete(RadarDataORM).where(radar_filter))
    counts["radar_data_deleted"] = int(result.rowcount or 0)

    result = await db.execute(delete(SourceProductAssetORM).where(asset_filter))
    counts["source_product_assets_deleted"] = int(result.rowcount or 0)

    await db.execute(delete(ScanStateORM).where(ScanStateORM.data_type == "radar"))
    await db.execute(
        update(AssetInventoryStateORM)
        .where(AssetInventoryStateORM.inventory_type == "source_product")
        .values(
            status="NEVER_SCANNED",
            last_scan_started_at=None,
            last_scan_finished_at=None,
            last_seen_entry_count=None,
            last_asset_count=None,
            last_issue_count=None,
            needs_rescan=True,
            last_error=None,
            updated_at=datetime.utcnow(),
        )
    )
    await db.execute(
        update(PairingCacheStateORM).values(
            status="READY",
            scene_count=0,
            pair_count=0,
            dirty_scene_count=0,
            last_error=None,
            updated_at=datetime.utcnow(),
        )
    )

    await db.commit()
    return counts


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    if database.AsyncSessionLocal is None:
        database.init_db(settings.DATABASE_URL)
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized")

    async with database.AsyncSessionLocal() as db:
        before = await _summary(db)
        payload: Dict[str, Any] = {
            "apply": bool(args.apply),
            "before": before,
            "scope": {
                "families": TARGET_FAMILIES,
                "source_formats": TARGET_SOURCE_FORMATS,
                "clears_preview_cache": bool(args.clear_preview_cache),
            },
        }
        if args.apply:
            payload["database_changes"] = await _apply_reset(db, allow_sar_scene_geo=bool(args.allow_sar_scene_geo))
            if args.clear_preview_cache:
                payload["preview_cache_changes"] = _clear_cache_dirs(RADAR_CACHE_DIRS)
        else:
            payload["planned_actions"] = [
                "delete LT1/S1 radar_data rows",
                "delete LT1/S1 source_product_assets rows",
                "delete source inventory issues and scene-orbit bindings for those rows",
                "clear pairing cache/network cache",
                "reset source inventory scan state",
                "clear radar preview cache directories" if args.clear_preview_cache else "keep radar preview cache directories",
            ]
        payload["after"] = await _summary(db)
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the reset; default only prints a dry-run plan")
    parser.add_argument("--clear-preview-cache", action="store_true", default=True, help="clear radar preview cache dirs")
    parser.add_argument("--keep-preview-cache", action="store_false", dest="clear_preview_cache")
    parser.add_argument("--allow-sar-scene-geo", action="store_true", help="also delete SAR analysis scene rows if they block reset")
    args = parser.parse_args()

    import json

    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
