from __future__ import annotations

import json
import logging
import os
import re as _re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import distinct, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

try:
    from shapely.geometry import Point, Polygon, box, mapping, shape
except Exception:  # pragma: no cover - production dependency is optional for stats fallback
    Point = None
    Polygon = None
    box = None
    mapping = None
    shape = None

from ..auth_service import ROLE_ADMIN
from ..config import settings
from ..database import get_db
from ..models import (
    AssetInventoryIssueORM,
    AssetInventoryStateORM,
    AuthUserORM,
    DinsarProductionRunORM,
    DinsarTaskBatchORM,
    DinsarTaskItemORM,
    OrbitAssetORM,
    RadarDataORM,
    ResultAssetORM,
    ResultIssueORM,
    ResultProductORM,
    SARSceneGeoORM,
    SARSceneGeometryProfileORM,
    SceneOrbitBindingORM,
    SourceMetadataDocumentORM,
    SourceProductAssetORM,
    WorkflowRunORM,
)
from ..services.data_service import data_service
from ..services.dinsar_read_service import dinsar_read_service
from ..services.pairing_state_service import pairing_state_service
from ..services.admin_region_lookup_service import _build_region_path, _load_region_records
from ..utils import find_xml_file
from . import dependencies as _deps
from .dependencies import _get_current_user

router = APIRouter()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _family_label(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"LT1", "LT-1", "LUTAN", "LUTAN1"}:
        return "LT-1"
    if text in {"S1", "SENTINEL1", "SENTINEL-1"}:
        return "Sentinel-1"
    if text in {"GF3", "GAOFEN3", "GAOFEN-3"}:
        return "GF3"
    return text or "未分类"


def _status_label(value: Any) -> str:
    return str(value or "UNKNOWN").strip().upper() or "UNKNOWN"


