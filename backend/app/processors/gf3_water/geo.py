"""Small geospatial math helpers for lon/lat products."""

from __future__ import annotations

import math


def meters_to_degrees(meters: float, lat: float) -> tuple[float, float]:
    deg_lat = meters / 111_320.0
    deg_lon = meters / max(111_320.0 * math.cos(math.radians(lat)), 1.0)
    return deg_lon, deg_lat


def pixel_area_m2(info) -> float:
    center_lat = info.y0 - info.lines * info.dy * 0.5
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(center_lat))
    return abs(info.dx * meters_per_deg_lon * info.dy * meters_per_deg_lat)


def polygon_area_m2(geom, center_lat: float) -> float:
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(center_lat))
    return float(abs(geom.area) * meters_per_deg_lon * meters_per_deg_lat)

