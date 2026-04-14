"""
Water body detection service — Otsu adaptive threshold + DEM/slope constraints
+ morphological filtering + connected component analysis.

Python reimplementation of MATLAB WaterDetectProcess.m.
"""
from __future__ import annotations

import logging
import math
import os
import struct
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SRTM HGT helpers
# ---------------------------------------------------------------------------

def _read_hgt(filepath: str) -> np.ndarray:
    """Read a single SRTM .hgt file. Auto-detect SRTM1 (3601) vs SRTM3 (1201)."""
    file_size = os.path.getsize(filepath)
    if file_size == 3601 * 3601 * 2:
        size = 3601  # SRTM1
    elif file_size == 1201 * 1201 * 2:
        size = 1201  # SRTM3
    else:
        raise ValueError(f"Unexpected HGT file size: {file_size} bytes ({filepath})")

    with open(filepath, "rb") as f:
        raw = f.read()
    data = np.frombuffer(raw, dtype=">i2").reshape((size, size)).astype(np.float32)
    # SRTM void value
    data[data == -32768] = np.nan
    return data


def _load_srtm3_dem(
    bounds: Tuple[float, float, float, float],
    dem_dir: str,
) -> Tuple[Optional[np.ndarray], Optional[Tuple[float, float, float, float]]]:
    """Load and mosaic SRTM HGT tiles covering *bounds* (min_lon, min_lat, max_lon, max_lat).

    Returns (dem_array, (dem_min_lon, dem_min_lat, dem_max_lon, dem_max_lat)) or (None, None).
    """
    min_lon, min_lat, max_lon, max_lat = bounds

    lat_start = int(math.floor(min_lat))
    lat_end = int(math.floor(max_lat))
    lon_start = int(math.floor(min_lon))
    lon_end = int(math.floor(max_lon))

    tiles: Dict[Tuple[int, int], np.ndarray] = {}
    tile_size = None

    for lat in range(lat_start, lat_end + 1):
        for lon in range(lon_start, lon_end + 1):
            ns = "N" if lat >= 0 else "S"
            ew = "E" if lon >= 0 else "W"
            fname = f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}.hgt"
            fpath = os.path.join(dem_dir, fname)
            if not os.path.isfile(fpath):
                logger.warning("SRTM tile not found: %s", fpath)
                continue
            tile = _read_hgt(fpath)
            tiles[(lat, lon)] = tile
            tile_size = tile.shape[0]

    if not tiles or tile_size is None:
        logger.warning("No SRTM tiles found for bounds %s in %s", bounds, dem_dir)
        return None, None

    n_lats = lat_end - lat_start + 1
    n_lons = lon_end - lon_start + 1
    # Each tile is (tile_size x tile_size), tiles overlap by 1 pixel on edges
    effective = tile_size - 1
    mosaic_h = n_lats * effective + 1
    mosaic_w = n_lons * effective + 1
    mosaic = np.full((mosaic_h, mosaic_w), np.nan, dtype=np.float32)

    for (lat, lon), tile in tiles.items():
        row_offset = (lat_end - lat) * effective  # top = highest lat
        col_offset = (lon - lon_start) * effective
        mosaic[row_offset: row_offset + tile_size, col_offset: col_offset + tile_size] = tile

    dem_bounds = (
        float(lon_start),
        float(lat_start),
        float(lon_end + 1),
        float(lat_end + 1),
    )
    return mosaic, dem_bounds


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def _compute_water_area_km2(mask: np.ndarray, pixel_size_x: float, pixel_size_y: float) -> float:
    """Compute water area in km^2 from boolean mask and pixel sizes in degrees."""
    water_count = int(np.count_nonzero(mask))
    # Approximate at mid-latitude
    lat_km = abs(pixel_size_y) * 111.32
    lon_km = abs(pixel_size_x) * 111.32  # rough approximation
    return water_count * lat_km * lon_km


