"""Configuration objects for GF-3 water extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WaterExtractionConfig:
    hh: Path
    hv: Path
    out_dir: Path
    dem: Path | None = None
    dltb_gdb: Path | None = None
    dltb_cache_dir: Path | None = None
    dltb_layer: str = "DLTB"
    dltb_field: str = "DLMC"
    dltb_mode: str = "soft"
    dltb_max_features: int | None = None
    water_vector: list[Path] = field(default_factory=list)
    paddy_vector: list[Path] = field(default_factory=list)
    river_buffer_meters: float = 120.0
    paddy_buffer_meters: float = 0.0
    threshold_method: str = "otsu"
    score_percentile: float = 92.0
    hv_percentile: float = 35.0
    prior_score_percentile: float = 85.0
    prior_hv_percentile: float = 45.0
    paddy_score_percentile: float = 88.0
    paddy_hv_percentile: float = 45.0
    candidate_score_percentile: float = 90.0
    candidate_hv_percentile: float = 50.0
    slope_max: float = 8.0
    close_pixels: int = 0
    open_pixels: int = 0
    paddy_close_pixels: int = 0
    paddy_open_pixels: int = 0
    candidate_close_pixels: int = 0
    candidate_open_pixels: int = 0
    cartographic_water: bool = False
    cartographic_include_paddy: bool = False
    cartographic_close_pixels: int = 3
    cartographic_open_pixels: int = 0
    cartographic_fill_hole_pixels: int = 4096
    cartographic_min_component_pixels: int = 512
    min_component_pixels: int = 128
    fill_hole_pixels: int = 512
    no_morphology: bool = False
    out_vector_gpkg: Path | None = None
    out_vector_shp_dir: Path | None = None
    min_polygon_area_m2: float = 1000.0
    simplify_meters: float = 0.0
    smooth_meters: float = 0.0
    min_hole_area_m2: float = 0.0
