"""High-level GF-3 HH/HV water extraction pipeline."""

from __future__ import annotations

import json
from argparse import Namespace

import numpy as np

from .constants import (
    CLASS_HIGH_CONFIDENCE_WATER,
    CLASS_INVALID,
    CLASS_KNOWN_WATER,
    CLASS_LOW_CONFIDENCE_WATER,
    CLASS_NAMES,
    CLASS_NON_WATER,
    CLASS_PADDY_WATER_LIKE,
)
from .dltb import DltbConfig, load_dltb_scene_zones
from .envi import parse_envi, read_dem_for_sar, read_envi_band, write_tif
from .previews import save_class_preview, save_dltb_zone_preview, save_gray_png, save_mask_png, save_preview
from .raster_ops import close_mask, fill_small_holes, open_mask, otsu_threshold, remove_small_components, robust_normalize, slope_degrees, to_db
from .vector_io import rasterize_geometries, rasterize_vector_mask, rasterize_water_prior
from .vector_products import write_classified_vectors


def _load_dltb_masks(args: Namespace, info) -> tuple[dict | None, np.ndarray, np.ndarray, np.ndarray]:
    water_mask = np.zeros((info.lines, info.samples), dtype=bool)
    paddy_mask = np.zeros((info.lines, info.samples), dtype=bool)
    strict_mask = np.zeros((info.lines, info.samples), dtype=bool)

    if args.dltb_cache_dir is not None and args.dltb_mode != "off":
        water_cache = args.dltb_cache_dir / "water_prior.shp"
        paddy_cache = args.dltb_cache_dir / "paddy.shp"
        strict_cache = args.dltb_cache_dir / "strict_review.shp"
        if water_cache.exists():
            water_mask = rasterize_vector_mask([water_cache], info, 0.0, "--dltb-cache-dir water_prior")
        if paddy_cache.exists():
            paddy_mask = rasterize_vector_mask([paddy_cache], info, 0.0, "--dltb-cache-dir paddy")
        if strict_cache.exists():
            strict_mask = rasterize_vector_mask([strict_cache], info, 0.0, "--dltb-cache-dir strict_review")
        stats = {
            "cache_dir": str(args.dltb_cache_dir),
            "mode": args.dltb_mode,
            "source": "cache",
            "water_prior_path": str(water_cache) if water_cache.exists() else None,
            "paddy_path": str(paddy_cache) if paddy_cache.exists() else None,
            "strict_review_path": str(strict_cache) if strict_cache.exists() else None,
        }
        return stats, water_mask, paddy_mask, strict_mask

    if args.dltb_gdb is not None and args.dltb_mode != "off":
        zones = load_dltb_scene_zones(
            DltbConfig(gdb=args.dltb_gdb, layer=args.dltb_layer, field=args.dltb_field, mode=args.dltb_mode, max_features=args.dltb_max_features),
            info.bounds,
        )
        water_mask = rasterize_geometries(zones.water_geoms, info)
        paddy_mask = rasterize_geometries(zones.paddy_geoms, info)
        strict_mask = rasterize_geometries(zones.strict_geoms, info)
        stats = {
            "gdb": str(args.dltb_gdb),
            "source": "gdb",
            "layer": args.dltb_layer,
            "field": args.dltb_field,
            "mode": args.dltb_mode,
            "source_crs": zones.crs,
            "features_in_scene": zones.feature_count,
            "class_counts": zones.class_counts,
            "dlmc_values_in_scene": zones.dlmc_values,
        }
        return stats, water_mask, paddy_mask, strict_mask

    return None, water_mask, paddy_mask, strict_mask


def _ensure_matching_grids(hh_info, hv_info) -> None:
    hh_grid = (hh_info.samples, hh_info.lines, hh_info.x0, hh_info.y0, hh_info.dx, hh_info.dy)
    hv_grid = (hv_info.samples, hv_info.lines, hv_info.x0, hv_info.y0, hv_info.dx, hv_info.dy)
    if hh_grid != hv_grid:
        raise ValueError("HH and HV grids do not match")


