"""Cartographic vector product writing."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform as shapely_transform

from .constants import (
    CLASS_CARTOGRAPHIC_WATER,
    CLASS_HIGH_CONFIDENCE_WATER,
    CLASS_KNOWN_WATER,
    CLASS_LOW_CONFIDENCE_WATER,
    CLASS_NAMES,
    CLASS_NON_WATER,
    CLASS_PADDY_WATER_LIKE,
)
from .envi import EnviInfo
from .geo import meters_to_degrees, pixel_area_m2, polygon_area_m2

try:
    import fiona
except Exception:  # pragma: no cover - optional at runtime
    fiona = None

try:
    from rasterio.features import shapes
except Exception:  # pragma: no cover - optional at runtime
    shapes = None


def remove_small_polygon_holes(geom, min_hole_area_m2: float, center_lat: float):
    if min_hole_area_m2 <= 0 or geom.is_empty:
        return geom

    def clean_polygon(poly: Polygon) -> Polygon:
        interiors = []
        for ring in poly.interiors:
            hole = Polygon(ring)
            if polygon_area_m2(hole, center_lat) >= min_hole_area_m2:
                interiors.append(ring)
        return Polygon(poly.exterior, interiors)

    if geom.geom_type == "Polygon":
        return clean_polygon(geom)
    if geom.geom_type == "MultiPolygon":
        parts = [clean_polygon(poly) for poly in geom.geoms if not poly.is_empty]
        return MultiPolygon(parts) if parts else geom
    return geom


def smooth_geometry_meters(geom, smooth_meters: float, center_lat: float):
    if smooth_meters <= 0 or geom.is_empty:
        return geom
    smooth_lon, smooth_lat = meters_to_degrees(smooth_meters, center_lat)
    scaled = shapely_transform(lambda x, y, z=None: (np.asarray(x) / smooth_lon, np.asarray(y) / smooth_lat), geom)
    smoothed = scaled.buffer(1.0).buffer(-1.0)
    return shapely_transform(lambda x, y, z=None: (np.asarray(x) * smooth_lon, np.asarray(y) * smooth_lat), smoothed)


def write_classified_vectors(
    gpkg_path: Path | None,
    shp_dir: Path | None,
    classified: np.ndarray,
    cartographic_water: np.ndarray | None,
    info: EnviInfo,
    score: np.ndarray,
    hh_db: np.ndarray,
    hv_db: np.ndarray,
    slope: np.ndarray | None,
    known_water: np.ndarray,
    paddy: np.ndarray,
    min_area_m2: float,
    simplify_meters: float,
    smooth_meters: float,
    min_hole_area_m2: float,
) -> dict:
    if fiona is None or shapes is None:
        raise RuntimeError("fiona and rasterio.features.shapes are required for vector output")

    if gpkg_path is not None:
        gpkg_path.parent.mkdir(parents=True, exist_ok=True)
        if gpkg_path.exists():
            gpkg_path.unlink()
    if shp_dir is not None:
        shp_dir.mkdir(parents=True, exist_ok=True)

    px_area = pixel_area_m2(info)
    center_lat = info.y0 - info.lines * info.dy * 0.5
    simplify_lon, _ = meters_to_degrees(simplify_meters, center_lat)
    schema = {
        "geometry": "Polygon",
        "properties": {
            "class_id": "int",
            "class_name": "str:32",
            "confidence": "str:16",
            "area_m2": "float",
            "pixels": "int",
            "mean_score": "float",
            "mean_hh_db": "float",
            "mean_hv_db": "float",
            "mean_slope": "float",
            "known_water": "int",
            "paddy": "int",
            "review_flag": "int",
        },
    }
    crs = "EPSG:4326"
    counts = {name: 0 for name in CLASS_NAMES.values()}
    layers = {
        CLASS_CARTOGRAPHIC_WATER: "cartographic_water",
        CLASS_HIGH_CONFIDENCE_WATER: "high_confidence_water",
        CLASS_KNOWN_WATER: "known_water",
        CLASS_PADDY_WATER_LIKE: "paddy_water_like",
        CLASS_LOW_CONFIDENCE_WATER: "review_candidates",
    }
    handles = {}
    shp_handles = {}
    try:
        if gpkg_path is not None:
            for class_id, layer_name in layers.items():
                handles[class_id] = fiona.open(gpkg_path, "w", driver="GPKG", layer=layer_name, crs=crs, schema=schema)
        if shp_dir is not None:
            for class_id, layer_name in layers.items():
                shp_path = shp_dir / f"{layer_name}.shp"
                for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                    sidecar = shp_path.with_suffix(suffix)
                    if sidecar.exists():
                        sidecar.unlink()
                shp_handles[class_id] = fiona.open(shp_path, "w", driver="ESRI Shapefile", crs=crs, schema=schema, encoding="UTF-8")

        vector_classes = classified.copy()
        class_mask = np.isin(vector_classes, [CLASS_HIGH_CONFIDENCE_WATER, CLASS_KNOWN_WATER, CLASS_PADDY_WATER_LIKE, CLASS_LOW_CONFIDENCE_WATER])
        if cartographic_water is not None:
            class_mask |= cartographic_water
        for geom_mapping, value in shapes(vector_classes.astype(np.uint8), mask=class_mask, transform=info.transform):
            class_id = int(value)
            if cartographic_water is not None and class_id == CLASS_NON_WATER:
                continue
            if class_id not in layers:
                continue
            geom = shape(geom_mapping)
            if geom.is_empty:
                continue
            geom = smooth_geometry_meters(geom, smooth_meters, center_lat)
            geom = remove_small_polygon_holes(geom, min_hole_area_m2, center_lat)
            if geom.is_empty:
                continue
            if simplify_meters > 0:
                geom = geom.simplify(simplify_lon, preserve_topology=True)
                if geom.is_empty:
                    continue
            geom_area_m2 = polygon_area_m2(geom, center_lat)

            minx, miny, maxx, maxy = geom.bounds
            col0 = max(0, int(math.floor((minx - info.x0) / info.dx)) - 1)
            col1 = min(info.samples, int(math.ceil((maxx - info.x0) / info.dx)) + 1)
            row0 = max(0, int(math.floor((info.y0 - maxy) / info.dy)) - 1)
            row1 = min(info.lines, int(math.ceil((info.y0 - miny) / info.dy)) + 1)
            if col1 <= col0 or row1 <= row0:
                continue
            if class_id == CLASS_CARTOGRAPHIC_WATER and cartographic_water is not None:
                window = cartographic_water[row0:row1, col0:col1]
            else:
                window = classified[row0:row1, col0:col1] == class_id
            pixels = int(window.sum())
            area_m2 = pixels * px_area
            if max(area_m2, geom_area_m2) < min_area_m2:
                continue

            score_window = score[row0:row1, col0:col1]
            hh_window = hh_db[row0:row1, col0:col1]
            hv_window = hv_db[row0:row1, col0:col1]
            slope_window = slope[row0:row1, col0:col1] if slope is not None else None
            mean_slope = float(np.nanmean(slope_window[window])) if slope_window is not None and np.any(np.isfinite(slope_window[window])) else -9999.0
            review_flag = 1 if class_id in (CLASS_PADDY_WATER_LIKE, CLASS_LOW_CONFIDENCE_WATER, CLASS_CARTOGRAPHIC_WATER) else 0
            confidence = "map" if class_id == CLASS_CARTOGRAPHIC_WATER else ("high" if class_id in (CLASS_HIGH_CONFIDENCE_WATER, CLASS_KNOWN_WATER) else "review")
            feature = {
                "geometry": geom.__geo_interface__,
                "properties": {
                    "class_id": class_id,
                    "class_name": CLASS_NAMES[class_id],
                    "confidence": confidence,
                    "area_m2": float(geom_area_m2),
                    "pixels": pixels,
                    "mean_score": float(np.nanmean(score_window[window])),
                    "mean_hh_db": float(np.nanmean(hh_window[window])),
                    "mean_hv_db": float(np.nanmean(hv_window[window])),
                    "mean_slope": mean_slope,
                    "known_water": int(np.any(known_water[row0:row1, col0:col1] & window)),
                    "paddy": int(np.any(paddy[row0:row1, col0:col1] & window)),
                    "review_flag": review_flag,
                },
            }
            if class_id in handles:
                handles[class_id].write(feature)
            if class_id in shp_handles:
                shp_handles[class_id].write(feature)
            counts[CLASS_NAMES[class_id]] += 1

        if cartographic_water is not None:
            for geom_mapping, value in shapes(np.where(cartographic_water, CLASS_CARTOGRAPHIC_WATER, CLASS_NON_WATER).astype(np.uint8), mask=cartographic_water, transform=info.transform):
                class_id = int(value)
                geom = shape(geom_mapping)
                if geom.is_empty:
                    continue
                geom = smooth_geometry_meters(geom, smooth_meters, center_lat)
                geom = remove_small_polygon_holes(geom, min_hole_area_m2, center_lat)
                if geom.is_empty:
                    continue
                if simplify_meters > 0:
                    geom = geom.simplify(simplify_lon, preserve_topology=True)
                    if geom.is_empty:
                        continue
                geom_area_m2 = polygon_area_m2(geom, center_lat)
                if geom_area_m2 < min_area_m2:
                    continue
                minx, miny, maxx, maxy = geom.bounds
                col0 = max(0, int(math.floor((minx - info.x0) / info.dx)) - 1)
                col1 = min(info.samples, int(math.ceil((maxx - info.x0) / info.dx)) + 1)
                row0 = max(0, int(math.floor((info.y0 - maxy) / info.dy)) - 1)
                row1 = min(info.lines, int(math.ceil((info.y0 - miny) / info.dy)) + 1)
                if col1 <= col0 or row1 <= row0:
                    continue
                window = cartographic_water[row0:row1, col0:col1]
                pixels = int(window.sum())
                score_window = score[row0:row1, col0:col1]
                hh_window = hh_db[row0:row1, col0:col1]
                hv_window = hv_db[row0:row1, col0:col1]
                slope_window = slope[row0:row1, col0:col1] if slope is not None else None
                mean_slope = float(np.nanmean(slope_window[window])) if slope_window is not None and np.any(np.isfinite(slope_window[window])) else -9999.0
                feature = {
                    "geometry": geom.__geo_interface__,
                    "properties": {
                        "class_id": CLASS_CARTOGRAPHIC_WATER,
                        "class_name": CLASS_NAMES[CLASS_CARTOGRAPHIC_WATER],
                        "confidence": "map",
                        "area_m2": float(geom_area_m2),
                        "pixels": pixels,
                        "mean_score": float(np.nanmean(score_window[window])),
                        "mean_hh_db": float(np.nanmean(hh_window[window])),
                        "mean_hv_db": float(np.nanmean(hv_window[window])),
                        "mean_slope": mean_slope,
                        "known_water": int(np.any(known_water[row0:row1, col0:col1] & window)),
                        "paddy": int(np.any(paddy[row0:row1, col0:col1] & window)),
                        "review_flag": 1,
                    },
                }
                if CLASS_CARTOGRAPHIC_WATER in handles:
                    handles[CLASS_CARTOGRAPHIC_WATER].write(feature)
                if CLASS_CARTOGRAPHIC_WATER in shp_handles:
                    shp_handles[CLASS_CARTOGRAPHIC_WATER].write(feature)
                counts[CLASS_NAMES[CLASS_CARTOGRAPHIC_WATER]] += 1
    finally:
        for handle in list(handles.values()) + list(shp_handles.values()):
            handle.close()
    return {
        "gpkg_path": str(gpkg_path) if gpkg_path is not None else None,
        "shp_dir": str(shp_dir) if shp_dir is not None else None,
        "layers": layers,
        "feature_counts": counts,
        "min_area_m2": float(min_area_m2),
        "simplify_meters": float(simplify_meters),
        "smooth_meters": float(smooth_meters),
        "min_hole_area_m2": float(min_hole_area_m2),
    }
