"""Vector loading and rasterization helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from shapely.geometry import box, shape
from shapely.ops import transform as shapely_transform

from .envi import EnviInfo
from .geo import meters_to_degrees

try:
    import fiona
except Exception:  # pragma: no cover - optional at runtime
    fiona = None

try:
    from rasterio.features import rasterize
except Exception:  # pragma: no cover - optional at runtime
    rasterize = None


def load_vector_geometries(paths: list[Path], bounds: tuple[float, float, float, float], line_buffer_meters: float) -> list:
    if not paths:
        return []
    if fiona is None:
        raise RuntimeError("fiona is required for vector inputs")

    roi = box(*bounds)
    center_lat = (bounds[1] + bounds[3]) * 0.5
    buffer_lon, buffer_lat = meters_to_degrees(max(line_buffer_meters, 0.1), center_lat)
    search_roi = box(bounds[0] - buffer_lon, bounds[1] - buffer_lat, bounds[2] + buffer_lon, bounds[3] + buffer_lat)
    geoms = []

    for path in paths:
        with fiona.open(path) as src:
            for feat in src:
                if not feat.get("geometry"):
                    continue
                geom = shape(feat["geometry"])
                if geom.is_empty or not geom.intersects(search_roi):
                    continue
                if geom.geom_type in ("LineString", "MultiLineString"):
                    geom = shapely_transform(lambda x, y, z=None: (np.asarray(x) / buffer_lon, np.asarray(y) / buffer_lat), geom)
                    geom = geom.buffer(1.0)
                    geom = shapely_transform(lambda x, y, z=None: (np.asarray(x) * buffer_lon, np.asarray(y) * buffer_lat), geom)
                geom = geom.intersection(search_roi)
                if not geom.is_empty and geom.intersects(roi):
                    geoms.append(geom)
    return geoms


def rasterize_vector_mask(paths: list[Path], info: EnviInfo, line_buffer_meters: float, label: str) -> np.ndarray:
    if rasterize is None:
        raise RuntimeError(f"rasterio.features.rasterize is required for {label}")
    geoms = load_vector_geometries(paths, info.bounds, line_buffer_meters)
    if not geoms:
        return np.zeros((info.lines, info.samples), dtype=bool)
    return rasterize(
        [(geom, 1) for geom in geoms],
        out_shape=(info.lines, info.samples),
        transform=info.transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    ).astype(bool)


def rasterize_water_prior(paths: list[Path], info: EnviInfo, river_buffer_meters: float) -> np.ndarray:
    return rasterize_vector_mask(paths, info, river_buffer_meters, "--water-vector")


def rasterize_geometries(geoms: list, info: EnviInfo) -> np.ndarray:
    if rasterize is None:
        raise RuntimeError("rasterio.features.rasterize is required to rasterize geometry priors")
    if not geoms:
        return np.zeros((info.lines, info.samples), dtype=bool)
    return rasterize(
        [(geom, 1) for geom in geoms if not geom.is_empty],
        out_shape=(info.lines, info.samples),
        transform=info.transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    ).astype(bool)

