"""GF-3 HH/HV water extraction adapter for the flood-analysis job chain."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from ..config import settings
from ..processors.gf3_water import WaterExtractionConfig, run_water_extraction


GF3_HH_HV_PROCESSOR = "gf3_hh_hv"


def _existing_path(value: str | os.PathLike[str] | None, *, label: str) -> Path:
    if not value:
        raise ValueError(f"{label} is required")
    path = Path(str(value))
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    return path


def _optional_existing_path(value: str | os.PathLike[str] | None, *, label: str) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    return path


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _numeric_param(params: dict[str, Any], name: str, default: Any, cast: type) -> Any:
    value = params.get(name, default)
    if value is None or value == "":
        return default
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _path_param_list(params: dict[str, Any], name: str) -> list[Path]:
    raw = params.get(name) or []
    if isinstance(raw, (str, os.PathLike)):
        raw = [raw]
    paths: list[Path] = []
    for item in raw:
        if not item:
            continue
        paths.append(_existing_path(item, label=name))
    return paths


def _first_existing(*paths: Path) -> str | None:
    for path in paths:
        if path.exists():
            return str(path)
    return None


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"GF3 water metadata is not valid JSON: {path}") from exc


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


def _water_area_km2(mask_path: str | None, *, water_pixel_count: int | None = None) -> float | None:
    if not mask_path or not os.path.isfile(mask_path):
        return None
    try:
        import rasterio

        with rasterio.open(mask_path) as src:
            data = src.read(1)
            pixel_count = int((data > 0).sum()) if water_pixel_count is None else int(water_pixel_count)
            return round(pixel_count * _pixel_area_km2(src.transform, src.crs, src.bounds), 4)
    except Exception:
        return None


def _vector_runtime_available() -> bool:
    try:
        import fiona  # noqa: F401
        import rasterio.features  # noqa: F401

        return True
    except Exception:
        return False


def run_gf3_hh_hv_water_extraction(
    *,
    hh_path: str,
    hv_path: str,
    output_dir: str,
    job_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the embedded GF-3 HH/HV water extractor and normalize outputs."""
    params = dict(params or {})
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    hh = _existing_path(hh_path, label="HH input")
    hv = _existing_path(hv_path, label="HV input")
    use_dltb = False
    dltb_cache_dir = None
    dem = _optional_existing_path(
        params.get("dem") or params.get("dem_path") or settings.GF3_WATER_DEM_PATH,
        label="GF3_WATER_DEM_PATH",
    )

    cartographic_water = _as_bool(
        params.get("cartographic_water"),
        bool(settings.GF3_WATER_DEFAULT_CARTOGRAPHIC),
    )
    out_vector = _as_bool(params.get("out_vector"), bool(settings.GF3_WATER_DEFAULT_OUT_VECTOR))
    vector_runtime_available = _vector_runtime_available()
    vector_output_enabled = out_vector and vector_runtime_available

    config = WaterExtractionConfig(
        hh=hh,
        hv=hv,
        out_dir=out_dir,
        dem=dem,
        dltb_cache_dir=dltb_cache_dir,
        dltb_mode="off",
        water_vector=[],
        paddy_vector=[],
        threshold_method=str(params.get("threshold_method") or "percentile"),
        score_percentile=_numeric_param(params, "score_percentile", 95.0, float),
        hv_percentile=_numeric_param(params, "hv_percentile", 20.0, float),
        candidate_score_percentile=_numeric_param(params, "candidate_score_percentile", 90.0, float),
        candidate_hv_percentile=_numeric_param(params, "candidate_hv_percentile", 50.0, float),
        close_pixels=_numeric_param(params, "close_pixels", 2, int),
        open_pixels=_numeric_param(params, "open_pixels", 1, int),
        fill_hole_pixels=_numeric_param(params, "fill_hole_pixels", 2048, int),
        min_component_pixels=_numeric_param(params, "min_component_pixels", 4096, int),
        candidate_open_pixels=_numeric_param(params, "candidate_open_pixels", 1, int),
        cartographic_water=cartographic_water,
        cartographic_close_pixels=_numeric_param(params, "cartographic_close_pixels", 4, int),
        cartographic_fill_hole_pixels=_numeric_param(params, "cartographic_fill_hole_pixels", 20000, int),
        cartographic_min_component_pixels=_numeric_param(params, "cartographic_min_component_pixels", 4096, int),
        out_vector_shp_dir=out_dir / "shp" if vector_output_enabled else None,
        min_polygon_area_m2=_numeric_param(params, "min_polygon_area_m2", 50000.0, float),
        simplify_meters=_numeric_param(params, "simplify_meters", 3.0, float),
        smooth_meters=_numeric_param(params, "smooth_meters", 5.0, float),
        min_hole_area_m2=_numeric_param(params, "min_hole_area_m2", 5000.0, float),
    )

    exit_code = run_water_extraction(config)
    metadata_path = out_dir / "metadata.json"
    metadata = _read_metadata(metadata_path)
    if exit_code != 0:
        return {
            "ok": False,
            "processor": GF3_HH_HV_PROCESSOR,
            "error": f"GF3 HH/HV water extraction failed with exit code {exit_code}",
            "metadata_json": metadata,
        }

    output_path = _first_existing(
        out_dir / "cartographic_water.tif",
        out_dir / "water_mask.tif",
        out_dir / "classified_water.tif",
    )
    preview_path = _first_existing(out_dir / "preview_overlay.png", out_dir / "classified_preview.png")
    vector_path = _first_existing(out_dir / "shp" / "cartographic_water.shp", out_dir / "water_products.gpkg")
    water_pixels = (
        metadata.get("cartographic_water_pixels")
        if metadata.get("cartographic_water_enabled")
        else metadata.get("water_pixels")
    )
    if water_pixels is None:
        water_pixels = metadata.get("water_pixels")
    water_pixel_count = int(water_pixels or 0)
    area_km2 = _water_area_km2(output_path, water_pixel_count=water_pixel_count)

    normalized_metadata = {
        **metadata,
        "processor": GF3_HH_HV_PROCESSOR,
        "job_id": job_id,
        "metadata_path": str(metadata_path),
        "output_path": output_path,
        "preview_path": preview_path,
        "vector_path": vector_path,
        "input_assets": {
            "hh": str(hh),
            "hv": str(hv),
            "dem": str(dem) if dem else None,
            "dltb_cache_dir": str(dltb_cache_dir) if dltb_cache_dir else None,
            "water_vector": [],
            "paddy_vector": [],
        },
        "runtime": {
            "prior_inputs_enabled": False,
            "dltb_enabled": False,
            "deep_learning_enabled": False,
            "vector_requested": out_vector,
            "vector_runtime_available": vector_runtime_available,
            "vector_output_enabled": vector_output_enabled,
        },
    }

    return {
        "ok": True,
        "processor": GF3_HH_HV_PROCESSOR,
        "output_path": output_path,
        "preview_path": preview_path,
        "vector_path": vector_path,
        "water_area_km2": area_km2,
        "water_pixel_count": water_pixel_count,
        "threshold_value": metadata.get("score_threshold"),
        "metadata_json": normalized_metadata,
    }
