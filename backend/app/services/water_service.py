"""Water body monitoring service v2 — SARscape-based pipeline.

Single-scene preprocessing:
  SARsBasicMultilooking  → multi-look intensity image
  SARsBasicGeocoding     → geocoded + calibrated dB image

Flood detection (two-scene pair):
  SARsBasicFeFloodingClassification          → flood classification map
  SARsBasicFeFloodingClassificationRefinement → MRF refinement (optional)
"""
from __future__ import annotations

import os
import time
from glob import glob
from typing import Any, Dict, Optional

from .envi_service import (
    DEM_BASE_FILE,
    CUSTOM_GEOCODING_PIXEL_SIZE_M,
    CUSTOM_TARGET_RESOLUTION_M,
    RUNTIME_DIR,
    _build_sarscapedata,
    _normalize_path,
    _to_local_path,
    _unwrap_sarscapedata,
    _write_progress,
    execute_envi_task,
)

# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

from ..config import settings

WATER_RESULTS_DIR: str = settings.WATER_RESULTS_DIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_slc_base(data_dir: str) -> Optional[str]:
    """Find the SLC ENVI file base path (without extension) in data_dir.

    Looks for files matching *_slc (no extension, ENVI format with .hdr/.sml).
    Returns the base path (without extension) or None.
    """
    data_dir = _to_local_path(data_dir)
    # SARscape SLC files have no extension but have a .hdr and .sml companion
    for fname in os.listdir(data_dir):
        if fname.endswith("_slc") and os.path.isfile(os.path.join(data_dir, fname + ".hdr")):
            return os.path.join(data_dir, fname)
    # Fallback: look for .sml files whose base ends with _slc
    smls = glob(os.path.join(data_dir, "*_slc.sml"))
    if smls:
        return smls[0][:-4]  # strip .sml
    return None


def _find_geo_db_output(output_dir: str) -> Optional[str]:
    """Find the geocoded dB output file base path produced by SARsBasicGeocoding.

    SARscape names the output with a _geo_db or _geo suffix.
    Returns base path (without extension) or None.
    """
    output_dir = _to_local_path(output_dir)
    if not os.path.isdir(output_dir):
        return None
    candidates = []
    for fname in os.listdir(output_dir):
        fpath = os.path.join(output_dir, fname)
        if os.path.isfile(fpath) and fname.endswith(".hdr"):
            base = fpath[:-4]
            if "_geo_db" in fname or "_geo" in fname:
                candidates.append(base)
    if candidates:
        # prefer _geo_db over _geo
        db_candidates = [c for c in candidates if "_geo_db" in c]
        return (db_candidates or candidates)[0]
    return None


# ---------------------------------------------------------------------------
# Single-scene geocoding workflow
# ---------------------------------------------------------------------------

def _find_tiff_file(data_dir: str) -> Optional[str]:
    """Find the LuTan-1 .meta.xml file for SARsImportLuTan1 input."""
    data_dir = _to_local_path(data_dir)
    metas = glob(os.path.join(data_dir, "*.meta.xml"))
    if metas:
        return metas[0]
    # Fallback: raw tiff (older layout)
    for fname in os.listdir(data_dir):
        if fname.lower().endswith(".tiff") or fname.lower().endswith(".tif"):
            return os.path.join(data_dir, fname)
    return None


