"""Pure GeoTIFF flood detection for the flood-analysis module.

This service deliberately does not depend on ENVI/SARscape. Satellite-specific
preprocessors are responsible only for producing analysis-ready GeoTIFFs; the
flood classification below operates on those GeoTIFFs with Python/rasterio.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import numpy as np


def _valid_mask(data: np.ndarray, nodata: float | int | None) -> np.ndarray:
    valid = np.isfinite(data)
    if nodata is not None and np.isfinite(float(nodata)):
        valid &= data != float(nodata)
    return valid


def _sample_valid(values: np.ndarray, max_samples: int = 1_000_000) -> np.ndarray:
    flat = values[np.isfinite(values)]
    if flat.size <= max_samples:
        return flat
    step = max(1, int(math.ceil(flat.size / max_samples)))
    return flat[::step]


def _otsu_threshold(values: np.ndarray) -> float:
    sample = _sample_valid(values)
    if sample.size < 100:
        raise ValueError("Too few valid pixels for thresholding")
    manual_threshold = _manual_otsu_threshold(sample)
    try:
        from skimage.filters import threshold_otsu

        skimage_threshold = float(threshold_otsu(sample))
        if np.isfinite(skimage_threshold):
            p05, p95 = np.nanpercentile(sample, [5, 95])
            if p05 < skimage_threshold < p95:
                return skimage_threshold
        return manual_threshold
    except Exception:
        return manual_threshold


def _manual_otsu_threshold(values: np.ndarray) -> float:
    sample = _sample_valid(values)
    if sample.size < 100:
        raise ValueError("Too few valid pixels for thresholding")
    vmin = float(np.nanmin(sample))
    vmax = float(np.nanmax(sample))
    if not np.isfinite(vmin) or not np.isfinite(vmax):
        raise ValueError("Input pixels are not finite")
    if math.isclose(vmin, vmax):
        return vmin

    hist, edges = np.histogram(sample, bins=256, range=(vmin, vmax))
    hist = hist.astype("float64")
    centers = (edges[:-1] + edges[1:]) / 2.0
    total = hist.sum()
    if total <= 0:
        return float(np.nanpercentile(sample, 10))

    weight_background = np.cumsum(hist)
    weight_foreground = total - weight_background
    mean_background = np.cumsum(hist * centers) / np.maximum(weight_background, 1e-12)
    mean_foreground = (
        np.cumsum((hist * centers)[::-1]) / np.maximum(np.cumsum(hist[::-1]), 1e-12)
    )[::-1]
    variance = weight_background[:-1] * weight_foreground[:-1] * (
        mean_background[:-1] - mean_foreground[1:]
    ) ** 2
    if variance.size == 0 or not np.isfinite(variance).any():
        return float(np.nanpercentile(sample, 10))
    idx = int(np.nanargmax(variance))
    return float(edges[idx + 1])


def _pixel_area_km2(transform: Any, crs: Any, bounds: Any) -> float:
    px_w = abs(float(transform.a))
    px_h = abs(float(transform.e))
    if crs and getattr(crs, "is_geographic", False):
        lat_center = (float(bounds.top) + float(bounds.bottom)) / 2.0
        px_w_m = px_w * math.cos(math.radians(lat_center)) * 111_320.0
        px_h_m = px_h * 111_320.0
    else:
        px_w_m, px_h_m = px_w, px_h
    return max(0.0, (px_w_m * px_h_m) / 1_000_000.0)


def _clean_mask(mask: np.ndarray, min_pixels: int) -> np.ndarray:
    try:
        from scipy.ndimage import binary_closing, binary_opening, generate_binary_structure, label
    except Exception:
        return mask

    structure = generate_binary_structure(2, 2)
    cleaned = binary_closing(mask, structure=structure, iterations=1)
    cleaned = binary_opening(cleaned, structure=structure, iterations=1)
    if min_pixels <= 1:
        return cleaned

    labels, count = label(cleaned)
    if count <= 0:
        return cleaned
    component_sizes = np.bincount(labels.ravel())
    keep = component_sizes >= int(min_pixels)
    keep[0] = False
    return keep[labels]


def _read_pre_on_post_grid(pre_path: str, post_profile: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    with rasterio.open(pre_path) as pre_ds:
        pre_data = pre_ds.read(1).astype("float32")
        pre_nodata = pre_ds.nodata
        same_grid = (
            pre_ds.width == int(post_profile["width"])
            and pre_ds.height == int(post_profile["height"])
            and pre_ds.transform == post_profile["transform"]
            and str(pre_ds.crs or "") == str(post_profile["crs"] or "")
        )
        metadata = {
            "path": pre_path,
            "crs": pre_ds.crs.to_string() if pre_ds.crs else None,
            "width": pre_ds.width,
            "height": pre_ds.height,
            "nodata": pre_nodata,
            "reprojected_to_post_grid": not same_grid,
        }
        if same_grid:
            data = pre_data.astype("float32")
            if pre_nodata is not None and np.isfinite(float(pre_nodata)):
                data[data == float(pre_nodata)] = np.nan
            return data, metadata
        if not pre_ds.crs or not post_profile["crs"]:
            raise ValueError("Pre/post GeoTIFF CRS is required when grids differ")
        destination = np.full(
            (int(post_profile["height"]), int(post_profile["width"])),
            np.nan,
            dtype="float32",
        )
        reproject(
            source=pre_data,
            destination=destination,
            src_transform=pre_ds.transform,
            src_crs=pre_ds.crs,
            src_nodata=pre_nodata,
            dst_transform=post_profile["transform"],
            dst_crs=post_profile["crs"],
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        return destination, metadata


def run_geotiff_flood_detection(
    *,
    pre_tif_path: str,
    post_tif_path: str,
    output_dir: str,
    job_id: str | None = None,
    refine: bool = False,
) -> dict[str, Any]:
    """Classify stable water and new flood extent from two analysis-ready GeoTIFFs."""
    import rasterio

    pre_path = Path(os.path.normpath(str(pre_tif_path or "").strip()))
    post_path = Path(os.path.normpath(str(post_tif_path or "").strip()))
    out_dir = Path(os.path.normpath(str(output_dir or "").strip()))
    if not pre_path.is_file():
        return {"ok": False, "error": f"Pre-event analysis GeoTIFF not found: {pre_path}"}
    if not post_path.is_file():
        return {"ok": False, "error": f"Post-event analysis GeoTIFF not found: {post_path}"}
    out_dir.mkdir(parents=True, exist_ok=True)

    with rasterio.open(post_path) as post_ds:
        post_data = post_ds.read(1).astype("float32")
        post_nodata = post_ds.nodata
        post_profile = post_ds.profile.copy()
        post_grid = {
            "height": post_ds.height,
            "width": post_ds.width,
            "transform": post_ds.transform,
            "crs": post_ds.crs,
        }
        post_metadata = {
            "path": str(post_path),
            "crs": post_ds.crs.to_string() if post_ds.crs else None,
            "width": post_ds.width,
            "height": post_ds.height,
            "nodata": post_nodata,
        }
        pixel_area_km2 = _pixel_area_km2(post_ds.transform, post_ds.crs, post_ds.bounds)

    pre_data, pre_metadata = _read_pre_on_post_grid(str(pre_path), post_grid)
    valid_pre = _valid_mask(pre_data, None)
    valid_post = _valid_mask(post_data, post_nodata)
    valid = valid_pre & valid_post
    if int(np.count_nonzero(valid)) < 100:
        return {"ok": False, "error": "Too few overlapping valid pixels between pre/post GeoTIFFs"}

    pre_valid_values = pre_data[valid]
    post_valid_values = post_data[valid]
    pre_threshold = _otsu_threshold(pre_valid_values)
    post_threshold = _otsu_threshold(post_valid_values)

    pre_water = (pre_data <= pre_threshold) & valid
    post_water = (post_data <= post_threshold) & valid
    stable_water = pre_water & post_water
    flood = post_water & ~pre_water

    if refine:
        min_pixels = max(4, int(round(3_000.0 / max(pixel_area_km2 * 1_000_000.0, 1.0))))
        stable_water = _clean_mask(stable_water, min_pixels=min_pixels)
        flood = _clean_mask(flood, min_pixels=min_pixels)

    high_threshold = float(np.nanpercentile(post_valid_values, 98))
    high_backscatter = (post_data >= high_threshold) & valid & ~(stable_water | flood)

    classified = np.zeros(post_data.shape, dtype="uint8")
    classified[valid] = 4
    classified[high_backscatter] = 3
    classified[stable_water] = 1
    classified[flood] = 2

    classified_path = out_dir / "classified.tif"
    flood_mask_path = out_dir / "flood_mask.tif"
    stable_mask_path = out_dir / "stable_water_mask.tif"

    classified_profile = post_profile.copy()
    classified_profile.update(
        driver="GTiff",
        dtype="uint8",
        count=1,
        nodata=0,
        compress="deflate",
    )
    with rasterio.open(classified_path, "w", **classified_profile) as dst:
        dst.write(classified, 1)
        try:
            dst.write_colormap(
                1,
                {
                    0: (0, 0, 0, 0),
                    1: (24, 144, 255, 255),
                    2: (255, 77, 79, 255),
                    3: (250, 173, 20, 255),
                    4: (80, 80, 80, 255),
                },
            )
        except Exception:
            pass

    mask_profile = classified_profile.copy()
    mask_profile.update(nodata=0)
    with rasterio.open(flood_mask_path, "w", **mask_profile) as dst:
        dst.write(np.where(flood, 255, 0).astype("uint8"), 1)
    with rasterio.open(stable_mask_path, "w", **mask_profile) as dst:
        dst.write(np.where(stable_water, 255, 0).astype("uint8"), 1)

    flood_pixels = int(np.count_nonzero(flood))
    stable_pixels = int(np.count_nonzero(stable_water))
    high_pixels = int(np.count_nonzero(high_backscatter))
    non_water_pixels = int(np.count_nonzero(classified == 4))
    metadata = {
        "schema": "flood_detection_geotiff.v1",
        "job_id": job_id,
        "processor": "python_geotiff_otsu_change",
        "refine": bool(refine),
        "pre": pre_metadata,
        "post": post_metadata,
        "thresholds": {
            "pre_water_threshold": pre_threshold,
            "post_water_threshold": post_threshold,
            "post_high_backscatter_threshold": high_threshold,
        },
        "pixel_area_km2": pixel_area_km2,
        "class_values": {
            "0": "nodata",
            "1": "stable_water",
            "2": "flood",
            "3": "high_backscatter",
            "4": "non_water",
        },
        "counts": {
            "valid_pixels": int(np.count_nonzero(valid)),
            "stable_water_pixels": stable_pixels,
            "flood_pixels": flood_pixels,
            "high_backscatter_pixels": high_pixels,
            "non_water_pixels": non_water_pixels,
        },
        "outputs": {
            "classified_path": str(classified_path),
            "flood_mask_path": str(flood_mask_path),
            "stable_water_mask_path": str(stable_mask_path),
        },
    }
    metadata_path = out_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return {
        "ok": True,
        "classified_path": str(classified_path),
        "flood_mask_path": str(flood_mask_path),
        "stable_water_mask_path": str(stable_mask_path),
        "metadata_path": str(metadata_path),
        "flood_area_km2": round(flood_pixels * pixel_area_km2, 4),
        "stable_water_area_km2": round(stable_pixels * pixel_area_km2, 4),
        "flood_pixel_count": flood_pixels,
        "stable_water_pixel_count": stable_pixels,
        "processor": "python_geotiff_otsu_change",
        "log": [
            "pre/post analysis-ready GeoTIFFs loaded",
            "pre scene reprojected to post-event grid",
            f"thresholds pre={pre_threshold:.4f}, post={post_threshold:.4f}",
            f"flood_pixels={flood_pixels}, stable_water_pixels={stable_pixels}",
        ],
    }