def _month_from_yyyymmdd(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if len(text) >= 6 and text[:6].isdigit():
        return f"{text[:4]}-{text[4:6]}"
    return None


def _month_from_datetime(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return value.strftime("%Y-%m")
    except AttributeError:
        text = str(value)
        if len(text) >= 7:
            return text[:7]
    return None


def _percent_text(value: float) -> str:
    return f"{round(value * 100, 1)}%"


def _point_bbox(lon: Any, lat: Any) -> Optional[tuple[float, float, float, float]]:
    lon_value = _safe_float(lon)
    lat_value = _safe_float(lat)
    if lon_value is None or lat_value is None:
        return None
    return (lon_value, lat_value, lon_value, lat_value)


def _polygon_points(value: Any) -> list[tuple[float, float]]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if isinstance(value, dict):
        coordinates = value.get("coordinates")
        if value.get("type") == "Feature":
            return _polygon_points(value.get("geometry"))
        if value.get("type") == "Polygon" and coordinates:
            value = coordinates[0] if coordinates else []
        elif value.get("type") == "MultiPolygon" and coordinates:
            value = coordinates[0][0] if coordinates and coordinates[0] else []
        else:
            return []
    points: list[tuple[float, float]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                lon = _safe_float(item.get("lon", item.get("longitude")))
                lat = _safe_float(item.get("lat", item.get("latitude")))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                lon = _safe_float(item[0])
                lat = _safe_float(item[1])
            else:
                continue
            if lon is not None and lat is not None:
                points.append((lon, lat))
    return points


def _bbox_from_polygon_or_values(
    polygon_value: Any,
    min_lon: Any = None,
    min_lat: Any = None,
    max_lon: Any = None,
    max_lat: Any = None,
) -> Optional[tuple[float, float, float, float]]:
    values = [_safe_float(min_lon), _safe_float(min_lat), _safe_float(max_lon), _safe_float(max_lat)]
    if all(value is not None for value in values):
        left, bottom, right, top = values
        if left > right:
            left, right = right, left
        if bottom > top:
            bottom, top = top, bottom
        return (left, bottom, right, top)
    points = _polygon_points(polygon_value)
    if not points:
        return None
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return (min(lons), min(lats), max(lons), max(lats))


def _shape_from_polygon_or_bbox(polygon_value: Any, bbox_value: tuple[float, float, float, float]):
    if Polygon is not None and shape is not None:
        if polygon_value:
            try:
                if isinstance(polygon_value, str):
                    polygon_value = json.loads(polygon_value)
                if isinstance(polygon_value, dict):
                    geom = shape(polygon_value.get("geometry") if polygon_value.get("type") == "Feature" else polygon_value)
                    if not geom.is_empty:
                        return geom
                points = _polygon_points(polygon_value)
                if len(points) >= 3:
                    geom = Polygon(points)
                    if geom.is_valid and not geom.is_empty:
                        return geom
            except Exception:
                pass
        if box is not None:
            left, bottom, right, top = bbox_value
            if left != right and bottom != top:
                return box(left, bottom, right, top)
    return None


def _build_heatmap_grid(items: list[dict[str, Any]], *, columns: int = 48) -> dict[str, Any]:
    valid_items = [
        item for item in items
        if item.get("bbox") is not None
    ]
    if not valid_items:
        return {
            "total": len(items),
            "covered_count": 0,
            "cell_count": 0,
            "max_count": 0,
            "extent": {"min_lon": None, "min_lat": None, "max_lon": None, "max_lat": None},
            "cells": [],
        }

    min_lon = min(item["bbox"][0] for item in valid_items)
    min_lat = min(item["bbox"][1] for item in valid_items)
    max_lon = max(item["bbox"][2] for item in valid_items)
    max_lat = max(item["bbox"][3] for item in valid_items)
    lon_span = max(max_lon - min_lon, 0.01)
    lat_span = max(max_lat - min_lat, 0.01)
    rows = max(16, min(40, round((columns * lat_span) / lon_span)))
    cell_lon = lon_span / columns
    cell_lat = lat_span / rows
    buckets: dict[tuple[int, int], dict[str, Any]] = {}

    def add_to_bucket(col: int, row: int, item: dict[str, Any]) -> None:
        key = (col, row)
        bucket = buckets.setdefault(
            key,
            {
                "col": col,
                "row": row,
                "count": 0,
                "families": {},
                "catalogs": {},
                "examples": [],
            },
        )
        bucket["count"] += 1
        family = str(item.get("family") or "").strip()
        catalog = str(item.get("catalog") or "").strip()
        if family:
            bucket["families"][family] = bucket["families"].get(family, 0) + 1
        if catalog:
            bucket["catalogs"][catalog] = bucket["catalogs"].get(catalog, 0) + 1
        if len(bucket["examples"]) < 4:
            bucket["examples"].append({
                "label": item.get("label"),
                "family": family or None,
                "catalog": catalog or None,
                "date": item.get("date"),
            })

    for item in valid_items:
        left, bottom, right, top = item["bbox"]
        col_start = max(0, min(columns - 1, int((left - min_lon) / cell_lon)))
        col_end = max(0, min(columns - 1, int((right - min_lon) / cell_lon)))
        row_start = max(0, min(rows - 1, int((bottom - min_lat) / cell_lat)))
        row_end = max(0, min(rows - 1, int((top - min_lat) / cell_lat)))
        geom = _shape_from_polygon_or_bbox(item.get("polygon"), item["bbox"])
        for col in range(col_start, col_end + 1):
            for row in range(row_start, row_end + 1):
                if geom is not None and box is not None:
                    cell = box(
                        min_lon + col * cell_lon,
                        min_lat + row * cell_lat,
                        min_lon + (col + 1) * cell_lon,
                        min_lat + (row + 1) * cell_lat,
                    )
                    try:
                        if not geom.intersects(cell):
                            continue
                    except Exception:
                        pass
                add_to_bucket(col, row, item)

    cells = []
    for bucket in buckets.values():
        col = bucket["col"]
        row = bucket["row"]
        dominant_family = sorted(bucket["families"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if bucket["families"] else None
        dominant_catalog = sorted(bucket["catalogs"].items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if bucket["catalogs"] else None
        cells.append(
            {
                "col": col,
                "row": row,
                "count": bucket["count"],
                "lon_min": round(min_lon + col * cell_lon, 6),
                "lon_max": round(min_lon + (col + 1) * cell_lon, 6),
                "lat_min": round(min_lat + row * cell_lat, 6),
                "lat_max": round(min_lat + (row + 1) * cell_lat, 6),
                "lon": round(min_lon + (col + 0.5) * cell_lon, 6),
                "lat": round(min_lat + (row + 0.5) * cell_lat, 6),
                "dominant_family": dominant_family,
                "dominant_catalog": dominant_catalog,
                "families": [
                    {"name": name, "count": count}
                    for name, count in sorted(bucket["families"].items(), key=lambda kv: (-kv[1], kv[0]))
                ],
                "catalogs": [
                    {"name": name, "count": count}
                    for name, count in sorted(bucket["catalogs"].items(), key=lambda kv: (-kv[1], kv[0]))
                ],
                "examples": bucket["examples"],
            }
        )
    cells.sort(key=lambda item: (-item["count"], item["row"], item["col"]))

    return {
        "total": len(items),
        "covered_count": len(valid_items),
        "cell_count": len(cells),
        "max_count": max((cell["count"] for cell in cells), default=0),
        "columns": columns,
        "rows": rows,
        "extent": {
            "min_lon": round(min_lon, 6),
            "min_lat": round(min_lat, 6),
            "max_lon": round(max_lon, 6),
            "max_lat": round(max_lat, 6),
        },
        "cells": cells,
    }


def _build_region_match_candidates(records: list[Any]) -> list[tuple[Any, tuple[float, float, float, float]]]:
    candidates = []
    for record in records:
        try:
            bounds = tuple(float(value) for value in record.geometry.bounds)
        except Exception:
            continue
        if len(bounds) == 4:
            candidates.append((record, bounds))
    return candidates


def _match_city_region(
    lon: Any,
    lat: Any,
    region_candidates: list[tuple[Any, tuple[float, float, float, float]]],
    region_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    lon_value = _safe_float(lon)
    lat_value = _safe_float(lat)
    if lon_value is None or lat_value is None:
        return None
    try:
        point = Point(lon_value, lat_value)
    except Exception:
        return None

    matched = None
    for record, bounds in region_candidates:
        min_lon, min_lat, max_lon, max_lat = bounds
        if lon_value < min_lon or lon_value > max_lon or lat_value < min_lat or lat_value > max_lat:
            continue
        try:
            if record.geometry.covers(point):
                matched = record
                break
        except Exception:
            continue
    if matched is None:
        return None

    path_names, path_tree_ids = _build_region_path(matched.tree_id, region_by_id)
    city_tree_id = None
    city_name = None
    province_name = None
    for tree_id, name in zip(path_tree_ids, path_names):
        node_level = str((region_by_id.get(tree_id) or {}).get("level") or "").strip().lower()
        if node_level == "province":
            province_name = name
        if node_level == "city":
            city_tree_id = tree_id
            city_name = name
            break

    if not city_tree_id:
        level = str(getattr(matched, "level", "") or "").lower()
        if level == "city":
            city_tree_id = matched.tree_id
            city_name = matched.name
        else:
            parts = str(matched.tree_id).split("-")
            if len(parts) >= 3:
                city_tree_id = "-".join(parts[:3])
                city_name = (region_by_id.get(city_tree_id) or {}).get("name") or matched.name

    if not city_tree_id:
        return None

    return {
        "tree_id": city_tree_id,
        "name": str(city_name or city_tree_id),
        "province": province_name,
        "matched_tree_id": matched.tree_id,
    }


def _echarts_map_geometry(geometry: Any) -> dict[str, Any] | None:
    if mapping is None or geometry is None or getattr(geometry, "is_empty", True):
        return None
    try:
        simplified = geometry.simplify(0.015, preserve_topology=True)
        if simplified is not None and not simplified.is_empty:
            geometry = simplified
    except Exception:
        pass
    try:
        geometry_json = mapping(geometry)
    except Exception:
        return None
    if geometry_json.get("type") not in {"Polygon", "MultiPolygon"}:
        return None
    coordinates = geometry_json.get("coordinates")
    if not coordinates:
        return None
    return geometry_json


def _build_city_region_coverage(
    source_points: list[dict[str, Any]],
    result_points: list[dict[str, Any]],
) -> dict[str, Any]:
    records, region_by_id, error = _load_region_records()
    if error:
        return {
            "status": "unavailable",
            "message": error,
            "features": {"type": "FeatureCollection", "features": []},
            "source": {"total": len(source_points), "matched_count": 0, "max_count": 0, "regions": []},
            "results": {"total": len(result_points), "matched_count": 0, "max_count": 0, "regions": []},
        }

    by_tree: dict[str, dict[str, Any]] = {}
    region_candidates = _build_region_match_candidates(records)

    def ensure_bucket(region: dict[str, Any]) -> dict[str, Any]:
        tree_id = region["tree_id"]
        return by_tree.setdefault(
            tree_id,
            {
                "tree_id": tree_id,
                "name": region.get("name") or tree_id,
                "province": region.get("province"),
                "source_count": 0,
                "result_count": 0,
                "families": {},
                "catalogs": {},
            },
        )

    for item in source_points:
        region = _match_city_region(item.get("lon"), item.get("lat"), region_candidates, region_by_id)
        if not region:
            continue
        bucket = ensure_bucket(region)
        bucket["source_count"] += 1
        family = str(item.get("family") or "").strip()
        if family:
            bucket["families"][family] = bucket["families"].get(family, 0) + 1

    for item in result_points:
        region = _match_city_region(item.get("lon"), item.get("lat"), region_candidates, region_by_id)
        if not region:
            continue
        bucket = ensure_bucket(region)
        bucket["result_count"] += 1
        catalog = str(item.get("catalog") or "").strip()
        if catalog:
            bucket["catalogs"][catalog] = bucket["catalogs"].get(catalog, 0) + 1

    city_records = {record.tree_id: record for record in records if str(record.level or "").lower() == "city"}
    features = []
    for tree_id, bucket in by_tree.items():
        record = city_records.get(tree_id)
        if record is None:
            continue
        geometry_json = _echarts_map_geometry(record.geometry)
        if geometry_json is None:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "tree_id": tree_id,
                    "name": bucket["name"],
                    "province": bucket.get("province"),
                    "source_count": bucket["source_count"],
                    "result_count": bucket["result_count"],
                },
                "geometry": geometry_json,
            }
        )
        try:
            point = record.geometry.representative_point()
            bucket["center_lon"] = float(point.x)
            bucket["center_lat"] = float(point.y)
        except Exception:
            pass

    def rows_for(kind: str) -> list[dict[str, Any]]:
        count_key = "source_count" if kind == "source" else "result_count"
        detail_key = "families" if kind == "source" else "catalogs"
        return [
            {
                "tree_id": bucket["tree_id"],
                "name": bucket["name"],
                "province": bucket.get("province"),
                "count": bucket[count_key],
                "lon": bucket.get("center_lon"),
                "lat": bucket.get("center_lat"),
                "breakdown": [
                    {"name": name, "count": count}
                    for name, count in sorted(bucket[detail_key].items(), key=lambda kv: (-kv[1], kv[0]))
                ],
            }
            for bucket in sorted(by_tree.values(), key=lambda item: (-item[count_key], item["name"]))
            if bucket[count_key] > 0
        ]

    source_rows = rows_for("source")
    result_rows = rows_for("results")
    return {
        "status": "ok",
        "features": {"type": "FeatureCollection", "features": features},
        "source": {
            "total": len(source_points),
            "matched_count": sum(item["count"] for item in source_rows),
            "max_count": max((item["count"] for item in source_rows), default=0),
            "regions": source_rows,
        },
        "results": {
            "total": len(result_points),
            "matched_count": sum(item["count"] for item in result_rows),
            "max_count": max((item["count"] for item in result_rows), default=0),
            "regions": result_rows,
        },
    }


async def _scalar_count(db: AsyncSession, stmt) -> int:
    result = await db.execute(stmt)
    return _safe_int(result.scalar_one())


@router.get("/statistics/dashboard")
async def get_statistics_dashboard(
    fresh: bool = False,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Business-facing dashboard statistics for the production overview page.

    This endpoint keeps the leadership/statistics dashboard separate from the
    legacy /statistics health-consistency payload.
    """
    if fresh and current_user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Only admin can force refresh dashboard statistics.")

    now_mono = time.monotonic()
    if _deps.DASHBOARD_STATS_CACHE_TTL_SECONDS > 0 and not fresh:
        async with _deps._DASHBOARD_STATS_CACHE_LOCK:
            if (
                _deps._DASHBOARD_STATS_CACHE_DATA is not None
                and now_mono < _deps._DASHBOARD_STATS_CACHE_EXPIRES_AT
            ):
                return {
                    **_deps._DASHBOARD_STATS_CACHE_DATA,
                    "cache_meta": {
                        "enabled": True,
                        "hit": True,
                        "ttl_seconds": _deps.DASHBOARD_STATS_CACHE_TTL_SECONDS,
                        "generated_at": _deps._DASHBOARD_STATS_CACHE_GENERATED_AT_UTC,
                    },
                }

    generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    source_total = await _scalar_count(
        db,
        select(func.count(SourceProductAssetORM.id)).where(SourceProductAssetORM.is_active == True),
    )
    radar_total = await _scalar_count(db, select(func.count(RadarDataORM.id)))
    metadata_asset_total = await _scalar_count(
        db,
        select(func.count(distinct(SourceMetadataDocumentORM.source_asset_id))),
    )
    metadata_doc_total = await _scalar_count(db, select(func.count(SourceMetadataDocumentORM.id)))
    geometry_total = await _scalar_count(db, select(func.count(SARSceneGeometryProfileORM.id)))
    geometry_ready = await _scalar_count(
        db,
        select(func.count(SARSceneGeometryProfileORM.id)).where(
            SARSceneGeometryProfileORM.metadata_quality == "READY",
            SARSceneGeometryProfileORM.production_readiness == "READY",
        ),
    )
    preview_ready = await _scalar_count(
        db,
        select(func.count(RadarDataORM.id)).where(RadarDataORM.preview_cache_status == "READY"),
    )

    source_group_rows = await db.execute(
        select(
            SourceProductAssetORM.satellite_family,
            SourceProductAssetORM.source_format,
            SourceProductAssetORM.parse_status,
            func.count(SourceProductAssetORM.id),
        )
        .where(SourceProductAssetORM.is_active == True)
        .group_by(
            SourceProductAssetORM.satellite_family,
            SourceProductAssetORM.source_format,
            SourceProductAssetORM.parse_status,
        )
        .order_by(SourceProductAssetORM.satellite_family, SourceProductAssetORM.source_format)
    )
    source_by_family_map: dict[str, dict[str, Any]] = {}
    source_by_format: list[dict[str, Any]] = []
    for family, source_format, parse_status, count in source_group_rows.all():
        family_label = _family_label(family)
        status_label = _status_label(parse_status)
        count_int = _safe_int(count)
        family_bucket = source_by_family_map.setdefault(
            family_label,
            {
                "family": family_label,
                "count": 0,
                "ready_count": 0,
                "issue_count": 0,
                "formats": {},
            },
        )
        family_bucket["count"] += count_int
        if status_label in {"OK", "READY", "NATIVE_READY"}:
            family_bucket["ready_count"] += count_int
        else:
            family_bucket["issue_count"] += count_int
        format_label = str(source_format or "UNKNOWN")
        family_bucket["formats"][format_label] = family_bucket["formats"].get(format_label, 0) + count_int
        source_by_format.append(
            {
                "family": family_label,
                "source_format": format_label,
                "parse_status": status_label,
                "count": count_int,
            }
        )

    source_by_family = []
    for item in source_by_family_map.values():
        item["ready_rate"] = _ratio(item["ready_count"], item["count"])
        item["formats"] = [
            {"name": name, "count": count}
            for name, count in sorted(item["formats"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        source_by_family.append(item)
    source_by_family.sort(key=lambda row: (-row["count"], row["family"]))

    geometry_rows = await db.execute(
        select(
            SARSceneGeometryProfileORM.satellite_family,
            SARSceneGeometryProfileORM.metadata_quality,
            SARSceneGeometryProfileORM.production_readiness,
            func.count(SARSceneGeometryProfileORM.id),
        )
        .group_by(
            SARSceneGeometryProfileORM.satellite_family,
            SARSceneGeometryProfileORM.metadata_quality,
            SARSceneGeometryProfileORM.production_readiness,
        )
        .order_by(SARSceneGeometryProfileORM.satellite_family)
    )
    geometry_by_family_map: dict[str, dict[str, Any]] = {}
    for family, metadata_quality, production_readiness, count in geometry_rows.all():
        family_label = _family_label(family)
        count_int = _safe_int(count)
        bucket = geometry_by_family_map.setdefault(
            family_label,
            {"family": family_label, "count": 0, "ready_count": 0, "issue_count": 0, "statuses": {}},
        )
        bucket["count"] += count_int
        key = f"{_status_label(metadata_quality)} / {_status_label(production_readiness)}"
        bucket["statuses"][key] = bucket["statuses"].get(key, 0) + count_int
        if _status_label(metadata_quality) == "READY" and _status_label(production_readiness) == "READY":
            bucket["ready_count"] += count_int
        else:
            bucket["issue_count"] += count_int
    geometry_by_family = []
    for item in geometry_by_family_map.values():
        item["ready_rate"] = _ratio(item["ready_count"], item["count"])
        item["statuses"] = [
            {"name": name, "count": count}
            for name, count in sorted(item["statuses"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        geometry_by_family.append(item)
    geometry_by_family.sort(key=lambda row: (-row["count"], row["family"]))

    source_month_rows = await db.execute(
        select(SourceProductAssetORM.imaging_date, SourceProductAssetORM.satellite_family)
        .where(SourceProductAssetORM.is_active == True)
        .where(SourceProductAssetORM.imaging_date.isnot(None))
    )
    source_month_map: dict[str, dict[str, Any]] = {}
    for imaging_date, family in source_month_rows.all():
        month = _month_from_yyyymmdd(imaging_date)
        if not month:
            continue
        family_label = _family_label(family)
        bucket = source_month_map.setdefault(month, {"month": month, "total": 0, "by_family": {}})
        bucket["total"] += 1
        bucket["by_family"][family_label] = bucket["by_family"].get(family_label, 0) + 1
    source_by_month = [source_month_map[key] for key in sorted(source_month_map)]

    orbit_total = await _scalar_count(
        db,
        select(func.count(OrbitAssetORM.id)).where(OrbitAssetORM.is_active == True),
    )
    orbit_group_rows = await db.execute(
        select(OrbitAssetORM.satellite_family, OrbitAssetORM.parse_status, func.count(OrbitAssetORM.id))
        .where(OrbitAssetORM.is_active == True)
        .group_by(OrbitAssetORM.satellite_family, OrbitAssetORM.parse_status)
        .order_by(OrbitAssetORM.satellite_family)
    )
    orbit_by_family_map: dict[str, dict[str, Any]] = {}
    for family, parse_status, count in orbit_group_rows.all():
        family_label = _family_label(family)
        status_label = _status_label(parse_status)
        count_int = _safe_int(count)
        bucket = orbit_by_family_map.setdefault(
            family_label,
            {"family": family_label, "count": 0, "ok_count": 0, "issue_count": 0, "statuses": {}},
        )
        bucket["count"] += count_int
        bucket["statuses"][status_label] = bucket["statuses"].get(status_label, 0) + count_int
        if status_label == "OK":
            bucket["ok_count"] += count_int
        else:
            bucket["issue_count"] += count_int
    orbit_by_family = []
    for item in orbit_by_family_map.values():
        item["ok_rate"] = _ratio(item["ok_count"], item["count"])
        item["statuses"] = [
            {"name": name, "count": count}
            for name, count in sorted(item["statuses"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        orbit_by_family.append(item)
    orbit_by_family.sort(key=lambda row: (-row["count"], row["family"]))

    orbit_required_total = sum(
        item["count"]
        for item in source_by_family
        if item["family"] in {"LT-1", "Sentinel-1"}
    )
    selected_orbit_bindings = await _scalar_count(
        db,
        select(func.count(SceneOrbitBindingORM.id)).where(SceneOrbitBindingORM.selection_status == "SELECTED"),
    )
    matched_orbit_bindings = await _scalar_count(
        db,
        select(func.count(SceneOrbitBindingORM.id)).where(SceneOrbitBindingORM.match_status == "MATCHED"),
    )

    empty_legacy_coverage_grid = {
        "total": 0,
        "covered_count": 0,
        "cell_count": 0,
        "max_count": 0,
        "extent": {"min_lon": None, "min_lat": None, "max_lon": None, "max_lat": None},
        "cells": [],
    }

    coverage_rows = await db.execute(
        select(
            SARSceneGeometryProfileORM.id,
            SARSceneGeometryProfileORM.satellite_family,
            SARSceneGeometryProfileORM.acquisition_start_time_utc,
            SARSceneGeometryProfileORM.scene_center_lon,
            SARSceneGeometryProfileORM.scene_center_lat,
        )
        .where(
            SARSceneGeometryProfileORM.scene_center_lon.isnot(None),
            SARSceneGeometryProfileORM.scene_center_lat.isnot(None),
        )
        .order_by(SARSceneGeometryProfileORM.acquisition_start_time_utc.desc().nullslast())
    )
    source_region_points: list[dict[str, Any]] = []
    for (
        row_id,
        family,
        acquisition_start,
        lon,
        lat,
    ) in coverage_rows.all():
        family_label = _family_label(family)
        lon_float = _safe_float(lon)
        lat_float = _safe_float(lat)
        if lon_float is not None and lat_float is not None:
            source_region_points.append(
                {
                    "id": row_id,
                    "family": family_label,
                    "lon": lon_float,
                    "lat": lat_float,
                    "date": acquisition_start.date().isoformat() if acquisition_start else None,
                }
            )
    source_coverage_grid = {**empty_legacy_coverage_grid, "total": len(source_region_points), "covered_count": len(source_region_points)}

    result_total = await _scalar_count(db, select(func.count(ResultProductORM.id)))
    result_rows = await db.execute(
        select(
            ResultProductORM.catalog_name,
            ResultProductORM.status,
            ResultProductORM.health_status,
            func.count(ResultProductORM.id),
        )
        .group_by(ResultProductORM.catalog_name, ResultProductORM.status, ResultProductORM.health_status)
        .order_by(ResultProductORM.catalog_name)
    )
    results_by_catalog_map: dict[str, dict[str, Any]] = {}
    for catalog_name, status, health_status, count in result_rows.all():
        catalog = str(catalog_name or "unknown")
        count_int = _safe_int(count)
        bucket = results_by_catalog_map.setdefault(
            catalog,
            {"catalog": catalog, "count": 0, "ready_count": 0, "issue_count": 0, "statuses": {}, "health": {}},
        )
        bucket["count"] += count_int
        status_label = _status_label(status)
        health_label = _status_label(health_status)
        bucket["statuses"][status_label] = bucket["statuses"].get(status_label, 0) + count_int
        bucket["health"][health_label] = bucket["health"].get(health_label, 0) + count_int
        if status_label == "READY" and health_label == "OK":
            bucket["ready_count"] += count_int
        else:
            bucket["issue_count"] += count_int
    results_by_catalog = []
    for item in results_by_catalog_map.values():
        item["ready_rate"] = _ratio(item["ready_count"], item["count"])
        item["statuses"] = [
            {"name": name, "count": count}
            for name, count in sorted(item["statuses"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        item["health"] = [
            {"name": name, "count": count}
            for name, count in sorted(item["health"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        results_by_catalog.append(item)
    results_by_catalog.sort(key=lambda row: (-row["count"], row["catalog"]))

    result_assets_total = await _scalar_count(db, select(func.count(ResultAssetORM.id)))
    result_assets_missing = await _scalar_count(
        db,
        select(func.count(ResultAssetORM.id)).where(ResultAssetORM.exists_flag == False),
    )
    result_preview_count = await _scalar_count(
        db,
        select(func.count(ResultProductORM.id)).where(ResultProductORM.preview_path.isnot(None)),
    )

    result_month_rows = await db.execute(
        select(
            ResultProductORM.catalog_name,
            ResultProductORM.published_at,
            ResultProductORM.produced_at,
            ResultProductORM.registered_at,
        )
    )
    result_month_map: dict[str, dict[str, Any]] = {}
    for catalog, published_at, produced_at, registered_at in result_month_rows.all():
        month = _month_from_datetime(published_at or produced_at or registered_at)
        if not month:
            continue
        bucket = result_month_map.setdefault(month, {"month": month, "total": 0, "by_catalog": {}})
        bucket["total"] += 1
        catalog_label = str(catalog or "unknown")
        bucket["by_catalog"][catalog_label] = bucket["by_catalog"].get(catalog_label, 0) + 1
    results_by_month = [result_month_map[key] for key in sorted(result_month_map)]

    result_coverage_rows = await db.execute(
        select(
            ResultProductORM.id,
            ResultProductORM.catalog_name,
            ResultProductORM.product_type,
            ResultProductORM.produced_at,
            ResultProductORM.published_at,
            ResultProductORM.registered_at,
            ResultProductORM.min_lon,
            ResultProductORM.min_lat,
            ResultProductORM.max_lon,
            ResultProductORM.max_lat,
            ResultProductORM.coverage_polygon,
        )
        .where(
            (ResultProductORM.coverage_polygon.isnot(None))
            | (
                ResultProductORM.min_lon.isnot(None)
                & ResultProductORM.min_lat.isnot(None)
                & ResultProductORM.max_lon.isnot(None)
                & ResultProductORM.max_lat.isnot(None)
            )
        )
    )
    result_region_points: list[dict[str, Any]] = []
    for (
        product_id,
        catalog_name,
        product_type,
        produced_at,
        published_at,
        registered_at,
        min_lon_value,
        min_lat_value,
        max_lon_value,
        max_lat_value,
        coverage_polygon,
    ) in result_coverage_rows.all():
        bbox = _bbox_from_polygon_or_values(
            coverage_polygon,
            min_lon_value,
            min_lat_value,
            max_lon_value,
            max_lat_value,
        )
        center_lon = center_lat = None
        if bbox:
            center_lon = (bbox[0] + bbox[2]) / 2
            center_lat = (bbox[1] + bbox[3]) / 2
            result_region_points.append(
                {
                    "id": product_id,
                    "catalog": str(catalog_name or product_type or "unknown"),
                    "lon": center_lon,
                    "lat": center_lat,
                    "date": _month_from_datetime(published_at or produced_at or registered_at),
                }
            )
    result_coverage_grid = {**empty_legacy_coverage_grid, "total": len(result_region_points), "covered_count": len(result_region_points)}
    city_region_coverage = _build_city_region_coverage(source_region_points, result_region_points)

    dinsar_batch_count = await _scalar_count(db, select(func.count(DinsarTaskBatchORM.id)))
    dinsar_task_count = await _scalar_count(db, select(func.count(DinsarTaskItemORM.id)))
    dinsar_task_status_rows = await db.execute(
        select(DinsarTaskItemORM.status, func.count(DinsarTaskItemORM.id))
        .group_by(DinsarTaskItemORM.status)
        .order_by(DinsarTaskItemORM.status)
    )
    dinsar_task_status = [
        {"status": _status_label(status), "count": _safe_int(count)}
        for status, count in dinsar_task_status_rows.all()
    ]

    production_run_rows = await db.execute(
        select(
            DinsarProductionRunORM.run_id,
            DinsarProductionRunORM.product_family,
            DinsarProductionRunORM.engine_code,
            DinsarProductionRunORM.status,
            DinsarProductionRunORM.total_items,
            DinsarProductionRunORM.completed_items,
            DinsarProductionRunORM.failed_items,
            DinsarProductionRunORM.started_at,
            DinsarProductionRunORM.ended_at,
            DinsarProductionRunORM.created_at,
            DinsarProductionRunORM.latest_message,
        )
        .order_by(DinsarProductionRunORM.created_at.desc().nullslast(), DinsarProductionRunORM.id.desc())
    )
    production_status_map: dict[str, int] = {}
    production_engine_map: dict[str, dict[str, Any]] = {}
    recent_production_runs: list[dict[str, Any]] = []
    duration_seconds: list[float] = []
    production_run_count = 0
    for (
        run_id,
        product_family,
        engine_code,
        status,
        total_items,
        completed_items,
        failed_items,
        started_at,
        ended_at,
        created_at,
        latest_message,
    ) in production_run_rows.all():
        production_run_count += 1
        status_label = _status_label(status)
        production_status_map[status_label] = production_status_map.get(status_label, 0) + 1
        engine_label = str(engine_code or "unknown")
        engine_bucket = production_engine_map.setdefault(
            engine_label,
            {"engine": engine_label, "count": 0, "completed": 0, "failed": 0, "running": 0},
        )
        engine_bucket["count"] += 1
        if status_label in {"COMPLETED", "SUCCESS", "DONE"}:
            engine_bucket["completed"] += 1
        elif status_label in {"FAILED", "ERROR"}:
            engine_bucket["failed"] += 1
        elif status_label in {"RUNNING", "PENDING", "QUEUED"}:
            engine_bucket["running"] += 1
        if started_at and ended_at:
            try:
                duration_seconds.append((ended_at - started_at).total_seconds())
            except Exception:
                pass
        if len(recent_production_runs) < 8:
            recent_production_runs.append(
                {
                    "run_id": run_id,
                    "product_family": product_family,
                    "engine_code": engine_label,
                    "status": status_label,
                    "total_items": _safe_int(total_items),
                    "completed_items": _safe_int(completed_items),
                    "failed_items": _safe_int(failed_items),
                    "created_at": created_at.isoformat() if created_at else None,
                    "started_at": started_at.isoformat() if started_at else None,
                    "ended_at": ended_at.isoformat() if ended_at else None,
                    "latest_message": latest_message,
                }
            )

    workflow_rows = await db.execute(
        select(WorkflowRunORM.workflow_name, WorkflowRunORM.status, func.count(WorkflowRunORM.id))
        .group_by(WorkflowRunORM.workflow_name, WorkflowRunORM.status)
        .order_by(WorkflowRunORM.workflow_name, WorkflowRunORM.status)
    )
    workflow_status = [
        {"workflow": str(workflow or "unknown"), "status": _status_label(status), "count": _safe_int(count)}
        for workflow, status, count in workflow_rows.all()
    ]

    result_issue_rows = await db.execute(
        select(ResultIssueORM.severity, ResultIssueORM.issue_code, ResultIssueORM.status, func.count(ResultIssueORM.id))
        .group_by(ResultIssueORM.severity, ResultIssueORM.issue_code, ResultIssueORM.status)
        .order_by(ResultIssueORM.severity, ResultIssueORM.issue_code)
    )
    inventory_issue_rows = await db.execute(
        select(
            AssetInventoryIssueORM.severity,
            AssetInventoryIssueORM.issue_code,
            AssetInventoryIssueORM.status,
            func.count(AssetInventoryIssueORM.id),
        )
        .group_by(AssetInventoryIssueORM.severity, AssetInventoryIssueORM.issue_code, AssetInventoryIssueORM.status)
        .order_by(AssetInventoryIssueORM.severity, AssetInventoryIssueORM.issue_code)
    )
    issue_total = 0
    open_issue_total = 0
    issue_by_severity: dict[str, int] = {}
    issue_by_code: dict[str, int] = {}
    for severity, issue_code, status, count in list(result_issue_rows.all()) + list(inventory_issue_rows.all()):
        count_int = _safe_int(count)
        status_label = _status_label(status)
        severity_label = _status_label(severity)
        code_label = str(issue_code or "UNKNOWN")
        issue_total += count_int
        if status_label == "OPEN":
            open_issue_total += count_int
            issue_by_severity[severity_label] = issue_by_severity.get(severity_label, 0) + count_int
            issue_by_code[code_label] = issue_by_code.get(code_label, 0) + count_int

    inventory_state_rows = await db.execute(
        select(
            AssetInventoryStateORM.inventory_type,
            AssetInventoryStateORM.status,
            AssetInventoryStateORM.last_seen_entry_count,
            AssetInventoryStateORM.last_asset_count,
            AssetInventoryStateORM.last_issue_count,
            AssetInventoryStateORM.last_scan_started_at,
            AssetInventoryStateORM.last_scan_finished_at,
            AssetInventoryStateORM.needs_rescan,
        )
        .order_by(AssetInventoryStateORM.updated_at.desc().nullslast(), AssetInventoryStateORM.id.desc())
        .limit(12)
    )
    inventory_states = [
        {
            "inventory_type": inventory_type,
            "status": _status_label(status),
            "last_seen_entry_count": _safe_int(last_seen_entry_count),
            "last_asset_count": _safe_int(last_asset_count),
            "last_issue_count": _safe_int(last_issue_count),
            "last_scan_started_at": last_scan_started_at.isoformat() if last_scan_started_at else None,
            "last_scan_finished_at": last_scan_finished_at.isoformat() if last_scan_finished_at else None,
            "needs_rescan": bool(needs_rescan),
        }
        for (
            inventory_type,
            status,
            last_seen_entry_count,
            last_asset_count,
            last_issue_count,
            last_scan_started_at,
            last_scan_finished_at,
            needs_rescan,
        ) in inventory_state_rows.all()
    ]

    avg_duration_seconds = round(sum(duration_seconds) / len(duration_seconds), 1) if duration_seconds else None
    selected_orbit_rate = _ratio(selected_orbit_bindings, orbit_required_total)
    geometry_ready_rate = _ratio(geometry_ready, source_total)
    metadata_ready_rate = _ratio(metadata_asset_total, source_total)
    result_ready_total = sum(item["ready_count"] for item in results_by_catalog)

    risk_count = (
        max(0, source_total - metadata_asset_total)
        + max(0, source_total - geometry_ready)
        + max(0, orbit_required_total - selected_orbit_bindings)
        + result_assets_missing
        + open_issue_total
    )

    kpis = [
        {
            "key": "source_total",
            "label": "源数据资产",
            "value": source_total,
            "unit": "景",
            "note": f"兼容台账 {radar_total} 条",
            "tone": "primary",
        },
        {
            "key": "metadata_ready",
            "label": "元数据入库率",
            "value": round(metadata_ready_rate * 100, 1),
            "unit": "%",
            "note": f"{metadata_asset_total}/{source_total} 景已提取 XML/元数据",
            "tone": "success" if metadata_ready_rate >= 0.98 else "warning",
        },
        {
            "key": "geometry_ready",
            "label": "几何画像可用率",
            "value": round(geometry_ready_rate * 100, 1),
            "unit": "%",
            "note": f"{geometry_ready}/{source_total} 景可用于覆盖统计",
            "tone": "success" if geometry_ready_rate >= 0.95 else "warning",
        },
        {
            "key": "orbit_selected",
            "label": "精轨绑定率",
            "value": round(selected_orbit_rate * 100, 1),
            "unit": "%",
            "note": f"{selected_orbit_bindings}/{orbit_required_total} 景已选中精轨",
            "tone": "success" if selected_orbit_rate >= 0.95 else "warning",
        },
        {
            "key": "result_total",
            "label": "形变成果",
            "value": result_total,
            "unit": "项",
            "note": f"健康成果 {result_ready_total} 项，预览 {result_preview_count} 项",
            "tone": "primary",
        },
        {
            "key": "risk_total",
            "label": "待关注项",
            "value": risk_count,
            "unit": "项",
            "note": f"开放问题 {open_issue_total}，缺失成果资产 {result_assets_missing}",
            "tone": "danger" if risk_count else "success",
        },
    ]

    dashboard_payload = {
        "generated_at": generated_at,
        "kpis": kpis,
        "asset": {
            "source_total": source_total,
            "radar_total": radar_total,
            "source_by_family": source_by_family,
            "source_by_format": source_by_format,
            "source_by_month": source_by_month,
            "metadata_asset_total": metadata_asset_total,
            "metadata_doc_total": metadata_doc_total,
            "metadata_ready_rate": metadata_ready_rate,
            "geometry_total": geometry_total,
            "geometry_ready": geometry_ready,
            "geometry_ready_rate": geometry_ready_rate,
            "geometry_by_family": geometry_by_family,
            "preview_ready": preview_ready,
            "pipeline": [
                {"key": "source", "label": "源资产登记", "value": source_total, "rate": 1.0},
                {"key": "metadata", "label": "元数据入库", "value": metadata_asset_total, "rate": metadata_ready_rate},
                {"key": "geometry", "label": "几何画像", "value": geometry_total, "rate": _ratio(geometry_total, source_total)},
                {"key": "ready", "label": "可生产画像", "value": geometry_ready, "rate": geometry_ready_rate},
                {"key": "preview", "label": "预览缓存", "value": preview_ready, "rate": _ratio(preview_ready, radar_total)},
            ],
        },
        "orbit": {
            "orbit_total": orbit_total,
            "orbit_by_family": orbit_by_family,
            "orbit_required_total": orbit_required_total,
            "selected_bindings": selected_orbit_bindings,
            "matched_bindings": matched_orbit_bindings,
            "selected_rate": selected_orbit_rate,
        },
        "coverage": {
            "point_total": geometry_total,
            "source": source_coverage_grid,
            "results": result_coverage_grid,
            "city_regions": city_region_coverage,
        },
        "production": {
            "dinsar_batch_count": dinsar_batch_count,
            "dinsar_task_count": dinsar_task_count,
            "dinsar_task_status": dinsar_task_status,
            "run_count": production_run_count,
            "run_status": [
                {"status": status, "count": count}
                for status, count in sorted(production_status_map.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "engine_status": sorted(production_engine_map.values(), key=lambda row: (-row["count"], row["engine"])),
            "avg_duration_seconds": avg_duration_seconds,
            "recent_runs": recent_production_runs,
            "workflow_status": workflow_status,
        },
        "results": {
            "result_total": result_total,
            "result_ready_total": result_ready_total,
            "result_preview_count": result_preview_count,
            "result_assets_total": result_assets_total,
            "result_assets_missing": result_assets_missing,
            "results_by_catalog": results_by_catalog,
            "results_by_month": results_by_month,
        },
        "issues": {
            "issue_total": issue_total,
            "open_issue_total": open_issue_total,
            "by_severity": [
                {"severity": severity, "count": count}
                for severity, count in sorted(issue_by_severity.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "by_code": [
                {"code": code, "count": count}
                for code, count in sorted(issue_by_code.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
            ],
        },
        "inventory": {
            "states": inventory_states,
        },
        "summary": {
            "metadata_ready_text": _percent_text(metadata_ready_rate),
            "geometry_ready_text": _percent_text(geometry_ready_rate),
            "orbit_selected_text": _percent_text(selected_orbit_rate),
        },
    }

    if _deps.DASHBOARD_STATS_CACHE_TTL_SECONDS > 0:
        async with _deps._DASHBOARD_STATS_CACHE_LOCK:
            _deps._DASHBOARD_STATS_CACHE_DATA = dashboard_payload
            _deps._DASHBOARD_STATS_CACHE_EXPIRES_AT = time.monotonic() + _deps.DASHBOARD_STATS_CACHE_TTL_SECONDS
            _deps._DASHBOARD_STATS_CACHE_GENERATED_AT_UTC = generated_at

    return {
        **dashboard_payload,
        "cache_meta": {
            "enabled": _deps.DASHBOARD_STATS_CACHE_TTL_SECONDS > 0,
            "hit": False,
            "ttl_seconds": _deps.DASHBOARD_STATS_CACHE_TTL_SECONDS,
            "generated_at": generated_at,
        },
    }


@router.get("/statistics")
async def get_statistics(
    fresh: bool = False,
    current_user: AuthUserORM = Depends(_get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    获取关于Dinsar结果和源数据的统计信息。
    """
    if fresh and current_user.role != ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Only admin can force refresh statistics.")

    now_mono = time.monotonic()
    if _deps.STATS_CACHE_TTL_SECONDS > 0 and not fresh:
        async with _deps._STATS_CACHE_LOCK:
            if _deps._STATS_CACHE_DATA is not None and now_mono < _deps._STATS_CACHE_EXPIRES_AT:
                return {
                    **_deps._STATS_CACHE_DATA,
                    "cache_meta": {
                        "enabled": True,
                        "hit": True,
                        "ttl_seconds": _deps.STATS_CACHE_TTL_SECONDS,
                        "generated_at": _deps._STATS_CACHE_GENERATED_AT_UTC,
                    },
                }

    # 1. D-InSAR 结果统计（catalog 主读模型）
    dinsar_records = await dinsar_read_service.list_catalog_records(db)
    dinsar_total_count = len(dinsar_records)
    dinsar_cache_consistency = {
        "db_marked_cached_count": 0,
        "cache_file_exists_count": 0,
        "db_cached_and_file_exists_count": 0,
        "db_cached_but_file_missing_count": 0,
        "db_uncached_but_file_exists_count": 0,
        "db_uncached_and_file_missing_count": 0,
        "manifest_entries_count": 0,
        "manifest_missing_file_count": 0,
    }
    try:
        for record in dinsar_records:
            preview_path = str(record.product.preview_path or "").strip()
            manifest_path = str(record.product.manifest_path or "").strip()
            preview_exists = bool(preview_path and os.path.exists(preview_path))
            fallback_exists = bool(record.image_path and os.path.exists(record.image_path))

            if preview_path:
                dinsar_cache_consistency["db_marked_cached_count"] += 1
            if preview_exists:
                dinsar_cache_consistency["cache_file_exists_count"] += 1
            if preview_path and preview_exists:
                dinsar_cache_consistency["db_cached_and_file_exists_count"] += 1
            elif preview_path and (not preview_exists):
                dinsar_cache_consistency["db_cached_but_file_missing_count"] += 1
            elif fallback_exists:
                dinsar_cache_consistency["db_uncached_but_file_exists_count"] += 1
            else:
                dinsar_cache_consistency["db_uncached_and_file_missing_count"] += 1

            if manifest_path:
                dinsar_cache_consistency["manifest_entries_count"] += 1
                if not os.path.exists(manifest_path):
                    dinsar_cache_consistency["manifest_missing_file_count"] += 1
    except Exception as e:
        dinsar_cache_consistency["error"] = str(e)

    dinsar_cached_count = dinsar_cache_consistency["db_marked_cached_count"]

    # 2. 源数据统计
    source_data_total_count = 0
    envi_processed_count = 0
    with_orbit_data_count = 0
    by_satellite: Dict[str, Any] = {}
    source_preview_consistency = {
        "total_records_count": 0,
        "geo_cache_exists_count": 0,
        "raw_cache_exists_count": 0,
        "preview_exists_count": 0,
        "preview_missing_count": 0,
        "db_ready_count": 0,
        "db_ready_and_cache_exists_count": 0,
        "db_ready_but_cache_missing_count": 0,
    }
    source_xml_consistency = {
        "total_records_count": 0,
        "xml_detected_count": 0,
        "xml_missing_count": 0,
        "xml_parsed_ok_count": 0,
        "xml_detected_but_unparsed_count": 0,
    }

    try:
        source_data_total_count_res = await db.execute(select(func.count(RadarDataORM.id)))
        source_data_total_count = source_data_total_count_res.scalar_one()

        if source_data_total_count > 0:
            envi_processed_count_res = await db.execute(select(func.count(RadarDataORM.id)).where(RadarDataORM.is_envi_processed == True))
            envi_processed_count = envi_processed_count_res.scalar_one()

            with_orbit_data_count_res = await db.execute(select(func.count(RadarDataORM.id)).where(RadarDataORM.has_orbit_data == True))
            with_orbit_data_count = with_orbit_data_count_res.scalar_one()

            by_satellite_res = await db.execute(select(RadarDataORM.satellite, func.count(RadarDataORM.id)).group_by(RadarDataORM.satellite))
            by_satellite = {sat: count for sat, count in by_satellite_res.all()}

        source_rows_res = await db.execute(
            select(
                RadarDataORM.unique_id,
                RadarDataORM.file_path,
                RadarDataORM.preview_cache_status,
                RadarDataORM.scene_center_lon,
                RadarDataORM.scene_center_lat,
                RadarDataORM.acquisition_time_utc,
                RadarDataORM.satellite_mode,
                RadarDataORM.receiving_station,
                RadarDataORM.product_level,
                RadarDataORM.product_unique_id,
            )
        )
        source_rows = source_rows_res.all()
        source_preview_consistency["total_records_count"] = len(source_rows)
        source_xml_consistency["total_records_count"] = len(source_rows)

        for (
            unique_id,
            file_path,
            preview_cache_status,
            scene_center_lon,
            scene_center_lat,
            acquisition_time_utc,
            satellite_mode,
            receiving_station,
            product_level,
            product_unique_id,
        ) in source_rows:
            if not file_path:
                source_preview_consistency["preview_missing_count"] += 1
                source_xml_consistency["xml_missing_count"] += 1
                continue

            cache_key = unique_id or file_path
            raw_cache_path = data_service.get_radar_raw_cache_path(cache_key, file_path)
            geo_cache_path = data_service.get_radar_geo_cache_path(cache_key, file_path)
            has_raw_cache = os.path.exists(raw_cache_path)
            has_geo_cache = os.path.exists(geo_cache_path)

            if has_geo_cache:
                source_preview_consistency["geo_cache_exists_count"] += 1
            if has_raw_cache:
                source_preview_consistency["raw_cache_exists_count"] += 1

            has_any_preview_cache = has_geo_cache or has_raw_cache
            if has_any_preview_cache:
                source_preview_consistency["preview_exists_count"] += 1
            else:
                source_preview_consistency["preview_missing_count"] += 1

            status = (preview_cache_status or "NONE").upper()
            if status == "READY":
                source_preview_consistency["db_ready_count"] += 1
                if has_any_preview_cache:
                    source_preview_consistency["db_ready_and_cache_exists_count"] += 1
                else:
                    source_preview_consistency["db_ready_but_cache_missing_count"] += 1

            scene_dir = file_path if os.path.isdir(file_path) else os.path.dirname(file_path)
            xml_path = find_xml_file(scene_dir) if scene_dir else None
            has_xml = bool(xml_path and os.path.exists(xml_path))
            if has_xml:
                source_xml_consistency["xml_detected_count"] += 1
                parsed_ok = any(
                    value is not None and value != ""
                    for value in [
                        scene_center_lon,
                        scene_center_lat,
                        acquisition_time_utc,
                        satellite_mode,
                        receiving_station,
                        product_level,
                        product_unique_id,
                    ]
                )
                if parsed_ok:
                    source_xml_consistency["xml_parsed_ok_count"] += 1
                else:
                    source_xml_consistency["xml_detected_but_unparsed_count"] += 1
            else:
                source_xml_consistency["xml_missing_count"] += 1

    except Exception as e:
        logger.warning("统计源数据时发生错误 (可能是表不存在): %s", e)
        source_preview_consistency["error"] = str(e)
        source_xml_consistency["error"] = str(e)

    # 4. AI 质量统计
    labeled_good_count = sum(1 for record in dinsar_records if record.product.user_label == 1)
    labeled_bad_count = sum(1 for record in dinsar_records if record.product.user_label == 0)

    unlabeled_count = dinsar_total_count - labeled_good_count - labeled_bad_count

    # 5. AI 预测统计
    ai_good_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is not None and record.product.ai_score >= 0.7
    )
    ai_bad_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is not None and record.product.ai_score < 0.4
    )
    ai_medium_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is not None and 0.4 <= record.product.ai_score < 0.7
    )
    ai_unpredicted_count = sum(
        1 for record in dinsar_records
        if record.product.ai_score is None
    )

    # 6. IDL 处理统计（读取 runs/*.json）
    idl_processing_stats: Dict[str, Any] = {
        "by_workflow_success": {},
        "avg_duration_by_workflow": {},
    }
    try:
        all_runs = data_service.envi_service_list_runs_all() if hasattr(data_service, "envi_service_list_runs_all") else []
        # 直接读取 runs 目录
        from ..services.envi_service import list_recent_runs as _list_runs
        all_runs = _list_runs(limit=500)
        wf_counts: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failed": 0})
        wf_durations: Dict[str, list] = defaultdict(list)
        for run in all_runs:
            wf = run.get("workflow", "unknown")
            status = run.get("status", "")
            dur = run.get("duration_seconds")
            if status == "success":
                wf_counts[wf]["success"] += 1
            elif status == "failed":
                wf_counts[wf]["failed"] += 1
            if dur is not None:
                try:
                    wf_durations[wf].append(float(dur))
                except (TypeError, ValueError):
                    pass
        idl_processing_stats["by_workflow_success"] = {k: dict(v) for k, v in wf_counts.items()}
        idl_processing_stats["avg_duration_by_workflow"] = {
            k: round(sum(v) / len(v), 1) for k, v in wf_durations.items() if v
        }
    except Exception as _e:
        idl_processing_stats["error"] = str(_e)

    # 8. 水体地理编码一致性检测
    water_geo_consistency: Dict[str, Any] = {
        "water_results_dir": settings.WATER_RESULTS_DIR,
        "dir_scanned_count": 0,
        "geo_db_exists_count": 0,
        "matched_in_db_count": 0,
        "unregistered_count": 0,
        "registered_but_missing_count": 0,
    }
    try:
        import re as _re2
        water_dir = settings.WATER_RESULTS_DIR
        _uid_re = _re2.compile(r"_(\d{7,})$")

        # 从 DB 拉取所有 product_unique_id -> radar_data_id 映射
        uid_rows = await db.execute(
            select(RadarDataORM.product_unique_id, RadarDataORM.id)
            .where(RadarDataORM.product_unique_id.isnot(None))
        )
        uid_to_radar_id: Dict[str, int] = {uid: rid for uid, rid in uid_rows.all() if uid}

        # 从 DB 拉取所有 DONE 的 radar_data_id 集合
        done_rows = await db.execute(
            select(SARSceneGeoORM.radar_data_id, SARSceneGeoORM.geo_path)
            .where(SARSceneGeoORM.status == "DONE")
        )
        done_radar_ids: Dict[int, str] = {rid: gp for rid, gp in done_rows.all()}

        if os.path.isdir(water_dir):
            for entry in os.scandir(water_dir):
                if not entry.is_dir() or not entry.name.startswith("scene_"):
                    continue
                water_geo_consistency["dir_scanned_count"] += 1

                # 检查目录内是否有 *_geo_db 文件
                geo_db_path = None
                for f in os.scandir(entry.path):
                    if f.name.endswith("_geo_db") and not f.name.endswith(".hdr") and not f.name.endswith(".sml"):
                        geo_db_path = f.path
                        break
                if not geo_db_path:
                    continue
                water_geo_consistency["geo_db_exists_count"] += 1

                # 解析 product_unique_id
                m = _uid_re.search(entry.name)
                if not m:
                    continue
                uid = m.group(1).lstrip("0") or m.group(1)
                radar_id = uid_to_radar_id.get(m.group(1)) or uid_to_radar_id.get(uid)
                if not radar_id:
                    continue
                water_geo_consistency["matched_in_db_count"] += 1

                if radar_id not in done_radar_ids:
                    water_geo_consistency["unregistered_count"] += 1

        # 反向检查：DB DONE 但 geo_db 文件不存在
        for radar_id, geo_path in done_radar_ids.items():
            if geo_path and not os.path.exists(geo_path):
                water_geo_consistency["registered_but_missing_count"] += 1

    except Exception as _e:
        water_geo_consistency["error"] = str(_e)

    # 7. D-InSAR 结果按月统计（从 name 字段解析主影像日期）
    dinsar_by_month: list = []
    try:
        _month_counts: Dict[str, int] = defaultdict(int)
        _date_re = _re.compile(r"(\d{8})")
        for record in dinsar_records:
            name = (
                record.product.task_alias
                or record.product.display_name
                or record.product.task_name
                or record.display_name
            )
            if not name:
                continue
            dates = _date_re.findall(name)
            if not dates:
                continue
            master_date = dates[0]
            _month_counts[f"{master_date[:4]}-{master_date[4:6]}"] += 1
        dinsar_by_month = [
            {"month": k, "count": v}
            for k, v in sorted(_month_counts.items())
        ]
    except Exception as _e:
        dinsar_by_month = []

    pairing_consistency: Dict[str, Any] = {
        "metric_cache_count": 0,
        "network_run_count": 0,
        "network_edge_count": 0,
        "dirty_scene_count": 0,
        "cache_status": None,
        "needs_rebuild": None,
        "duplicate_reverse_pair_count": 0,
        "invalid_orientation_count": 0,
        "network_edge_orphan_count": 0,
        "task_orphan_count": 0,
        "result_trace_missing_count": 0,
        "result_trace_orphan_count": 0,
        "result_trace_pair_mismatch_count": 0,
    }
    try:
        pairing_status = await pairing_state_service.get_pairing_system_status(db)
        pairing_consistency["metric_cache_count"] = int(pairing_status.get("pair_count") or 0)
        pairing_consistency["network_run_count"] = int(pairing_status.get("network_run_count") or 0)
        pairing_consistency["network_edge_count"] = int(pairing_status.get("network_edge_count") or 0)
        pairing_consistency["dirty_scene_count"] = int(pairing_status.get("dirty_scene_count") or 0)
        pairing_consistency["cache_status"] = pairing_status.get("status")
        pairing_consistency["needs_rebuild"] = bool(pairing_status.get("needs_rebuild"))
        pairing_consistency["duplicate_reverse_pair_count"] = int(
            pairing_status.get("duplicate_reverse_pair_count") or 0
        )
        pairing_consistency["network_edge_orphan_count"] = int(
            pairing_status.get("orphan_edge_count") or 0
        )

        invalid_orientation_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM pairing_metric_cache
                WHERE
                    master_imaging_date IS NULL
                    OR slave_imaging_date IS NULL
                    OR master_scene_uid IS NULL
                    OR slave_scene_uid IS NULL
                    OR master_imaging_date > slave_imaging_date
                    OR (
                        master_imaging_date = slave_imaging_date
                        AND (
                            master_scene_uid > slave_scene_uid
                            OR (
                                master_scene_uid = slave_scene_uid
                                AND master_scene_ref_id > slave_scene_ref_id
                            )
                        )
                    )
                """
            )
        )
        pairing_consistency["invalid_orientation_count"] = int(
            invalid_orientation_result.scalar_one() or 0
        )

        result_trace_missing_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM result_products
                WHERE catalog_name = 'dinsar'
                  AND (
                    COALESCE(pair_uid, '') = ''
                    OR COALESCE(network_run_id, '') = ''
                    OR network_edge_id IS NULL
                    OR COALESCE(policy_version, '') = ''
                  )
                """
            )
        )
        pairing_consistency["result_trace_missing_count"] = int(
            result_trace_missing_result.scalar_one() or 0
        )

        result_trace_orphan_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM result_products rp
                LEFT JOIN pairing_network_runs pnr
                    ON pnr.network_run_id = rp.network_run_id
                LEFT JOIN pairing_network_edges pne
                    ON pne.id = rp.network_edge_id
                    AND pne.network_run_ref_id = pnr.id
                WHERE rp.catalog_name = 'dinsar'
                  AND COALESCE(rp.pair_uid, '') <> ''
                  AND COALESCE(rp.network_run_id, '') <> ''
                  AND rp.network_edge_id IS NOT NULL
                  AND (pnr.id IS NULL OR pne.id IS NULL)
                """
            )
        )
        pairing_consistency["result_trace_orphan_count"] = int(
            result_trace_orphan_result.scalar_one() or 0
        )

        result_trace_pair_mismatch_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM result_products rp
                JOIN pairing_network_runs pnr
                    ON pnr.network_run_id = rp.network_run_id
                JOIN pairing_network_edges pne
                    ON pne.id = rp.network_edge_id
                    AND pne.network_run_ref_id = pnr.id
                JOIN pairing_metric_cache pmc
                    ON pmc.id = pne.metric_cache_ref_id
                WHERE rp.catalog_name = 'dinsar'
                  AND COALESCE(rp.pair_uid, '') <> ''
                  AND COALESCE(pmc.pair_uid, '') <> ''
                  AND rp.pair_uid <> pmc.pair_uid
                """
            )
        )
        pairing_consistency["result_trace_pair_mismatch_count"] = int(
            result_trace_pair_mismatch_result.scalar_one() or 0
        )
    except Exception as _e:
        pairing_consistency["error"] = str(_e)

    stats_payload = {
        "dinsar_results_overview": {
            "total_count": dinsar_total_count,
            "cached_count": dinsar_cached_count,
            "uncached_count": dinsar_total_count - dinsar_cached_count,
        },
        "dinsar_cache_consistency": dinsar_cache_consistency,
        "source_data_overview": {
            "total_count": source_data_total_count,
            "envi_processed_count": envi_processed_count,
            "with_orbit_data_count": with_orbit_data_count,
        },
        "source_preview_consistency": source_preview_consistency,
        "source_xml_consistency": source_xml_consistency,
        "water_geo_consistency": water_geo_consistency,
        "pairing_consistency": pairing_consistency,
        "by_satellite": by_satellite,
        "idl_processing_stats": idl_processing_stats,
        "dinsar_by_month": dinsar_by_month,
        "ai_quality_overview": {
            "good_count": labeled_good_count,
            "bad_count": labeled_bad_count,
            "unlabeled_count": unlabeled_count
        },
        "ai_prediction_overview": {
            "good_count": ai_good_count,
            "bad_count": ai_bad_count,
            "medium_count": ai_medium_count,
            "unpredicted_count": ai_unpredicted_count
        }
    }

    generated_at = datetime.utcnow().isoformat() + "Z"
    if _deps.STATS_CACHE_TTL_SECONDS > 0:
        async with _deps._STATS_CACHE_LOCK:
            _deps._STATS_CACHE_DATA = stats_payload
            _deps._STATS_CACHE_EXPIRES_AT = time.monotonic() + _deps.STATS_CACHE_TTL_SECONDS
            _deps._STATS_CACHE_GENERATED_AT_UTC = generated_at

    return {
        **stats_payload,
        "cache_meta": {
            "enabled": _deps.STATS_CACHE_TTL_SECONDS > 0,
            "hit": False,
            "ttl_seconds": _deps.STATS_CACHE_TTL_SECONDS,
            "generated_at": generated_at,
        },
    }