def run_geocoding_workflow(
    file_path: str,
    output_dir: str,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run multilooking + geocoding on a single SAR SLC scene.

    Args:
        file_path: Path to the radar data directory (contains *_slc file).
        output_dir: Directory to write outputs into.
        job_id: Optional job ID for progress reporting.

    Returns:
        {"ok": True, "geo_path": "...", "pixel_size_m": 10.0}
        or {"ok": False, "error": "..."}
    """
    log: list[str] = []
    file_path = _to_local_path(file_path)
    output_dir = _to_local_path(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    # --- Find SLC file (already imported) or TIFF (needs import first) ---
    slc_base = _find_slc_base(file_path)
    if not slc_base:
        # Need to import from TIFF first
        tiff_file = _find_tiff_file(file_path)
        if not tiff_file:
            return {"ok": False, "error": f"No SLC or TIFF file found in {file_path}"}
        log.append(f"[water] TIFF found, running import: {tiff_file}")
        _write_progress(job_id, 1, 3, "Importing LuTan-1 data", output_dir)
        t0 = time.time()
        try:
            r0 = execute_envi_task(
                "SARsImportLuTan1",
                {
                    "INPUT_FILE_LIST": [tiff_file],
                    "RENAME_THE_FILE_USING_PARAMETERS": True,
                    "APPLY_CALIBRATION_CONSTANT": True,
                    "GENERATE_QL": False,
                    "ROOT_URI_FOR_OUTPUT": _normalize_path(file_path),
                },
            )
            log.append(f"[water] import ok ({round(time.time() - t0, 1)}s)")
        except Exception as exc:
            return {"ok": False, "error": f"Import failed: {exc}", "log": log}

        # After import, find the generated _slc file
        slc_base = _find_slc_base(file_path)
        if not slc_base:
            # Also check output from task result
            imported = _unwrap_sarscapedata(r0.get("OUTPUT_SARSCAPEDATA"))
            if isinstance(imported, list) and imported:
                imported = imported[0]
            if isinstance(imported, dict):
                slc_base = _to_local_path(imported.get("url", "")) or None
        if not slc_base:
            return {"ok": False, "error": "Import produced no SLC file", "log": log}
        total_steps = 3
        step_offset = 1
    else:
        total_steps = 2
        step_offset = 0

    log.append(f"[water] SLC base: {slc_base}")
    slc_sd = _build_sarscapedata(slc_base)

    # --- Multilooking ---
    _write_progress(job_id, 1 + step_offset, total_steps, "Multilooking", output_dir)
    log.append(f"[water] step {1 + step_offset}/{total_steps}: SARsBasicMultilooking")
    t0 = time.time()
    try:
        r1 = execute_envi_task(
            "SARsBasicMultilooking",
            {
                "INPUT_SARSCAPEDATA": [slc_sd],
                "GRID_SIZE_FOR_SUGGESTED_LOOKS": float(CUSTOM_TARGET_RESOLUTION_M),
                "ROOT_URI_FOR_OUTPUT": _normalize_path(output_dir),
            },
        )
        log.append(f"[water] multilooking ok ({round(time.time() - t0, 1)}s)")
    except Exception as exc:
        return {"ok": False, "error": f"Multilooking failed: {exc}", "log": log}

    mli_sd = _unwrap_sarscapedata(r1.get("OUTPUT_SARSCAPEDATA"))
    if not mli_sd:
        return {"ok": False, "error": "Multilooking produced no output", "log": log}
    log.append(f"[water] multilooking output: {mli_sd.get('url', '?')}")

    # --- Geocoding + Radiometric Calibration ---
    _write_progress(job_id, 2 + step_offset, total_steps, "Geocoding & Calibration", output_dir)
    log.append(f"[water] step {2 + step_offset}/{total_steps}: SARsBasicGeocoding")
    t0 = time.time()

    geo_params: Dict[str, Any] = {
        "INPUT_SARSCAPEDATA": [mli_sd],
        "GEOCODE_GRID_SIZE_X": float(CUSTOM_GEOCODING_PIXEL_SIZE_M),
        "GEOCODE_GRID_SIZE_Y": float(CUSTOM_GEOCODING_PIXEL_SIZE_M),
        "CALIBRATION": True,
        "OUTPUT_TYPE": "output_type_db",
        "ROOT_URI_FOR_OUTPUT": _normalize_path(output_dir),
    }
    if DEM_BASE_FILE and os.path.isfile(DEM_BASE_FILE + ".hdr"):
        geo_params["DEM_SARSCAPEDATA"] = _build_sarscapedata(DEM_BASE_FILE)

    try:
        r2 = execute_envi_task("SARsBasicGeocoding", geo_params)
        log.append(f"[water] step 2 ok ({round(time.time() - t0, 1)}s)")
    except Exception as exc:
        return {"ok": False, "error": f"Geocoding failed: {exc}", "log": log}

    # Try to get output path from task result
    geo_sd = _unwrap_sarscapedata(
        r2.get("OUTPUT_DB_SARSCAPEDATA") or r2.get("OUTPUT_SARSCAPEDATA")
    )
    if isinstance(geo_sd, list) and geo_sd:
        geo_sd = geo_sd[0]

    geo_path: Optional[str] = None
    if isinstance(geo_sd, dict):
        geo_path = _to_local_path(geo_sd.get("url", "")) or None
    if not geo_path:
        # Fallback: scan output dir for _geo_db file
        geo_path = _find_geo_db_output(output_dir)

    if not geo_path:
        return {"ok": False, "error": "Geocoding produced no output file", "log": log}

    log.append(f"[water] geo output: {geo_path}")
    return {
        "ok": True,
        "geo_path": geo_path,
        "pixel_size_m": CUSTOM_GEOCODING_PIXEL_SIZE_M,
        "log": log,
    }


# ---------------------------------------------------------------------------
# Flood detection workflow
# ---------------------------------------------------------------------------

def run_flood_detection(
    pre_geo_path: str,
    post_geo_path: str,
    output_dir: str,
    job_id: Optional[str] = None,
    refine: bool = False,
) -> Dict[str, Any]:
    """Run flood classification on a pre/post event geocoded dB image pair.

    Args:
        pre_geo_path: Base path of pre-event geocoded dB image (no extension).
        post_geo_path: Base path of post-event geocoded dB image (no extension).
        output_dir: Directory to write outputs into.
        job_id: Optional job ID for progress reporting.
        refine: Whether to run MRF refinement after classification.

    Returns:
        {"ok": True, "classified_path": "...", "flood_area_km2": ..., "stable_water_area_km2": ...}
        or {"ok": False, "error": "..."}
    """
    log: list[str] = []
    pre_geo_path = _to_local_path(pre_geo_path)
    post_geo_path = _to_local_path(post_geo_path)
    output_dir = _to_local_path(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    total_steps = 3 if refine else 2
    pre_sd = _build_sarscapedata(pre_geo_path)
    post_sd = _build_sarscapedata(post_geo_path)

    # --- Step 1: Flood Classification ---
    _write_progress(job_id, 1, total_steps, "Flood Classification", output_dir)
    log.append("[water] step 1: SARsBasicFeFloodingClassification")
    t0 = time.time()

    flood_params: Dict[str, Any] = {
        "INPUT_SARSCAPEDATA": [pre_sd],
        "POST_EVENT_FILE": post_sd,
        "ROOT_URI_FOR_OUTPUT": _normalize_path(output_dir),
    }
    if DEM_BASE_FILE and os.path.isfile(DEM_BASE_FILE + ".hdr"):
        flood_params["DEM_FILE"] = _build_sarscapedata(DEM_BASE_FILE)

    try:
        r1 = execute_envi_task("SARsBasicFeFloodingClassification", flood_params)
        log.append(f"[water] step 1 ok ({round(time.time() - t0, 1)}s)")
    except Exception as exc:
        return {"ok": False, "error": f"Flood classification failed: {exc}", "log": log}

    classified_sd = _unwrap_sarscapedata(r1.get("OUTPUT_SARSCAPEDATA"))
    ratio_sd = _unwrap_sarscapedata(r1.get("RATIO_SARSCAPEDATA"))
    pre_out_sd = _unwrap_sarscapedata(r1.get("PRE_EVENT_SARSCAPEDATA"))
    post_out_sd = _unwrap_sarscapedata(r1.get("POST_EVENT_SARSCAPEDATA"))

    if not classified_sd:
        return {"ok": False, "error": "Flood classification produced no output", "log": log}

    classified_path = _to_local_path(
        classified_sd.get("url", "") if isinstance(classified_sd, dict) else ""
    ) or None

    # --- Step 2 (optional): MRF Refinement ---
    if refine and classified_sd and ratio_sd and pre_out_sd and post_out_sd:
        _write_progress(job_id, 2, total_steps, "MRF Refinement", output_dir)
        log.append("[water] step 2: SARsBasicFeFloodingClassificationRefinement")
        t0 = time.time()
        try:
            r2 = execute_envi_task(
                "SARsBasicFeFloodingClassificationRefinement",
                {
                    "PRE_EVENT_FILE": pre_out_sd,
                    "POST_EVENT_FILE": post_out_sd,
                    "CLASSIFIED_FILE": classified_sd,
                    "RATIO_FILE": ratio_sd,
                    "ROOT_URI_FOR_OUTPUT": _normalize_path(output_dir),
                },
            )
            refined_sd = _unwrap_sarscapedata(r2.get("OUTPUT_SARSCAPEDATA"))
            if refined_sd and isinstance(refined_sd, dict):
                classified_path = _to_local_path(refined_sd.get("url", "")) or classified_path
            log.append(f"[water] step 2 ok ({round(time.time() - t0, 1)}s)")
        except Exception as exc:
            log.append(f"[water] step 2 refinement failed (non-fatal): {exc}")

    # --- Step 3: Parse classification statistics ---
    _write_progress(job_id, total_steps, total_steps, "Parsing results", output_dir)
    flood_area_km2, stable_water_area_km2 = _parse_flood_stats(classified_path)
    log.append(
        f"[water] flood={flood_area_km2} km², stable_water={stable_water_area_km2} km²"
    )

    return {
        "ok": True,
        "classified_path": classified_path,
        "flood_area_km2": flood_area_km2,
        "stable_water_area_km2": stable_water_area_km2,
        "log": log,
    }


def _parse_flood_stats(classified_path: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """Read the classified flood map and compute area statistics.

    SARscape flood classification output values:
      0 = no data / background
      1 = stable water (permanent water body)
      2 = flood (new water)
      3 = high scattering point (urban / double bounce)
      4 = non-water

    Returns (flood_area_km2, stable_water_area_km2).
    """
    if not classified_path:
        return None, None
    classified_path = _to_local_path(classified_path)
    if not os.path.isfile(classified_path):
        return None, None
    try:
        import rasterio
        with rasterio.open(classified_path) as ds:
            data = ds.read(1)
            transform = ds.transform
            # Pixel area in m²
            px_w = abs(transform.a)
            px_h = abs(transform.e)
            # If CRS is geographic (degrees), convert to meters approximately
            if ds.crs and ds.crs.is_geographic:
                import math
                lat_center = (ds.bounds.top + ds.bounds.bottom) / 2.0
                px_w_m = px_w * math.cos(math.radians(lat_center)) * 111320
                px_h_m = px_h * 111320
            else:
                px_w_m, px_h_m = px_w, px_h
            pixel_area_km2 = (px_w_m * px_h_m) / 1e6

            flood_pixels = int((data == 2).sum())
            stable_pixels = int((data == 1).sum())
            return (
                round(flood_pixels * pixel_area_km2, 4),
                round(stable_pixels * pixel_area_km2, 4),
            )
    except Exception as exc:
        print(f"[WARN] _parse_flood_stats: {exc}")
        return None, None
