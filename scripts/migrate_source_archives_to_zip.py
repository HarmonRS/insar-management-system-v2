"""Migrate LT1/Sentinel-1 source records from unpacked pools to ZIP archives.

Default mode is a dry run. Use --apply to sync configured roots and scan ZIP
source pools. Use --quarantine-old-pools only after the database and preview
checks pass.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import database  # noqa: E402
from backend.app.config import settings, split_env_paths  # noqa: E402
from backend.app.models import ManagedRootORM, RadarDataORM, SourceProductAssetORM  # noqa: E402
from backend.app.services.asset_inventory_service import asset_inventory_service  # noqa: E402
from backend.app.services.data_service import DataService  # noqa: E402
from backend.app.services.image_service import image_service  # noqa: E402
from backend.app.services.root_registry_service import root_registry_service  # noqa: E402


OLD_LT1_POOL = r"D:\LuTan1_Image_Pool"
OLD_S1_POOL = r"D:\Sentinel1_Image_Pool"


def _norm(path: str | Path) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text))


def _normcase(path: str | Path) -> str:
    return os.path.normcase(_norm(path))


def _path_under(path: str | Path, root: str | Path) -> bool:
    path_norm = _normcase(path)
    root_norm = _normcase(root)
    return path_norm == root_norm or path_norm.startswith(root_norm + os.sep)


def _human_bytes(value: int | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def _measure_top_level(path: str) -> Dict[str, Any]:
    root = Path(path)
    if not root.exists():
        return {
            "path": path,
            "exists": False,
            "top_level_items": 0,
            "sample": [],
        }
    sample = []
    top_level_items = 0
    try:
        for item in root.iterdir():
            top_level_items += 1
            if len(sample) < 10:
                sample.append(str(item))
    except OSError as exc:
        return {
            "path": path,
            "exists": True,
            "error": str(exc),
            "top_level_items": top_level_items,
            "sample": sample,
        }
    return {
        "path": path,
        "exists": True,
        "top_level_items": top_level_items,
        "sample": sample,
    }


async def _count_scalar(db: AsyncSession, stmt) -> int:
    return int((await db.execute(stmt)).scalar_one() or 0)


def _path_prefix_clauses(column: Any, roots: Sequence[str]) -> List[Any]:
    clauses: List[Any] = []
    for root in roots:
        text = str(root or "").strip()
        if not text:
            continue
        clauses.append(
            and_(
                func.lower(func.substr(column, 1, len(text))) == text.lower(),
                or_(
                    func.length(column) == len(text),
                    func.substr(column, len(text) + 1, 1).in_(["\\", "/"]),
                ),
            )
        )
    return clauses


def _path_prefix_filter(column: Any, roots: Sequence[str]) -> Any:
    clauses = _path_prefix_clauses(column, roots)
    if not clauses:
        return None
    return or_(*clauses)


async def _db_summary(db: AsyncSession, old_pools: Sequence[str], zip_roots: Sequence[str]) -> Dict[str, Any]:
    old_filter = _path_prefix_filter(RadarDataORM.file_path, old_pools)
    zip_filter = _path_prefix_filter(RadarDataORM.file_path, zip_roots)

    radar_by_family = {}
    family_rows = await db.execute(
        select(RadarDataORM.satellite_family, func.count(RadarDataORM.id))
        .where(RadarDataORM.satellite_family.in_(["LT1", "S1"]))
        .group_by(RadarDataORM.satellite_family)
    )
    for family, count in family_rows.all():
        radar_by_family[str(family or "")] = int(count or 0)

    radar_by_source_format = {}
    format_rows = await db.execute(
        select(RadarDataORM.source_format, func.count(RadarDataORM.id))
        .where(RadarDataORM.satellite_family.in_(["LT1", "S1"]))
        .group_by(RadarDataORM.source_format)
    )
    for source_format, count in format_rows.all():
        radar_by_source_format[str(source_format or "NULL")] = int(count or 0)

    assets_by_format = {}
    asset_rows = await db.execute(
        select(SourceProductAssetORM.source_format, func.count(SourceProductAssetORM.id))
        .where(SourceProductAssetORM.satellite_family.in_(["LT1", "S1"]))
        .group_by(SourceProductAssetORM.source_format)
    )
    for source_format, count in asset_rows.all():
        assets_by_format[str(source_format or "NULL")] = int(count or 0)

    preview_rows = await db.execute(
        select(
            func.count(RadarDataORM.id),
            func.sum(case((RadarDataORM.preview_cache_status == "READY", 1), else_=0)),
            func.sum(case((RadarDataORM.preview_cache_path.is_not(None), 1), else_=0)),
        ).where(RadarDataORM.satellite_family.in_(["LT1", "S1"]))
    )
    total_previews, ready_previews, path_previews = preview_rows.one()

    summary: Dict[str, Any] = {
        "radar_by_family": radar_by_family,
        "radar_by_source_format": radar_by_source_format,
        "source_assets_by_format": assets_by_format,
        "preview_rows": {
            "total": int(total_previews or 0),
            "ready": int(ready_previews or 0),
            "has_cache_path": int(path_previews or 0),
        },
    }
    if old_filter is not None:
        summary["radar_file_paths_under_old_pools"] = await _count_scalar(
            db,
            select(func.count(RadarDataORM.id)).where(old_filter),
        )
    if zip_filter is not None:
        summary["radar_file_paths_under_zip_roots"] = await _count_scalar(
            db,
            select(func.count(RadarDataORM.id)).where(zip_filter),
        )
    return summary


async def _archive_migration_gap_summary(db: AsyncSession, old_pools: Sequence[str]) -> Dict[str, Any]:
    old_filter = _path_prefix_filter(RadarDataORM.file_path, old_pools)
    if old_filter is None:
        return {"old_pool_records": 0}

    def _archive_exists(family: str, source_format: str) -> Any:
        return exists(
            select(SourceProductAssetORM.id).where(
                SourceProductAssetORM.satellite_family == family,
                SourceProductAssetORM.source_format == source_format,
                SourceProductAssetORM.logical_product_uid == RadarDataORM.product_unique_id,
                SourceProductAssetORM.is_active == True,  # noqa: E712
            )
        )

    lt1_archive_exists = _archive_exists("LT1", "LT1_ARCHIVE")
    s1_archive_exists = _archive_exists("S1", "S1_ZIP")
    base_filter = and_(
        old_filter,
        RadarDataORM.satellite_family.in_(["LT1", "S1"]),
        RadarDataORM.source_format.in_(["LT1_DIR", "S1_SAFE_DIR"]),
    )
    migrateable_filter = and_(
        base_filter,
        or_(
            and_(RadarDataORM.satellite_family == "LT1", lt1_archive_exists),
            and_(RadarDataORM.satellite_family == "S1", s1_archive_exists),
        ),
    )
    missing_filter = and_(
        base_filter,
        or_(
            and_(RadarDataORM.satellite_family == "LT1", ~lt1_archive_exists),
            and_(RadarDataORM.satellite_family == "S1", ~s1_archive_exists),
        ),
    )

    samples = await db.execute(
        select(
            RadarDataORM.id,
            RadarDataORM.satellite_family,
            RadarDataORM.product_unique_id,
            RadarDataORM.file_path,
        )
        .where(missing_filter)
        .order_by(RadarDataORM.id.asc())
        .limit(10)
    )
    return {
        "old_pool_records": await _count_scalar(db, select(func.count(RadarDataORM.id)).where(base_filter)),
        "migrateable_with_archive_asset": await _count_scalar(db, select(func.count(RadarDataORM.id)).where(migrateable_filter)),
        "missing_archive_asset": await _count_scalar(db, select(func.count(RadarDataORM.id)).where(missing_filter)),
        "missing_archive_samples": [
            {
                "radar_id": radar_id,
                "family": family,
                "product_unique_id": product_unique_id,
                "file_path": file_path,
            }
            for radar_id, family, product_unique_id, file_path in samples.all()
        ],
    }


async def _list_zip_roots(db: AsyncSession) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(ManagedRootORM)
        .where(ManagedRootORM.root_role == "source_product_pool")
        .order_by(ManagedRootORM.id.asc())
    )
    roots = []
    for root in result.scalars().all():
        roots.append(
            {
                "id": root.id,
                "path": root.path,
                "enabled": bool(root.enabled),
                "exists": bool(root.exists_flag),
                "source_ref": root.source_ref,
            }
        )
    return roots


async def _target_scan_root_ids(db: AsyncSession, *, bind_orbits: bool) -> List[int]:
    source_result = await db.execute(
        select(ManagedRootORM.id).where(
            ManagedRootORM.enabled == True,  # noqa: E712
            ManagedRootORM.root_role == "source_product_pool",
            ManagedRootORM.source_ref.like("SOURCE_PRODUCT_DIRS%"),
        )
    )
    root_ids = [int(item) for item in source_result.scalars().all()]
    if bind_orbits:
        orbit_result = await db.execute(
            select(ManagedRootORM.id).where(
                ManagedRootORM.enabled == True,  # noqa: E712
                ManagedRootORM.root_role == "orbit_asset_pool",
            )
        )
        root_ids.extend(int(item) for item in orbit_result.scalars().all())
    return root_ids


async def _sample_preview_archive_sources(db: AsyncSession, limit: int) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(RadarDataORM)
        .where(RadarDataORM.satellite_family.in_(["LT1", "S1"]))
        .where(RadarDataORM.source_format.in_(["LT1_ARCHIVE", "S1_ZIP"]))
        .order_by(RadarDataORM.id.asc())
        .limit(max(0, int(limit)))
    )
    samples: List[Dict[str, Any]] = []
    for record in result.scalars().all():
        preview_source = DataService.find_radar_preview_source(record.file_path)
        geo_path = DataService.get_radar_geo_cache_path(record.unique_id or record.file_path, record.file_path)
        samples.append(
            {
                "radar_id": record.id,
                "family": record.satellite_family,
                "source_format": record.source_format,
                "file_path": record.file_path,
                "preview_source_found": bool(preview_source),
                "preview_source": preview_source,
                "db_preview_ready": (record.preview_cache_status or "") == "READY",
                "db_preview_cache_exists": bool(record.preview_cache_path and os.path.exists(record.preview_cache_path)),
                "expected_geo_cache_exists": os.path.exists(geo_path),
            }
        )
    return samples


async def _build_archive_previews(
    db: AsyncSession,
    *,
    limit: int,
    force: bool,
) -> Dict[str, Any]:
    stmt = (
        select(RadarDataORM)
        .where(RadarDataORM.satellite_family.in_(["LT1", "S1"]))
        .where(RadarDataORM.source_format.in_(["LT1_ARCHIVE", "S1_ZIP"]))
        .order_by(RadarDataORM.id.asc())
    )
    if not force:
        stmt = stmt.where(
            or_(
                RadarDataORM.preview_cache_status != "READY",
                RadarDataORM.preview_cache_status.is_(None),
                RadarDataORM.preview_cache_path.is_(None),
            )
        )
    if limit > 0:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    records = result.scalars().all()
    summary = {
        "candidate_count": len(records),
        "ready": 0,
        "failed": 0,
        "missing_source": 0,
        "skipped_invalid_geometry": 0,
        "items": [],
    }
    thumb_size = (settings.RADAR_THUMBNAIL_MAX_SIZE, settings.RADAR_THUMBNAIL_MAX_SIZE)
    for record in records:
        unique_id = record.unique_id or record.file_path
        raw_cache_path = DataService.get_radar_raw_cache_path(unique_id, record.file_path)
        geo_cache_path = DataService.get_radar_geo_cache_path(unique_id, record.file_path)
        preview_source = DataService.find_radar_preview_source(record.file_path)
        item: Dict[str, Any] = {
            "radar_id": record.id,
            "family": record.satellite_family,
            "source_format": record.source_format,
            "file_path": record.file_path,
            "preview_source": preview_source,
        }
        if not preview_source:
            record.preview_cache_status = "NONE"
            record.preview_cache_path = None
            record.preview_cache_version = settings.RADAR_GEO_CACHE_VERSION
            record.preview_cache_updated_at = datetime.utcnow()
            record.preview_cache_error = "preview_source_not_found"
            db.add(record)
            summary["missing_source"] += 1
            item["status"] = "missing_source"
            summary["items"].append(item)
            continue

        coverage_polygon = DataService._normalize_coverage_polygon(record.coverage_polygon)
        try:
            bbox = (
                float(record.min_lon),
                float(record.min_lat),
                float(record.max_lon),
                float(record.max_lat),
            )
        except (TypeError, ValueError):
            bbox = None
        if not coverage_polygon or not bbox:
            record.preview_cache_status = "FAILED"
            record.preview_cache_path = None
            record.preview_cache_version = settings.RADAR_GEO_CACHE_VERSION
            record.preview_cache_updated_at = datetime.utcnow()
            record.preview_cache_error = "invalid_coverage_polygon" if not coverage_polygon else "invalid_bbox"
            db.add(record)
            summary["skipped_invalid_geometry"] += 1
            item["status"] = record.preview_cache_error
            summary["items"].append(item)
            continue

        source_corner_mapping = DataService.get_radar_source_corner_mapping(record.file_path)
        ok_geo, geo_error = image_service.create_geocorrected_radar_cached_image(
            preview_source,
            geo_cache_path,
            coverage_polygon,
            bbox,
            source_corner_mapping,
            thumb_size,
            settings.RADAR_GEO_CACHE_QUALITY,
        )
        ok_raw = image_service.create_radar_cached_image(preview_source, raw_cache_path, thumb_size)
        if ok_geo and os.path.exists(geo_cache_path):
            record.preview_cache_status = "READY"
            record.preview_cache_path = geo_cache_path
            record.preview_cache_error = None
            summary["ready"] += 1
            item["status"] = "ready"
            item["geo_cache_path"] = geo_cache_path
        else:
            record.preview_cache_status = "FAILED"
            record.preview_cache_path = None
            record.preview_cache_error = geo_error or ("raw_cache_ready_only" if ok_raw else "preview_cache_build_failed")
            summary["failed"] += 1
            item["status"] = "failed"
            item["error"] = record.preview_cache_error
        record.preview_cache_version = settings.RADAR_GEO_CACHE_VERSION
        record.preview_cache_updated_at = datetime.utcnow()
        db.add(record)
        summary["items"].append(item)
    await db.commit()
    return summary


async def _count_duplicate_products(db: AsyncSession) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(
            RadarDataORM.satellite_family,
            RadarDataORM.product_unique_id,
            func.count(RadarDataORM.id).label("count"),
        )
        .where(RadarDataORM.satellite_family.in_(["LT1", "S1"]))
        .where(RadarDataORM.product_unique_id.is_not(None))
        .group_by(RadarDataORM.satellite_family, RadarDataORM.product_unique_id)
        .having(func.count(RadarDataORM.id) > 1)
        .order_by(func.count(RadarDataORM.id).desc())
        .limit(50)
    )
    return [
        {
            "family": family,
            "product_unique_id": product_unique_id,
            "count": int(count or 0),
        }
        for family, product_unique_id, count in result.all()
    ]


async def _mark_old_assets_inactive(db: AsyncSession, old_pools: Sequence[str]) -> Dict[str, int]:
    if not old_pools:
        return {"source_assets": 0}
    old_filter = _path_prefix_filter(SourceProductAssetORM.file_path, old_pools)
    if old_filter is None:
        return {"source_assets": 0}
    result = await db.execute(
        update(SourceProductAssetORM)
        .where(old_filter)
        .where(SourceProductAssetORM.source_format.in_(["LT1_DIR", "S1_SAFE_DIR"]))
        .values(is_active=False, missing_since=datetime.utcnow())
        .execution_options(synchronize_session=False)
    )
    return {"source_assets": int(result.rowcount or 0)}


def _quarantine_old_pools(old_pools: Iterable[str], suffix: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for raw_path in old_pools:
        path = _norm(raw_path)
        if not path:
            continue
        target = f"{path}.{suffix}"
        result: Dict[str, Any] = {
            "source": path,
            "target": target,
            "source_exists": os.path.exists(path),
            "target_exists": os.path.exists(target),
        }
        if not os.path.exists(path):
            results.append(result)
            continue
        if os.path.exists(target):
            result["status"] = "skipped_target_exists"
            results.append(result)
            continue
        if Path(path).anchor == path:
            raise ValueError(f"Refusing to quarantine drive root: {path}")
        shutil.move(path, target)
        result["status"] = "moved"
        results.append(result)
    return results


async def _run(args: argparse.Namespace) -> Dict[str, Any]:
    if database.AsyncSessionLocal is None:
        database.init_db(settings.DATABASE_URL)
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized")

    old_pools = [_norm(item) for item in (args.old_pool or []) if str(item or "").strip()]
    zip_roots = [_norm(item) for item in split_env_paths(settings.SOURCE_PRODUCT_DIRS)]

    async with database.AsyncSessionLocal() as db:
        before = await _db_summary(db, old_pools, zip_roots)
        result: Dict[str, Any] = {
            "apply": bool(args.apply),
            "old_pools": [_measure_top_level(path) for path in old_pools],
            "source_product_dirs": zip_roots,
            "before": before,
            "archive_migration_gap_before": await _archive_migration_gap_summary(db, old_pools),
        }

        if args.apply:
            result["root_registry_sync"] = await root_registry_service.sync_from_settings(db)
            if args.scan_archives:
                root_ids = await _target_scan_root_ids(db, bind_orbits=bool(args.bind_orbits))
                result["asset_inventory_scan"] = await asset_inventory_service.scan_configured_roots(
                    db,
                    inventory_types=["source_product", "orbit_asset"] if args.bind_orbits else ["source_product"],
                    root_ids=root_ids,
                    bind_orbits=bool(args.bind_orbits),
                )
            if args.build_archive_previews:
                result["archive_preview_build"] = await _build_archive_previews(
                    db,
                    limit=max(0, int(args.preview_build_limit)),
                    force=bool(args.force_preview_rebuild),
                )
            if args.mark_old_assets_inactive:
                result["marked_old_assets_inactive"] = await _mark_old_assets_inactive(db, old_pools)
                await db.commit()
        else:
            result["planned_actions"] = [
                "sync managed roots from .env",
                "scan SOURCE_PRODUCT_DIRS for LT1_ARCHIVE/S1_ZIP",
                "upsert radar_data by logical product id",
            ]
            if args.mark_old_assets_inactive:
                result["planned_actions"].append("mark old LT1_DIR/S1_SAFE_DIR source assets inactive")

        result["roots"] = await _list_zip_roots(db)
        result["after"] = await _db_summary(db, old_pools, zip_roots)
        result["archive_migration_gap_after"] = await _archive_migration_gap_summary(db, old_pools)
        result["duplicate_products"] = await _count_duplicate_products(db)
        if args.preview_sample > 0:
            result["preview_archive_samples"] = await _sample_preview_archive_sources(db, args.preview_sample)

        if args.quarantine_old_pools:
            if not args.apply:
                result["quarantine"] = {"skipped": True, "reason": "requires --apply"}
            elif result["after"].get("radar_file_paths_under_old_pools", 0) > 0:
                result["quarantine"] = {
                    "skipped": True,
                    "reason": "database still has radar_data.file_path under old pools",
                }
            else:
                result["quarantine"] = _quarantine_old_pools(old_pools, args.quarantine_suffix)

        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write database changes and run scans")
    parser.add_argument("--scan-archives", action="store_true", default=True, help="scan configured source product roots")
    parser.add_argument("--no-scan-archives", action="store_false", dest="scan_archives")
    parser.add_argument("--bind-orbits", action="store_true", default=True, help="bind scenes to orbit assets after scan")
    parser.add_argument("--no-bind-orbits", action="store_false", dest="bind_orbits")
    parser.add_argument("--mark-old-assets-inactive", action="store_true", help="mark old LT1_DIR/S1_SAFE_DIR assets inactive")
    parser.add_argument("--preview-sample", type=int, default=5, help="try archive preview extraction on N migrated records")
    parser.add_argument("--build-archive-previews", action="store_true", help="build WebP preview caches for LT1_ARCHIVE/S1_ZIP records")
    parser.add_argument("--preview-build-limit", type=int, default=0, help="limit preview cache builds; 0 means no limit")
    parser.add_argument("--force-preview-rebuild", action="store_true", help="rebuild archive preview caches even if READY")
    parser.add_argument("--quarantine-old-pools", action="store_true", help="rename old unpacked pools after migration checks pass")
    parser.add_argument("--quarantine-suffix", default="__quarantine_20260616", help="suffix appended to old pool directory names")
    parser.add_argument("--old-pool", action="append", default=[OLD_LT1_POOL, OLD_S1_POOL], help="old unpacked source pool path")
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
