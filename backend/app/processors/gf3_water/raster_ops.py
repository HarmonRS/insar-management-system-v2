"""Raster transforms, thresholding, and morphology."""

from __future__ import annotations

import math

import numpy as np
from scipy import ndimage as ndi


def slope_degrees(dem: np.ndarray, dx_deg: float, dy_deg: float, center_lat: float) -> np.ndarray:
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(center_lat))
    dz_dy, dz_dx = np.gradient(dem.astype(np.float32), dy_deg * meters_per_deg_lat, dx_deg * meters_per_deg_lon)
    slope = np.degrees(np.arctan(np.sqrt(dz_dx * dz_dx + dz_dy * dz_dy)))
    slope[~np.isfinite(slope)] = np.nan
    return slope.astype(np.float32)


def to_db(arr: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    out = np.full(arr.shape, np.nan, dtype=np.float32)
    valid = np.isfinite(arr) & (arr > 0)
    out[valid] = 10.0 * np.log10(arr[valid] + eps)
    return out


def robust_normalize(arr: np.ndarray, valid: np.ndarray, q_low: float = 2.0, q_high: float = 98.0) -> tuple[np.ndarray, float, float]:
    values = arr[valid]
    lo, hi = np.nanpercentile(values, [q_low, q_high])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.nanmin(values)), float(np.nanmax(values))
    norm = np.clip((arr - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    norm[~valid] = np.nan
    return norm.astype(np.float32), float(lo), float(hi)


def otsu_threshold(values: np.ndarray, bins: int = 512) -> float:
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("No finite values for thresholding")
    hist, edges = np.histogram(values, bins=bins)
    centers = (edges[:-1] + edges[1:]) * 0.5
    weight1 = np.cumsum(hist).astype(np.float64)
    weight2 = np.cumsum(hist[::-1]).astype(np.float64)[::-1]
    mean1 = np.cumsum(hist * centers) / np.maximum(weight1, 1.0)
    mean2 = (np.cumsum((hist * centers)[::-1]) / np.maximum(weight2[::-1], 1.0))[::-1]
    variance12 = weight1[:-1] * weight2[1:] * (mean1[:-1] - mean2[1:]) ** 2
    return float(centers[:-1][np.argmax(variance12)])


def remove_small_components(mask: np.ndarray, min_pixels: int) -> np.ndarray:
    if min_pixels <= 1:
        return mask
    labels, count = ndi.label(mask)
    if count == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    keep = sizes >= min_pixels
    keep[0] = False
    return keep[labels]


def fill_small_holes(mask: np.ndarray, max_pixels: int) -> np.ndarray:
    if max_pixels <= 0:
        return mask
    inv = ~mask
    labels, count = ndi.label(inv)
    if count == 0:
        return mask
    border = np.unique(np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]]))
    sizes = np.bincount(labels.ravel())
    fill = sizes <= max_pixels
    fill[border] = False
    out = mask.copy()
    out[fill[labels]] = True
    return out


def disk_structure(radius: int) -> np.ndarray:
    if radius <= 0:
        return np.ones((1, 1), dtype=bool)
    y, x = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    return (x * x + y * y) <= radius * radius


def close_mask(mask: np.ndarray, radius: int, valid: np.ndarray) -> np.ndarray:
    if radius <= 0:
        return mask
    closed = ndi.binary_closing(mask & valid, structure=disk_structure(radius))
    return closed & valid


def open_mask(mask: np.ndarray, radius: int, valid: np.ndarray) -> np.ndarray:
    if radius <= 0:
        return mask
    opened = ndi.binary_opening(mask & valid, structure=disk_structure(radius))
    return opened & valid