def _classify_products(mask: np.ndarray, low_confidence_candidate: np.ndarray, paddy_candidate: np.ndarray, prior_mask: np.ndarray, valid: np.ndarray) -> np.ndarray:
    high_confidence_mask = mask & ~paddy_candidate
    known_water_mask = mask & prior_mask
    classified = np.full(mask.shape, CLASS_INVALID, dtype=np.uint8)
    classified[valid] = CLASS_NON_WATER
    classified[low_confidence_candidate & valid & ~mask & ~paddy_candidate] = CLASS_LOW_CONFIDENCE_WATER
    classified[high_confidence_mask & valid] = CLASS_HIGH_CONFIDENCE_WATER
    classified[paddy_candidate & valid] = CLASS_PADDY_WATER_LIKE
    classified[known_water_mask & valid] = CLASS_KNOWN_WATER
    return classified


def _write_rasters(
    args: Namespace,
    info,
    probability: np.ndarray,
    valid: np.ndarray,
    mask: np.ndarray,
    raw_mask: np.ndarray,
    classified: np.ndarray,
    cartographic_water: np.ndarray | None,
    prior_mask: np.ndarray,
    paddy_mask: np.ndarray,
    paddy_candidate: np.ndarray,
    dltb_enabled: bool,
    dltb_water_mask: np.ndarray,
    dltb_paddy_mask: np.ndarray,
    dltb_strict_mask: np.ndarray,
) -> None:
    mask_u8 = np.where(valid, mask.astype(np.uint8), 255).astype(np.uint8)
    raw_mask_u8 = np.where(valid, raw_mask.astype(np.uint8), 255).astype(np.uint8)

    write_tif(args.out_dir / "water_score.tif", np.where(np.isfinite(probability), probability, -9999.0), info, "float32", nodata=-9999.0)
    write_tif(args.out_dir / "water_mask.tif", mask_u8, info, "uint8", nodata=255)
    write_tif(args.out_dir / "water_mask_raw.tif", raw_mask_u8, info, "uint8", nodata=255)
    write_tif(args.out_dir / "classified_water.tif", classified, info, "uint8", nodata=CLASS_INVALID)
    if cartographic_water is not None:
        write_tif(args.out_dir / "cartographic_water.tif", np.where(valid, cartographic_water.astype(np.uint8), 255).astype(np.uint8), info, "uint8", nodata=255)
    if args.water_vector:
        write_tif(args.out_dir / "known_water_prior.tif", prior_mask.astype(np.uint8), info, "uint8", nodata=0)
    if args.paddy_vector or dltb_enabled:
        write_tif(args.out_dir / "paddy_prior.tif", paddy_mask.astype(np.uint8), info, "uint8", nodata=0)
        write_tif(args.out_dir / "paddy_water_like.tif", np.where(valid, paddy_candidate.astype(np.uint8), 255).astype(np.uint8), info, "uint8", nodata=255)
    if dltb_enabled:
        write_tif(args.out_dir / "dltb_water_prior.tif", dltb_water_mask.astype(np.uint8), info, "uint8", nodata=0)
        write_tif(args.out_dir / "dltb_paddy_prior.tif", dltb_paddy_mask.astype(np.uint8), info, "uint8", nodata=0)
        write_tif(args.out_dir / "dltb_strict_zone.tif", dltb_strict_mask.astype(np.uint8), info, "uint8", nodata=0)


def _write_previews(
    args: Namespace,
    probability: np.ndarray,
    valid: np.ndarray,
    mask: np.ndarray,
    raw_mask: np.ndarray,
    hh_norm: np.ndarray,
    hv_norm: np.ndarray,
    classified: np.ndarray,
    prior_mask: np.ndarray,
    paddy_candidate: np.ndarray,
    dltb_enabled: bool,
    dltb_water_mask: np.ndarray,
    dltb_paddy_mask: np.ndarray,
    dltb_strict_mask: np.ndarray,
    cartographic_water: np.ndarray | None,
) -> None:
    save_gray_png(args.out_dir / "water_score.png", probability, valid)
    save_mask_png(args.out_dir / "water_mask.png", mask)
    save_mask_png(args.out_dir / "water_mask_raw.png", raw_mask)
    if args.water_vector:
        save_mask_png(args.out_dir / "known_water_prior.png", prior_mask)
    if args.paddy_vector or dltb_enabled:
        save_mask_png(args.out_dir / "paddy_water_like.png", paddy_candidate)
    if dltb_enabled:
        save_dltb_zone_preview(args.out_dir / "dltb_zone_preview.png", dltb_water_mask, dltb_paddy_mask, dltb_strict_mask)
    if cartographic_water is not None:
        save_mask_png(args.out_dir / "cartographic_water.png", cartographic_water)
    save_preview(args.out_dir / "preview_overlay.png", hh_norm, hv_norm, mask, valid)
    save_class_preview(args.out_dir / "classified_preview.png", classified, hh_norm, hv_norm, valid)


