from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPolygon, Point, shape
from shapely.ops import unary_union

try:
    from shapely.validation import make_valid as _make_valid_geometry
except Exception:  # pragma: no cover - depends on the installed Shapely version.
    _make_valid_geometry = None


_LEVEL_RANK = {
    "country": 0,
    "province": 1,
    "city": 2,
    "district": 3,
    "county": 3,
}


@dataclass(frozen=True)
class _RegionGeometryRecord:
    tree_id: str
    name: str
    level: str | None
    adcode: str | None
    geometry: Any
    area: float


_REGION_GEOMETRY_CACHE: list[_RegionGeometryRecord] | None = None
_REGION_BY_ID_LOOKUP_CACHE: dict[str, dict[str, Any]] | None = None
_REGION_LOAD_ERROR: str | None = None
_POLYGONAL_GEOMETRY_TYPES = {"Polygon", "MultiPolygon"}


def _backend_geojson_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "geojson"


def _normalize_region_index_node(raw: dict[str, Any]) -> dict[str, Any] | None:
    tree_id = str(raw.get("treeID") or raw.get("tree_id") or raw.get("treeId") or "").strip()
    if not tree_id:
        return None
    parent_raw = raw.get("parent")
    parent_tree_id = str(parent_raw).strip() if parent_raw is not None else None
    if parent_tree_id == "":
        parent_tree_id = None
    depth = len(tree_id.split("-"))
    level = {1: "country", 2: "province", 3: "city", 4: "district"}.get(depth, "unknown")
    return {
        "tree_id": tree_id,
        "parent_tree_id": parent_tree_id,
        "name": str(raw.get("name") or tree_id).strip(),
        "level": level,
    }


def _load_region_index_from_files() -> dict[str, dict[str, Any]]:
    geojson_dir = _backend_geojson_dir()
    candidates = [geojson_dir / "层级映射.json", *sorted(geojson_dir.glob("*.json"))]
    for path in candidates:
        if not path.is_file() or path.name == "treeid_fill_report.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        nodes: dict[str, dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            node = _normalize_region_index_node(item)
            if node:
                nodes[node["tree_id"]] = node
        if nodes:
            return nodes
    return {}


