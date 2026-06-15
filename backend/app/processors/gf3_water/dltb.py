"""DLTB land-use prior definitions.

DLTB should be treated as a soft background layer for flood-period mapping:
it can change interpretation and confidence, but should not hard-exclude strong
water evidence outside normal water classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path

import numpy as np
from shapely.geometry import box, shape
from shapely.ops import transform as shapely_transform

try:
    import fiona
except Exception:  # pragma: no cover - optional at runtime
    fiona = None

try:
    from pyproj import Transformer
except Exception:  # pragma: no cover - optional at runtime
    Transformer = None


@dataclass(frozen=True)
class DltbConfig:
    gdb: Path
    layer: str = "DLTB"
    field: str = "DLMC"
    mode: str = "soft"
    max_features: int | None = None
    water_names: tuple[str, ...] = dc_field(default_factory=lambda: ("河流水面", "湖泊水面", "水库水面", "坑塘水面", "沟渠"))
    paddy_names: tuple[str, ...] = dc_field(default_factory=lambda: ("水田",))
    strict_names: tuple[str, ...] = dc_field(default_factory=lambda: ("城镇村道路用地", "公路用地", "农村道路", "设施农用地"))


@dataclass
class DltbSceneZones:
    water_geoms: list
    paddy_geoms: list
    strict_geoms: list
    normal_geoms: list
    feature_count: int
    class_counts: dict[str, int]
    dlmc_values: list[str]
    crs: str | None


def classify_dlmc(name: str, config: DltbConfig) -> str:
    """Classify one DLMC value into a soft policy zone."""
    value = (name or "").strip()
    if value in config.water_names:
        return "water_prior"
    if value in config.paddy_names:
        return "paddy"
    if value in config.strict_names:
        return "strict_review"
    return "normal"


def _transform_bounds(bounds: tuple[float, float, float, float], src_crs: str, dst_crs) -> tuple[float, float, float, float]:
    if Transformer is None:
        raise RuntimeError("pyproj is required to use DLTB priors with CRS transformation")
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    left, bottom, right, top = bounds
    xs = [left, left, right, right]
    ys = [bottom, top, bottom, top]
    tx, ty = transformer.transform(xs, ys)
    return min(tx), min(ty), max(tx), max(ty)


def _transform_geom(geom, src_crs, dst_crs: str):
    if Transformer is None:
        raise RuntimeError("pyproj is required to transform DLTB geometries")
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    return shapely_transform(lambda x, y, z=None: transformer.transform(np.asarray(x), np.asarray(y)), geom)


def load_dltb_scene_zones(config: DltbConfig, sar_bounds_wgs84: tuple[float, float, float, float]) -> DltbSceneZones:
    """Load only DLTB features intersecting one SAR scene.

    The input bounds are WGS84 lon/lat. Returned geometries are transformed to
    WGS84 and clipped to the SAR bounds.
    """
    if fiona is None:
        raise RuntimeError("fiona is required for DLTB geodatabase access")
    if not config.gdb.exists():
        raise FileNotFoundError(f"DLTB geodatabase not found: {config.gdb}")

    roi_wgs84 = box(*sar_bounds_wgs84)
    water_geoms = []
    paddy_geoms = []
    strict_geoms = []
    normal_geoms = []
    class_counts = {"water_prior": 0, "paddy": 0, "strict_review": 0, "normal": 0}
    dlmc_values = set()
    feature_count = 0

    with fiona.open(config.gdb, layer=config.layer) as src:
        src_crs = src.crs_wkt or src.crs
        bbox = sar_bounds_wgs84
        if src_crs:
            bbox = _transform_bounds(sar_bounds_wgs84, "EPSG:4326", src_crs)

        for feat in src.filter(bbox=bbox):
            if config.max_features is not None and feature_count >= config.max_features:
                break
            geom_data = feat.get("geometry")
            if not geom_data:
                continue
            geom = shape(geom_data)
            if geom.is_empty:
                continue
            if src_crs:
                geom = _transform_geom(geom, src_crs, "EPSG:4326")
            geom = geom.intersection(roi_wgs84)
            if geom.is_empty:
                continue

            dlmc = str(feat.get("properties", {}).get(config.field, "") or "").strip()
            zone = classify_dlmc(dlmc, config)
            dlmc_values.add(dlmc)
            class_counts[zone] += 1
            feature_count += 1
            if zone == "water_prior":
                water_geoms.append(geom)
            elif zone == "paddy":
                paddy_geoms.append(geom)
            elif zone == "strict_review":
                strict_geoms.append(geom)
            else:
                normal_geoms.append(geom)

    return DltbSceneZones(
        water_geoms=water_geoms,
        paddy_geoms=paddy_geoms,
        strict_geoms=strict_geoms,
        normal_geoms=normal_geoms,
        feature_count=feature_count,
        class_counts=class_counts,
        dlmc_values=sorted(v for v in dlmc_values if v),
        crs=str(src_crs) if "src_crs" in locals() else None,
    )