def run_water_detection(
    geo_tiff_path: str,
    output_dir: str,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run water body detection on a GeoTIFF.

    Returns dict with keys: ok, output_path, water_area_km2, water_pixel_count, otsu_threshold_db
    """
    import rasterio
    from scipy.ndimage import median_filter, gaussian_filter, label, zoom
    from skimage.filters import threshold_otsu
    from skimage.morphology import disk, binary_dilation, binary_erosion
    from skimage.measure import regionprops

    from ..config import settings

    dem_dir = settings.SRTM_DEM_DIR
    os.makedirs(output_dir, exist_ok=True)

    logger.info("[WaterDetect] Reading input: %s", geo_tiff_path)

    # Step 1: Read SAR GeoTIFF
    with rasterio.open(geo_tiff_path) as src:
        img = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs
        height, width = img.shape
        pixel_size_x = transform.a   # degrees per pixel (x)
        pixel_size_y = transform.e   # degrees per pixel (y, negative)

    # Step 2: Compute lon/lat bounds
    min_lon = transform.c
    max_lon = transform.c + width * pixel_size_x
    max_lat = transform.f
    min_lat = transform.f + height * pixel_size_y  # pixel_size_y is negative
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon

    bounds = (min_lon, min_lat, max_lon, max_lat)
    logger.info("[WaterDetect] Image bounds: %s, size: %dx%d", bounds, width, height)

    # Step 5: Valid mask
    valid = np.isfinite(img) & (img != 0)

    if np.count_nonzero(valid) < 100:
        return {"ok": False, "error": "Too few valid pixels in input image"}

    # Step 6: Otsu threshold on valid pixels
    valid_pixels = img[valid]
    thresh = threshold_otsu(valid_pixels)
    logger.info("[WaterDetect] Otsu threshold: %.4f", thresh)

    # Step 7-8: Median + Gaussian filtering
    filtered = median_filter(img, size=3)
    filtered = gaussian_filter(filtered, sigma=1.0)

    # Step 9: Initial water mask
    water = (filtered < thresh) & valid

    # Step 3-4: Load and resample DEM (if available)
    dem_applied = False
    if dem_dir and os.path.isdir(dem_dir):
        dem, dem_bounds = _load_srtm3_dem(bounds, dem_dir)
        if dem is not None and dem_bounds is not None:
            # Resample DEM to image resolution
            zoom_y = height / dem.shape[0]
            zoom_x = width / dem.shape[1]
            dem_resampled = zoom(dem, (zoom_y, zoom_x), order=1)
            # Clip to match image shape exactly
            dem_resampled = dem_resampled[:height, :width]

            # Step 10: DEM height constraint (0m <= DEM <= 1000m)
            dem_valid = np.isfinite(dem_resampled)
            height_mask = dem_valid & (dem_resampled >= 0) & (dem_resampled <= 1000)
            water = water & height_mask

            # Step 11: Slope constraint — exclude slope > tan(60 deg)
            slope_threshold = math.tan(math.radians(60))
            dy, dx = np.gradient(dem_resampled)
            # Convert gradient from pixels to approximate meters
            m_per_pixel_y = abs(pixel_size_y) * 111320
            m_per_pixel_x = abs(pixel_size_x) * 111320 * math.cos(math.radians((min_lat + max_lat) / 2))
            slope_y = dy / max(m_per_pixel_y, 1)
            slope_x = dx / max(m_per_pixel_x, 1)
            slope = np.sqrt(slope_y ** 2 + slope_x ** 2)
            gentle_slope = slope < slope_threshold
            water = water & gentle_slope
            dem_applied = True
            logger.info("[WaterDetect] DEM constraints applied")
        else:
            logger.warning("[WaterDetect] DEM not available, skipping DEM constraints")
    else:
        logger.warning("[WaterDetect] SRTM_DEM_DIR not configured, skipping DEM constraints")

    # Step 12: Morphological processing — disk(5) dilate→erode→dilate→erode
    selem = disk(5)
    water = binary_dilation(water, selem)
    water = binary_erosion(water, selem)
    water = binary_dilation(water, selem)
    water = binary_erosion(water, selem)

    # Step 13: Connected component filtering
    labeled, num_features = label(water)
    if num_features > 0:
        # Compute min_area: max(3000m² / pixel_area_m², median_area)
        pixel_area_m2 = abs(pixel_size_x) * 111320 * abs(pixel_size_y) * 111320
        min_pixels_by_area = max(1, int(3000 / max(pixel_area_m2, 1)))

        props = regionprops(labeled)
        areas = [p.area for p in props]
        if areas:
            median_area = float(np.median(areas))
            min_area = max(min_pixels_by_area, int(median_area))
        else:
            min_area = min_pixels_by_area

        # Remove small components
        for prop in props:
            if prop.area < min_area:
                water[labeled == prop.label] = False
        logger.info("[WaterDetect] Connected component filter: min_area=%d pixels, kept %d/%d components",
                     min_area, np.count_nonzero(np.unique(labeled[water])), num_features)

    # Step 14: Output binary mask GeoTIFF (0/255)
    output_path = os.path.join(output_dir, "water_mask.tif")
    mask_uint8 = np.where(water, 255, 0).astype(np.uint8)

    with rasterio.open(
        output_path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="uint8",
        crs=crs,
        transform=transform,
        compress="deflate",
    ) as dst:
        dst.write(mask_uint8, 1)

    water_pixel_count = int(np.count_nonzero(water))
    water_area = _compute_water_area_km2(water, pixel_size_x, pixel_size_y)

    logger.info("[WaterDetect] Done: water_pixels=%d, area=%.3f km², output=%s",
                water_pixel_count, water_area, output_path)

    return {
        "ok": True,
        "output_path": output_path,
        "water_area_km2": round(water_area, 4),
        "water_pixel_count": water_pixel_count,
        "otsu_threshold_db": round(float(thresh), 4),
    }