def _load_region_geometry_from_files() -> dict[str, list[dict[str, Any]]]:
    geojson_dir = _backend_geojson_dir()
    candidates = [
        geojson_dir / "全国行政区.geojson",
        geojson_dir / "中华人民共和国.geojson",
        *sorted(geojson_dir.glob("*.geojson"), key=lambda item: item.stat().st_size if item.exists() else 0, reverse=True),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        features = payload.get("features") if isinstance(payload, dict) else payload
        if not isinstance(features, list):
            continue
        feature_index: dict[str, list[dict[str, Any]]] = {}
        for feature in features:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                continue
            props = feature.get("properties") or {}
            if not isinstance(props, dict):
                continue
            tree_id = str(props.get("treeID") or props.get("tree_id") or props.get("treeId") or "").strip()
            if tree_id:
                feature_index.setdefault(tree_id, []).append(feature)
        if feature_index:
            return feature_index
    return {}


def _repair_geometry(geometry):
    if geometry is None or geometry.is_empty:
        return None
    if getattr(geometry, "is_valid", True):
        return geometry
    if _make_valid_geometry is not None:
        try:
            fixed = _make_valid_geometry(geometry)
            if fixed is not None and not fixed.is_empty:
                return fixed
        except Exception:
            pass
    try:
        fixed = geometry.buffer(0)
        if fixed is not None and not fixed.is_empty:
            return fixed
    except Exception:
        pass
    return None


def _merge_region_geometries(geometries: list[Any]):
    fixed_geometries = []
    for geometry in geometries:
        fixed = _repair_geometry(geometry)
        if fixed is not None and not fixed.is_empty:
            fixed_geometries.append(fixed)
    if not fixed_geometries:
        return None
    if len(fixed_geometries) == 1:
        return fixed_geometries[0]
    try:
        merged = unary_union(fixed_geometries)
        return _repair_geometry(merged)
    except Exception:
        repaired_buffers = []
        for geometry in fixed_geometries:
            try:
                buffered = geometry.buffer(0)
            except Exception:
                continue
            if buffered is not None and not buffered.is_empty:
                repaired_buffers.append(buffered)
        if not repaired_buffers:
            return None
        try:
            merged = unary_union(repaired_buffers)
            return _repair_geometry(merged)
        except Exception:
            return None


def _build_region_path(tree_id: str, region_by_id: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    names: list[str] = []
    tree_ids: list[str] = []
    current = tree_id
    guard = 0
    while current and guard < 12:
        node = region_by_id.get(current) or {}
        name = str(node.get("name") or current).strip()
        if name:
            names.append(name)
        tree_ids.append(current)
        current = str(node.get("parent_tree_id") or "").strip()
        guard += 1
    names.reverse()
    tree_ids.reverse()
    if names and names[0] in {"中国", "中华人民共和国"}:
        names = names[1:]
    if tree_ids and tree_ids[0] == "1":
        tree_ids = tree_ids[1:]
    return names, tree_ids


def _as_polygonal_geometry(geometry):
    if geometry is None or geometry.is_empty:
        return None
    if getattr(geometry, "geom_type", None) in _POLYGONAL_GEOMETRY_TYPES:
        return geometry

    polygon_parts = []
    for part in getattr(geometry, "geoms", []) or []:
        polygonal = _as_polygonal_geometry(part)
        if polygonal is None or polygonal.is_empty:
            continue
        if getattr(polygonal, "geom_type", None) == "Polygon":
            polygon_parts.append(polygonal)
        else:
            polygon_parts.extend([item for item in getattr(polygonal, "geoms", []) if not item.is_empty])

    if not polygon_parts:
        return None
    if len(polygon_parts) == 1:
        return polygon_parts[0]
    try:
        merged = unary_union(polygon_parts)
        if merged is not None and not merged.is_empty:
            if getattr(merged, "geom_type", None) in _POLYGONAL_GEOMETRY_TYPES:
                return merged
            return _as_polygonal_geometry(merged)
    except Exception:
        pass
    try:
        return MultiPolygon(polygon_parts)
    except Exception:
        return polygon_parts[0]


def _load_region_records() -> tuple[list[_RegionGeometryRecord], dict[str, dict[str, Any]], str | None]:
    global _REGION_BY_ID_LOOKUP_CACHE, _REGION_GEOMETRY_CACHE, _REGION_LOAD_ERROR
    if _REGION_GEOMETRY_CACHE is not None:
        return _REGION_GEOMETRY_CACHE, _REGION_BY_ID_LOOKUP_CACHE or {}, _REGION_LOAD_ERROR
    try:
        from ..routers import dependencies as deps

        deps._load_region_index()
        deps._load_region_geometry_index()
        region_by_id = deps._REGION_BY_ID_CACHE or {}
        geometry_by_id = deps._REGION_GEOMETRY_BY_ID_CACHE or {}
    except Exception:
        region_by_id = _load_region_index_from_files()
        geometry_by_id = _load_region_geometry_from_files()
        if not region_by_id or not geometry_by_id:
            _REGION_LOAD_ERROR = "AOI region index or geometry file is unavailable."
            return [], {}, _REGION_LOAD_ERROR
    try:
        records: list[_RegionGeometryRecord] = []
        for tree_id, features in geometry_by_id.items():
            geometries = []
            merged_props: dict[str, Any] = {}
            for feature in features:
                if not isinstance(feature, dict) or not feature.get("geometry"):
                    continue
                try:
                    geom = shape(feature["geometry"])
                except Exception:
                    continue
                if geom.is_empty:
                    continue
                geometries.append(geom)
                props = feature.get("properties") or {}
                if isinstance(props, dict):
                    merged_props.update({key: value for key, value in props.items() if value not in (None, "")})
            if not geometries:
                continue
            geometry = _merge_region_geometries(geometries)
            geometry = _as_polygonal_geometry(geometry)
            if geometry is None or geometry.is_empty:
                continue
            node = region_by_id.get(tree_id) or {}
            records.append(
                _RegionGeometryRecord(
                    tree_id=str(tree_id),
                    name=str(merged_props.get("name") or node.get("name") or tree_id).strip(),
                    level=str(merged_props.get("level") or node.get("level") or "").strip() or None,
                    adcode=str(merged_props.get("adcode") or "").strip() or None,
                    geometry=geometry,
                    area=float(getattr(geometry, "area", 0.0) or 0.0),
                )
            )
        records.sort(
            key=lambda item: (
                -_LEVEL_RANK.get(str(item.level or "").lower(), len(item.tree_id.split("-"))),
                item.area,
                item.tree_id,
            )
        )
        _REGION_GEOMETRY_CACHE = records
        _REGION_BY_ID_LOOKUP_CACHE = region_by_id
        _REGION_LOAD_ERROR = None
        return records, region_by_id, None
    except Exception as exc:
        _REGION_LOAD_ERROR = str(exc)
        return [], {}, _REGION_LOAD_ERROR


def lookup_admin_region_for_point(lon: Any, lat: Any) -> dict[str, Any] | None:
    try:
        lon_value = float(lon)
        lat_value = float(lat)
    except (TypeError, ValueError):
        return None
    if not (-180 <= lon_value <= 180 and -90 <= lat_value <= 90):
        return None

    records, region_by_id, error = _load_region_records()
    if error:
        return {
            "match_status": "unavailable",
            "message": error,
            "center": {"lon": lon_value, "lat": lat_value},
        }
    point = Point(lon_value, lat_value)
    best: _RegionGeometryRecord | None = None
    for record in records:
        try:
            if record.geometry.covers(point):
                best = record
                break
        except Exception:
            continue
    if best is None:
        return {
            "match_status": "not_matched",
            "center": {"lon": lon_value, "lat": lat_value},
        }

    path_names, path_tree_ids = _build_region_path(best.tree_id, region_by_id)
    display_name = " / ".join(path_names or [best.name])
    return {
        "match_status": "matched",
        "tree_id": best.tree_id,
        "name": best.name,
        "level": best.level,
        "adcode": best.adcode,
        "path_names": path_names,
        "path_tree_ids": path_tree_ids,
        "display_name": display_name,
        "center": {"lon": lon_value, "lat": lat_value},
        "source": "aoi_region_geometry",
    }


def lookup_admin_region_geometry(query: str | None) -> dict[str, Any] | None:
    text = str(query or "").strip()
    if not text:
        return None
    records, region_by_id, error = _load_region_records()
    if error:
        return {
            "match_status": "unavailable",
            "message": error,
            "query": text,
        }

    query_lower = text.lower()
    matches: list[tuple[tuple[int, int, float, str], _RegionGeometryRecord, dict[str, Any]]] = []
    for record in records:
        path_names, path_tree_ids = _build_region_path(record.tree_id, region_by_id)
        display_name = " / ".join(path_names or [record.name])
        name_lower = str(record.name or "").lower()
        display_lower = display_name.lower()
        adcode_lower = str(record.adcode or "").lower()
        tree_id_lower = str(record.tree_id or "").lower()
        path_lowers = [str(item or "").lower() for item in path_names]

        score: int | None = None
        if query_lower in {name_lower, display_lower, adcode_lower, tree_id_lower}:
            score = 0
        elif query_lower in path_lowers:
            score = 1
        elif query_lower and query_lower in name_lower:
            score = 2
        elif query_lower and query_lower in display_lower:
            score = 3
        elif query_lower and query_lower in " ".join(path_lowers + [adcode_lower, tree_id_lower]):
            score = 4
        if score is None:
            continue

        level_rank = _LEVEL_RANK.get(str(record.level or "").lower(), len(record.tree_id.split("-")))
        summary = {
            "match_status": "matched",
            "query": text,
            "tree_id": record.tree_id,
            "name": record.name,
            "level": record.level,
            "adcode": record.adcode,
            "path_names": path_names,
            "path_tree_ids": path_tree_ids,
            "display_name": display_name,
            "bbox": {
                "min_lon": float(record.geometry.bounds[0]),
                "min_lat": float(record.geometry.bounds[1]),
                "max_lon": float(record.geometry.bounds[2]),
                "max_lat": float(record.geometry.bounds[3]),
            },
            "source": "aoi_region_geometry",
        }
        matches.append(((score, level_rank, record.area, record.tree_id), record, summary))

    if not matches:
        return {
            "match_status": "not_matched",
            "query": text,
        }

    matches.sort(key=lambda item: item[0])
    _, record, summary = matches[0]
    return {
        **summary,
        "geometry": record.geometry,
    }


def admin_region_matches(region: dict[str, Any] | None, query: str | None) -> bool:
    text = str(query or "").strip().lower()
    if not text:
        return True
    if not isinstance(region, dict):
        return False
    values: list[str] = []
    for key in ("display_name", "name", "tree_id", "adcode", "level"):
        value = region.get(key)
        if value:
            values.append(str(value))
    for item in region.get("path_names") or []:
        values.append(str(item))
    return text in " ".join(values).lower()
