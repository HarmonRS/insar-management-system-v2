"""Repair LT1 archive metadata after XML imageDataType/product_type mix-up."""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from geoalchemy2.shape import from_shape  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from sqlalchemy import func, or_, select  # noqa: E402

from backend.app import database  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.models import RadarDataORM, SourceProductAssetORM  # noqa: E402
from backend.app.utils import parse_lt1_radar_filename  # noqa: E402


COMPLEX_TOKENS = {"COMPLEX", "SLC", "SSC"}


def _strip_known_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in (".tar.gz", ".tgz", ".zip", ".tar"):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return os.path.splitext(name)[0]


def _scene_name_from_path(path: Optional[str], fallback: Optional[str] = None) -> str:
    name = os.path.basename(str(path or "").strip())
    if not name:
        name = str(fallback or "").strip()
    return _strip_known_suffix(name)


def _metadata_dict(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _ordered_closed_polygon(points: Any) -> Optional[list[tuple[float, float]]]:
    unique: list[tuple[float, float]] = []
    for point in points or []:
        try:
            lon = float(point[0])
            lat = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        current = (lon, lat)
        if unique and abs(unique[-1][0] - lon) < 1e-12 and abs(unique[-1][1] - lat) < 1e-12:
            continue
        if unique and abs(unique[0][0] - lon) < 1e-12 and abs(unique[0][1] - lat) < 1e-12:
            continue
        if current not in unique:
            unique.append(current)
    if len(unique) < 3:
        return None
    if len(unique) == 4:
        center_lon = sum(item[0] for item in unique) / len(unique)
        center_lat = sum(item[1] for item in unique) / len(unique)
        ordered = sorted(
            unique,
            key=lambda item: math.atan2(item[1] - center_lat, item[0] - center_lon),
        )
    else:
        ordered = unique
    if ordered[0] != ordered[-1]:
        ordered.append(ordered[0])
    try:
        polygon = Polygon(ordered)
        if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
            return ordered
    except Exception:
        return None
    return None


def _normalize_lt1_metadata(metadata: Dict[str, Any], parsed: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(metadata)
    previous_product_type = str(updated.get("product_type") or "").strip().upper()
    if previous_product_type == "COMPLEX" and not updated.get("image_data_type"):
        updated["image_data_type"] = "COMPLEX"
    for key, value in parsed.items():
        if value not in (None, ""):
            updated[key] = value
    if parsed.get("source_product_token"):
        updated["filename_class_token"] = parsed.get("source_product_token")
    if previous_product_type == "COMPLEX":
        updated["xml_image_data_type_repaired_from_product_type"] = True
    return updated


def _ready_reason(row: Dict[str, Any], metadata: Dict[str, Any], coverage_polygon: Any) -> tuple[bool, Optional[str]]:
    reasons = []
    if not coverage_polygon or len(coverage_polygon) < 3:
        reasons.append("missing_footprint")
    if not row.get("imaging_date"):
        reasons.append("missing_date")
    if not row.get("orbit_direction"):
        reasons.append("missing_orbit_direction")
    if not row.get("imaging_mode"):
        reasons.append("missing_imaging_mode")
    if not row.get("polarization"):
        reasons.append("missing_polarization")
    tokens = {
        str(row.get("product_type") or "").strip().upper(),
        str(row.get("image_data_type") or "").strip().upper(),
        str(row.get("source_product_token") or "").strip().upper(),
        str(row.get("product_variant") or "").strip().upper(),
        str(metadata.get("image_data_type") or "").strip().upper(),
        str(metadata.get("product_variant") or "").strip().upper(),
        str(metadata.get("filename_class_token") or "").strip().upper(),
        str(metadata.get("source_product_token") or "").strip().upper(),
    }
    if not tokens.intersection(COMPLEX_TOKENS):
        reasons.append("not_complex_source")
    if reasons:
        return False, ";".join(reasons)
    return True, None


def _bbox(points: Any) -> Optional[tuple[float, float, float, float]]:
    ordered = _ordered_closed_polygon(points)
    if not ordered or len(ordered) < 4:
        return None
    try:
        lons = [float(item[0]) for item in ordered]
        lats = [float(item[1]) for item in ordered]
    except (TypeError, ValueError, IndexError):
        return None
    return min(lons), min(lats), max(lons), max(lats)


async def repair(apply: bool) -> Dict[str, int]:
    database.init_db(settings.DATABASE_URL)
    stats = {
        "source_seen": 0,
        "source_repaired": 0,
        "radar_seen": 0,
        "radar_repaired": 0,
        "radar_ready": 0,
        "radar_not_ready": 0,
        "source_polygon_repaired": 0,
        "radar_polygon_repaired": 0,
        "skipped_unparsed": 0,
    }
    async with database.AsyncSessionLocal() as db:
        assets = (
            await db.execute(
                select(SourceProductAssetORM).where(
                    SourceProductAssetORM.satellite_family == "LT1",
                    SourceProductAssetORM.source_format == "LT1_ARCHIVE",
                )
            )
        ).scalars().all()
        for asset in assets:
            stats["source_seen"] += 1
            scene_name = _scene_name_from_path(asset.file_path, asset.logical_product_uid)
            parsed = parse_lt1_radar_filename(scene_name)
            if not parsed:
                stats["skipped_unparsed"] += 1
                continue
            metadata = _normalize_lt1_metadata(_metadata_dict(asset.metadata_json), parsed)
            ordered_polygon = _ordered_closed_polygon(metadata.get("coverage_polygon"))
            if ordered_polygon:
                metadata["coverage_polygon"] = ordered_polygon
                metadata["coverage_bbox"] = _bbox(ordered_polygon)
                stats["source_polygon_repaired"] += 1
            asset.product_type = parsed.get("product_type") or asset.product_type
            asset.product_level = parsed.get("product_level") or asset.product_level
            asset.imaging_mode = parsed.get("imaging_mode") or asset.imaging_mode
            asset.polarization = parsed.get("polarization") or asset.polarization
            asset.absolute_orbit = parsed.get("orbit_circle") or asset.absolute_orbit
            asset.imaging_date = parsed.get("imaging_date") or asset.imaging_date
            asset.satellite = parsed.get("satellite") or asset.satellite
            asset.logical_product_uid = scene_name
            asset.metadata_json = metadata
            asset.parser_version = "asset_inventory_v2"
            asset.updated_at = datetime.utcnow()
            stats["source_repaired"] += 1

        radars = (
            await db.execute(
                select(RadarDataORM).where(
                    RadarDataORM.satellite_family == "LT1",
                    or_(
                        RadarDataORM.source_format == "LT1_ARCHIVE",
                        RadarDataORM.file_path.ilike("%.tar.gz"),
                        RadarDataORM.file_path.ilike("%.tgz"),
                        RadarDataORM.file_path.ilike("%.tar"),
                        RadarDataORM.file_path.ilike("%.zip"),
                    ),
                )
            )
        ).scalars().all()
        for radar in radars:
            stats["radar_seen"] += 1
            scene_name = _scene_name_from_path(radar.file_path, radar.product_unique_id)
            parsed = parse_lt1_radar_filename(scene_name)
            if not parsed:
                stats["skipped_unparsed"] += 1
                continue
            metadata = _normalize_lt1_metadata(_metadata_dict(radar.metadata_json), parsed)
            ordered_polygon = _ordered_closed_polygon(radar.coverage_polygon or metadata.get("coverage_polygon"))
            if ordered_polygon:
                metadata["coverage_polygon"] = ordered_polygon
                metadata["coverage_bbox"] = _bbox(ordered_polygon)
                radar.coverage_polygon = ordered_polygon
                stats["radar_polygon_repaired"] += 1
            product_type = parsed.get("product_type") or radar.product_type
            source_token = parsed.get("source_product_token") or radar.source_product_token
            row = {
                "product_type": product_type,
                "image_data_type": radar.image_data_type or metadata.get("image_data_type") or "COMPLEX",
                "source_product_token": source_token,
                "product_variant": radar.product_variant or metadata.get("product_variant"),
                "imaging_date": parsed.get("imaging_date") or radar.imaging_date,
                "orbit_direction": radar.orbit_direction,
                "imaging_mode": parsed.get("imaging_mode") or radar.imaging_mode,
                "polarization": parsed.get("polarization") or radar.polarization,
            }
            ready, reason = _ready_reason(row, metadata, radar.coverage_polygon)
            radar.product_type = product_type
            radar.source_product_token = source_token
            radar.product_level = parsed.get("product_level") or radar.product_level
            radar.imaging_mode = parsed.get("imaging_mode") or radar.imaging_mode
            radar.polarization = parsed.get("polarization") or radar.polarization
            radar.orbit_circle = parsed.get("orbit_circle") or radar.orbit_circle
            radar.absolute_orbit = parsed.get("orbit_circle") or radar.absolute_orbit
            radar.imaging_date = parsed.get("imaging_date") or radar.imaging_date
            radar.satellite = parsed.get("satellite") or radar.satellite
            radar.product_unique_id = scene_name
            radar.image_data_type = row["image_data_type"]
            radar.image_data_format = radar.image_data_format or "ARCHIVE"
            radar.metadata_json = metadata
            radar.insar_source_ready = ready
            radar.insar_source_reason = reason
            bbox = _bbox(radar.coverage_polygon)
            if bbox:
                radar.min_lon, radar.min_lat, radar.max_lon, radar.max_lat = bbox
                try:
                    poly = Polygon(radar.coverage_polygon)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if not poly.is_empty:
                        radar.geom = from_shape(poly, srid=4326)
                except Exception:
                    pass
            stats["radar_repaired"] += 1
            if ready:
                stats["radar_ready"] += 1
            else:
                stats["radar_not_ready"] += 1

        if apply:
            await db.commit()
        else:
            await db.rollback()

        ready_count = (
            await db.execute(
                select(func.count(RadarDataORM.id)).where(
                    RadarDataORM.satellite_family == "LT1",
                    RadarDataORM.insar_source_ready.is_(True),
                )
            )
        ).scalar_one()
        stats["db_lt1_ready_after"] = int(ready_count or 0)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit database changes")
    args = parser.parse_args()
    stats = asyncio.run(repair(apply=args.apply))
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode} LT1 archive metadata repair")
    for key in sorted(stats):
        print(f"{key}: {stats[key]}")


if __name__ == "__main__":
    main()
