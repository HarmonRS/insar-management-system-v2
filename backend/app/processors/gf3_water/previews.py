"""PNG preview writers for extraction products."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .constants import (
    CLASS_HIGH_CONFIDENCE_WATER,
    CLASS_KNOWN_WATER,
    CLASS_LOW_CONFIDENCE_WATER,
    CLASS_PADDY_WATER_LIKE,
)


def save_gray_png(path: Path, arr: np.ndarray, valid: np.ndarray) -> None:
    out = np.zeros(arr.shape, dtype=np.uint8)
    vals = arr[valid & np.isfinite(arr)]
    if vals.size:
        lo, hi = np.nanpercentile(vals, [2, 98])
        scaled = np.clip((arr - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        out[valid & np.isfinite(arr)] = (scaled[valid & np.isfinite(arr)] * 255).astype(np.uint8)
    Image.fromarray(out).save(path)


def save_mask_png(path: Path, mask: np.ndarray) -> None:
    Image.fromarray(np.where(mask, 255, 0).astype(np.uint8)).save(path)


def save_preview(path: Path, hh_norm: np.ndarray, hv_norm: np.ndarray, mask: np.ndarray, valid: np.ndarray) -> None:
    rgb = np.zeros((*mask.shape, 3), dtype=np.uint8)
    rgb[..., 0] = np.nan_to_num(hh_norm * 255.0, nan=0.0).astype(np.uint8)
    rgb[..., 1] = np.nan_to_num(hv_norm * 255.0, nan=0.0).astype(np.uint8)
    rgb[..., 2] = np.nan_to_num((1.0 - 0.5 * (hh_norm + hv_norm)) * 255.0, nan=0.0).astype(np.uint8)
    rgb[mask] = (0.35 * rgb[mask] + np.array([0, 120, 255]) * 0.65).astype(np.uint8)
    rgb[~valid] = 0
    Image.fromarray(rgb).save(path)


def save_class_preview(path: Path, classified: np.ndarray, hh_norm: np.ndarray, hv_norm: np.ndarray, valid: np.ndarray) -> None:
    rgb = np.zeros((*classified.shape, 3), dtype=np.uint8)
    base = np.nan_to_num((0.55 * hh_norm + 0.45 * hv_norm) * 180.0, nan=0.0).astype(np.uint8)
    rgb[..., 0] = base
    rgb[..., 1] = base
    rgb[..., 2] = base
    colors = {
        CLASS_HIGH_CONFIDENCE_WATER: np.array([0, 92, 230], dtype=np.uint8),
        CLASS_KNOWN_WATER: np.array([0, 170, 255], dtype=np.uint8),
        CLASS_PADDY_WATER_LIKE: np.array([0, 210, 170], dtype=np.uint8),
        CLASS_LOW_CONFIDENCE_WATER: np.array([245, 166, 35], dtype=np.uint8),
    }
    for class_id, color in colors.items():
        idx = classified == class_id
        rgb[idx] = (0.30 * rgb[idx] + 0.70 * color).astype(np.uint8)
    rgb[~valid] = 0
    Image.fromarray(rgb).save(path)


def save_dltb_zone_preview(path: Path, water_mask: np.ndarray, paddy_mask: np.ndarray, strict_mask: np.ndarray) -> None:
    rgb = np.zeros((*water_mask.shape, 3), dtype=np.uint8)
    rgb[water_mask] = (0, 120, 255)
    rgb[paddy_mask] = (0, 210, 170)
    rgb[strict_mask] = (220, 80, 40)
    Image.fromarray(rgb).save(path)

