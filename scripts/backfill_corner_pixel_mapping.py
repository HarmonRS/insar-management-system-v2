"""Backfill XML refRow/refColumn corner mappings for archive-managed radar records."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geoalchemy2.shape import from_shape  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from sqlalchemy import or_, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from backend.app import database  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.models import RadarDataORM, SourceMetadataDocumentORM, SourceProductAssetORM  # noqa: E402
from backend.app.services.asset_inventory_service import _parse_radar_xml_metadata_bytes  # noqa: E402
from backend.app.utils import build_corner_pixel_mapping  # noqa: E402


SOURCE_FORMATS = {"LT1_ARCHIVE"}
DOCUMENT_TYPES = {"LT1_META"}


def _metadata_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _normalize_families(values: Optional[Iterable[str]]) -> Sequence[str]:
    families = []
    for value in values or ["LT1"]:
        for item in str(value or "").split(","):
            family = item.strip().upper()
            if family and family not in families:
                families.append(family)
    supported = {"LT1"}
    invalid = [item for item in families if item not in supported]
    if invalid:
        raise SystemExit(f"Unsupported family: {', '.join(invalid)}")
    return families or ["LT1"]


def _decode_document(doc: SourceMetadataDocumentORM) -> Optional[bytes]:
    payload = bytes(doc.content_bytes or b"")
    if not payload:
        return None
    if str(doc.content_encoding or "").lower() == "gzip":
        return gzip.decompress(payload)
    return payload


def _mapping_from_xml_document(doc: SourceMetadataDocumentORM) -> Tuple[Optional[Dict[str, Any]], Optional[Any]]:
    payload = _decode_document(doc)
    if not payload:
        return None, None
    coverage_polygon, xml_meta = _parse_radar_xml_metadata_bytes(payload)
    mapping = xml_meta.get("corner_pixel_mapping") if isinstance(xml_meta, dict) else None
    return mapping if isinstance(mapping, dict) else None, coverage_polygon


def _mapping_from_metadata(metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    existing = metadata.get("corner_pixel_mapping")
    if isinstance(existing, dict):
        return existing
    corners = metadata.get("corner_details")
    if isinstance(corners, dict):
        mapping = build_corner_pixel_mapping(corners)
        if isinstance(mapping, dict):
            return mapping
    return None


def _bbox(points: Any) -> Optional[Tuple[float, float, float, float]]:
    try:
        ring = [(float(item[0]), float(item[1])) for item in points or []]
    except (TypeError, ValueError, IndexError):
        return None
    if len(ring) < 3:
        return None
    lons = [item[0] for item in ring]
    lats = [item[1] for item in ring]
    return min(lons), min(lats), max(lons), max(lats)


async def _first_metadata_document(
    db: AsyncSession,
    *,
    source_asset_id: Optional[int],
    radar_data_id: Optional[int],
) -> Optional[SourceMetadataDocumentORM]:
    clauses = []
    if source_asset_id:
        clauses.append(SourceMetadataDocumentORM.source_asset_id == int(source_asset_id))
    if radar_data_id:
        clauses.append(SourceMetadataDocumentORM.radar_data_id == int(radar_data_id))
    if not clauses:
        return None
    stmt = (
        select(SourceMetadataDocumentORM)
        .where(or_(*clauses))
        .where(SourceMetadataDocumentORM.document_type.in_(sorted(DOCUMENT_TYPES)))
        .order_by(SourceMetadataDocumentORM.id.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_source_assets(db: AsyncSession, families: Sequence[str], limit: int) -> Sequence[SourceProductAssetORM]:
    stmt = (
        select(SourceProductAssetORM)
        .where(SourceProductAssetORM.satellite_family.in_(families))
        .where(SourceProductAssetORM.source_format.in_(sorted(SOURCE_FORMATS)))
        .where(SourceProductAssetORM.is_active.is_(True))
        .order_by(SourceProductAssetORM.id.asc())
    )
    if limit > 0:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def _load_radar_records(db: AsyncSession, families: Sequence[str], limit: int) -> Sequence[RadarDataORM]:
    stmt = (
        select(RadarDataORM)
        .where(RadarDataORM.satellite_family.in_(families))
        .where(RadarDataORM.source_format.in_(sorted(SOURCE_FORMATS)))
        .order_by(RadarDataORM.id.asc())
    )
    if limit > 0:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def backfill(args: argparse.Namespace) -> Dict[str, int]:
    families = _normalize_families(args.family)
    database.init_db(settings.DATABASE_URL)
    stats = {
        "source_seen": 0,
        "source_updated": 0,
        "source_existing": 0,
        "source_missing_mapping": 0,
        "radar_seen": 0,
        "radar_updated": 0,
        "radar_existing": 0,
        "radar_missing_mapping": 0,
        "cache_invalidated": 0,
        "document_parse_failed": 0,
    }

    async with database.AsyncSessionLocal() as db:
        source_assets = await _load_source_assets(db, families, int(args.limit or 0))
        source_mappings: Dict[int, Dict[str, Any]] = {}
        source_polygons: Dict[int, Any] = {}
        now = datetime.utcnow()

        for asset in source_assets:
            stats["source_seen"] += 1
            metadata = _metadata_dict(asset.metadata_json)
            mapping = _mapping_from_metadata(metadata)
            coverage_polygon = metadata.get("coverage_polygon")
            if mapping:
                stats["source_existing"] += 1
            else:
                doc = await _first_metadata_document(db, source_asset_id=asset.id, radar_data_id=None)
                if doc:
                    try:
                        mapping, coverage_polygon_from_doc = _mapping_from_xml_document(doc)
                        coverage_polygon = coverage_polygon_from_doc or coverage_polygon
                    except Exception:
                        stats["document_parse_failed"] += 1
                        mapping = None
                if not mapping:
                    stats["source_missing_mapping"] += 1
                    continue
                metadata["corner_pixel_mapping"] = mapping
                if coverage_polygon:
                    metadata["coverage_polygon"] = coverage_polygon
                metadata["corner_pixel_mapping_backfilled_at"] = now.isoformat()
                asset.metadata_json = metadata
                asset.updated_at = now
                db.add(asset)
                stats["source_updated"] += 1

            if mapping and asset.id is not None:
                source_mappings[int(asset.id)] = mapping
                if coverage_polygon:
                    source_polygons[int(asset.id)] = coverage_polygon

        radar_records = await _load_radar_records(db, families, int(args.limit or 0))
        for radar in radar_records:
            stats["radar_seen"] += 1
            metadata = _metadata_dict(radar.metadata_json)
            mapping = _mapping_from_metadata(metadata)
            coverage_polygon = radar.coverage_polygon or metadata.get("coverage_polygon")
            if mapping:
                stats["radar_existing"] += 1
            else:
                source_id = int(radar.source_product_ref_id or 0)
                mapping = source_mappings.get(source_id)
                if source_id and source_id in source_polygons:
                    coverage_polygon = coverage_polygon or source_polygons[source_id]
                if not mapping:
                    doc = await _first_metadata_document(
                        db,
                        source_asset_id=radar.source_product_ref_id,
                        radar_data_id=radar.id,
                    )
                    if doc:
                        try:
                            mapping, coverage_polygon_from_doc = _mapping_from_xml_document(doc)
                            coverage_polygon = coverage_polygon_from_doc or coverage_polygon
                        except Exception:
                            stats["document_parse_failed"] += 1
                            mapping = None
                if not mapping:
                    stats["radar_missing_mapping"] += 1
                    continue

                metadata["corner_pixel_mapping"] = mapping
                if coverage_polygon:
                    metadata["coverage_polygon"] = coverage_polygon
                    radar.coverage_polygon = coverage_polygon
                    bbox = _bbox(coverage_polygon)
                    if bbox:
                        radar.min_lon, radar.min_lat, radar.max_lon, radar.max_lat = bbox
                    try:
                        polygon = Polygon(coverage_polygon)
                        if not polygon.is_valid:
                            polygon = polygon.buffer(0)
                        if not polygon.is_empty:
                            radar.geom = from_shape(polygon, srid=4326)
                    except Exception:
                        pass
                metadata["corner_pixel_mapping_backfilled_at"] = now.isoformat()
                radar.metadata_json = metadata
                db.add(radar)
                stats["radar_updated"] += 1

            if args.invalidate_preview and mapping:
                radar.preview_cache_status = "NONE"
                radar.preview_cache_version = None
                radar.preview_cache_path = None
                radar.preview_cache_error = "corner_pixel_mapping_backfilled"
                radar.preview_cache_updated_at = now
                db.add(radar)
                stats["cache_invalidated"] += 1

        if args.apply:
            await db.commit()
        else:
            await db.rollback()

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill corner_pixel_mapping into source/radar metadata_json.")
    parser.add_argument("--apply", action="store_true", help="commit database changes")
    parser.add_argument("--family", action="append", help="family to process: LT1 or GF3; defaults to LT1")
    parser.add_argument("--limit", type=int, default=0, help="maximum source/radar rows per table; 0 means all")
    parser.add_argument(
        "--no-invalidate-preview",
        dest="invalidate_preview",
        action="store_false",
        help="do not mark old geocorrected WebP caches stale",
    )
    parser.set_defaults(invalidate_preview=True)
    args = parser.parse_args()
    stats = asyncio.run(backfill(args))
    print(json.dumps({"apply": bool(args.apply), **stats}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