def run_from_args(args: Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)

    hh_info = parse_envi(args.hh)
    hv_info = parse_envi(args.hv)
    _ensure_matching_grids(hh_info, hv_info)

    dltb_stats, dltb_water_mask, dltb_paddy_mask, dltb_strict_mask = _load_dltb_masks(args, hh_info)
    dltb_enabled = dltb_stats is not None

    hh = read_envi_band(hh_info)
    hv = read_envi_band(hv_info)
    valid = np.isfinite(hh) & np.isfinite(hv) & (hh > 0) & (hv > 0)

    hh_db = to_db(hh)
    hv_db = to_db(hv)
    valid &= np.isfinite(hh_db) & np.isfinite(hv_db)
    hh_norm, hh_lo, hh_hi = robust_normalize(hh_db, valid)
    hv_norm, hv_lo, hv_hi = robust_normalize(hv_db, valid)

    low_backscatter = 1.0 - 0.5 * (hh_norm + hv_norm)
    low_backscatter[~valid] = np.nan

    values = low_backscatter[valid]
    if args.threshold_method == "otsu":
        score_threshold = otsu_threshold(values)
    else:
        score_threshold = float(np.nanpercentile(values, args.score_percentile))
    hv_dark_threshold = float(np.nanpercentile(hv_norm[valid], args.hv_percentile))

    mask = (low_backscatter >= score_threshold) & (hv_norm <= hv_dark_threshold) & valid
    prior_mask = np.zeros(mask.shape, dtype=bool)
    prior_candidate_pixels = 0
    prior_score_threshold = None
    prior_hv_threshold = None
    if args.water_vector:
        prior_mask = rasterize_water_prior(args.water_vector, hh_info, args.river_buffer_meters) & valid
        prior_score_threshold = float(np.nanpercentile(values, args.prior_score_percentile))
        prior_hv_threshold = float(np.nanpercentile(hv_norm[valid], args.prior_hv_percentile))
        prior_candidate = prior_mask & (low_backscatter >= prior_score_threshold) & (hv_norm <= prior_hv_threshold)
        prior_candidate_pixels = int(prior_candidate.sum())
        mask |= prior_candidate
    if dltb_enabled:
        prior_mask |= dltb_water_mask & valid
        prior_score_threshold = prior_score_threshold if prior_score_threshold is not None else float(np.nanpercentile(values, args.prior_score_percentile))
        prior_hv_threshold = prior_hv_threshold if prior_hv_threshold is not None else float(np.nanpercentile(hv_norm[valid], args.prior_hv_percentile))
        dltb_prior_candidate = prior_mask & (low_backscatter >= prior_score_threshold) & (hv_norm <= prior_hv_threshold)
        prior_candidate_pixels += int((dltb_water_mask & dltb_prior_candidate).sum())
        mask |= dltb_prior_candidate

    paddy_mask = np.zeros(mask.shape, dtype=bool)
    paddy_candidate = np.zeros(mask.shape, dtype=bool)
    paddy_score_threshold = None
    paddy_hv_threshold = None
    if args.paddy_vector:
        paddy_mask = rasterize_vector_mask(args.paddy_vector, hh_info, args.paddy_buffer_meters, "--paddy-vector") & valid
        paddy_score_threshold = float(np.nanpercentile(values, args.paddy_score_percentile))
        paddy_hv_threshold = float(np.nanpercentile(hv_norm[valid], args.paddy_hv_percentile))
        paddy_candidate = paddy_mask & (low_backscatter >= paddy_score_threshold) & (hv_norm <= paddy_hv_threshold)
    if dltb_enabled:
        paddy_mask |= dltb_paddy_mask & valid
        paddy_score_threshold = paddy_score_threshold if paddy_score_threshold is not None else float(np.nanpercentile(values, args.paddy_score_percentile))
        paddy_hv_threshold = paddy_hv_threshold if paddy_hv_threshold is not None else float(np.nanpercentile(hv_norm[valid], args.paddy_hv_percentile))
        paddy_candidate |= paddy_mask & (low_backscatter >= paddy_score_threshold) & (hv_norm <= paddy_hv_threshold)

    candidate_score_threshold = float(np.nanpercentile(values, args.candidate_score_percentile))
    candidate_hv_threshold = float(np.nanpercentile(hv_norm[valid], args.candidate_hv_percentile))
    low_confidence_candidate = (low_backscatter >= candidate_score_threshold) & (hv_norm <= candidate_hv_threshold) & valid
    if dltb_enabled and args.dltb_mode == "soft":
        strong_candidate = (low_backscatter >= score_threshold) & (hv_norm <= hv_dark_threshold) & valid
        low_confidence_candidate &= ~dltb_strict_mask | strong_candidate
    elif dltb_enabled and args.dltb_mode == "strict":
        low_confidence_candidate &= ~dltb_strict_mask

    dem_used = False
    slope_threshold = None
    slope = None
    if args.dem is not None:
        dem_info = parse_envi(args.dem)
        dem = read_dem_for_sar(dem_info, hh_info)
        slope = slope_degrees(dem, hh_info.dx, hh_info.dy, center_lat=hh_info.y0 - hh_info.lines * hh_info.dy * 0.5)
        slope_threshold = float(args.slope_max)
        mask &= np.isfinite(slope) & (slope <= args.slope_max)
        paddy_candidate &= np.isfinite(slope) & (slope <= args.slope_max)
        low_confidence_candidate &= np.isfinite(slope) & (slope <= args.slope_max)
        valid &= np.isfinite(slope)
        dem_used = True
        save_gray_png(args.out_dir / "slope_preview.png", slope, np.isfinite(slope))
        write_tif(args.out_dir / "slope_degrees.tif", np.where(np.isfinite(slope), slope, -9999.0), hh_info, "float32", nodata=-9999.0)

    raw_mask = mask.copy()
    if not args.no_morphology:
        mask = close_mask(mask, args.close_pixels, valid)
        mask = remove_small_components(mask, args.min_component_pixels)
        mask = fill_small_holes(mask, args.fill_hole_pixels)
        mask = open_mask(mask, args.open_pixels, valid)
        paddy_candidate = close_mask(paddy_candidate, args.paddy_close_pixels, valid)
        paddy_candidate = open_mask(paddy_candidate, args.paddy_open_pixels, valid)
        low_confidence_candidate = close_mask(low_confidence_candidate, args.candidate_close_pixels, valid)
        low_confidence_candidate = open_mask(low_confidence_candidate, args.candidate_open_pixels, valid)

    probability = np.clip(low_backscatter, 0.0, 1.0)
    probability[~valid] = np.nan
    classified = _classify_products(mask, low_confidence_candidate, paddy_candidate, prior_mask, valid)

    cartographic_water = None
    if args.cartographic_water:
        cartographic_water = (mask | low_confidence_candidate) & valid
        if args.cartographic_include_paddy:
            cartographic_water |= paddy_candidate & valid
        else:
            cartographic_water &= ~paddy_candidate
        if not args.no_morphology:
            cartographic_water = close_mask(cartographic_water, args.cartographic_close_pixels, valid)
            cartographic_water = fill_small_holes(cartographic_water, args.cartographic_fill_hole_pixels)
            cartographic_water = remove_small_components(cartographic_water, args.cartographic_min_component_pixels)
            cartographic_water = open_mask(cartographic_water, args.cartographic_open_pixels, valid)

    _write_rasters(
        args,
        hh_info,
        probability,
        valid,
        mask,
        raw_mask,
        classified,
        cartographic_water,
        prior_mask,
        paddy_mask,
        paddy_candidate,
        dltb_enabled,
        dltb_water_mask,
        dltb_paddy_mask,
        dltb_strict_mask,
    )
    _write_previews(
        args,
        probability,
        valid,
        mask,
        raw_mask,
        hh_norm,
        hv_norm,
        classified,
        prior_mask,
        paddy_candidate,
        dltb_enabled,
        dltb_water_mask,
        dltb_paddy_mask,
        dltb_strict_mask,
        cartographic_water,
    )

    vector_stats = None
    if args.out_vector_gpkg is not None or args.out_vector_shp_dir is not None:
        vector_stats = write_classified_vectors(
            args.out_vector_gpkg,
            args.out_vector_shp_dir,
            classified,
            cartographic_water,
            hh_info,
            probability,
            hh_db,
            hv_db,
            slope,
            prior_mask,
            paddy_mask,
            args.min_polygon_area_m2,
            args.simplify_meters,
            args.smooth_meters,
            args.min_hole_area_m2,
        )

    valid_count = int(valid.sum())
    stats = {
        "hh": str(args.hh),
        "hv": str(args.hv),
        "dem": str(args.dem) if args.dem else None,
        "dltb": dltb_stats,
        "water_vectors": [str(p) for p in args.water_vector],
        "paddy_vectors": [str(p) for p in args.paddy_vector],
        "river_buffer_meters": float(args.river_buffer_meters),
        "paddy_buffer_meters": float(args.paddy_buffer_meters),
        "shape": [hh_info.lines, hh_info.samples],
        "bounds_wgs84": list(hh_info.bounds),
        "valid_pixels": valid_count,
        "valid_ratio": float(valid_count / valid.size),
        "hh_db_percentile_2_98": [hh_lo, hh_hi],
        "hv_db_percentile_2_98": [hv_lo, hv_hi],
        "threshold_method": args.threshold_method,
        "score_threshold": float(score_threshold),
        "hv_norm_dark_threshold": float(hv_dark_threshold),
        "prior_score_threshold": prior_score_threshold,
        "prior_hv_norm_dark_threshold": prior_hv_threshold,
        "paddy_score_threshold": paddy_score_threshold,
        "paddy_hv_norm_dark_threshold": paddy_hv_threshold,
        "candidate_score_threshold": candidate_score_threshold,
        "candidate_hv_norm_dark_threshold": candidate_hv_threshold,
        "slope_threshold_degrees": slope_threshold,
        "dem_used": dem_used,
        "known_water_prior_pixels": int(prior_mask.sum()),
        "known_water_prior_ratio_valid": float(prior_mask.sum() / max(valid_count, 1)),
        "dltb_water_prior_pixels": int(dltb_water_mask.sum()),
        "dltb_paddy_prior_pixels": int(dltb_paddy_mask.sum()),
        "dltb_strict_zone_pixels": int(dltb_strict_mask.sum()),
        "prior_candidate_pixels": prior_candidate_pixels,
        "paddy_prior_pixels": int(paddy_mask.sum()),
        "paddy_water_like_pixels": int(paddy_candidate.sum()),
        "paddy_water_like_ratio_valid": float(paddy_candidate.sum() / max(valid_count, 1)),
        "low_confidence_water_pixels": int((classified == CLASS_LOW_CONFIDENCE_WATER).sum()),
        "cartographic_water_pixels": int(cartographic_water.sum()) if cartographic_water is not None else 0,
        "cartographic_water_ratio_valid": float(cartographic_water.sum() / max(valid_count, 1)) if cartographic_water is not None else 0.0,
        "raw_water_pixels": int(raw_mask.sum()),
        "raw_water_ratio_valid": float(raw_mask.sum() / max(valid_count, 1)),
        "water_pixels": int(mask.sum()),
        "water_ratio_valid": float(mask.sum() / max(valid_count, 1)),
        "classified_counts": {CLASS_NAMES[class_id]: int((classified == class_id).sum()) for class_id in CLASS_NAMES},
        "min_component_pixels": int(args.min_component_pixels),
        "fill_hole_pixels": int(args.fill_hole_pixels),
        "close_pixels": int(args.close_pixels),
        "open_pixels": int(args.open_pixels),
        "paddy_close_pixels": int(args.paddy_close_pixels),
        "paddy_open_pixels": int(args.paddy_open_pixels),
        "candidate_close_pixels": int(args.candidate_close_pixels),
        "candidate_open_pixels": int(args.candidate_open_pixels),
        "cartographic_water_enabled": bool(args.cartographic_water),
        "cartographic_include_paddy": bool(args.cartographic_include_paddy),
        "cartographic_close_pixels": int(args.cartographic_close_pixels),
        "cartographic_open_pixels": int(args.cartographic_open_pixels),
        "cartographic_fill_hole_pixels": int(args.cartographic_fill_hole_pixels),
        "cartographic_min_component_pixels": int(args.cartographic_min_component_pixels),
        "min_polygon_area_m2": float(args.min_polygon_area_m2),
        "simplify_meters": float(args.simplify_meters),
        "smooth_meters": float(args.smooth_meters),
        "min_hole_area_m2": float(args.min_hole_area_m2),
        "vector_output": vector_stats,
    }
    (args.out_dir / "metadata.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))
    return 0

