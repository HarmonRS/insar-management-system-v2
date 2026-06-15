"""Command-line interface for GF-3 water extraction."""

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run_from_args


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract water from GF-3 HH/HV ENVI images with a non-DL baseline.")
    parser.add_argument("--hh", required=True, type=Path)
    parser.add_argument("--hv", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--dem", type=Path, default=None)
    parser.add_argument("--dltb-gdb", type=Path, default=None, help="DLTB FileGDB path used as soft land-use prior.")
    parser.add_argument("--dltb-cache-dir", type=Path, default=None, help="Directory containing water_prior.shp, paddy.shp, strict_review.shp DLTB cache layers.")
    parser.add_argument("--dltb-layer", default="DLTB")
    parser.add_argument("--dltb-field", default="DLMC")
    parser.add_argument("--dltb-mode", choices=["soft", "strict", "off"], default="soft")
    parser.add_argument("--dltb-max-features", type=int, default=None, help="Safety limit for DLTB features read from the source.")
    parser.add_argument("--water-vector", type=Path, action="append", default=[], help="Known river/lake shapefile. Can be passed multiple times.")
    parser.add_argument("--paddy-vector", type=Path, action="append", default=[], help="Paddy field/farmland water-sensitive vector. Can be passed multiple times.")
    parser.add_argument("--river-buffer-meters", type=float, default=120.0, help="Buffer width for line river vectors.")
    parser.add_argument("--paddy-buffer-meters", type=float, default=0.0, help="Buffer width for line paddy vectors, if any.")
    parser.add_argument("--threshold-method", choices=["otsu", "percentile"], default="otsu")
    parser.add_argument("--score-percentile", type=float, default=92.0)
    parser.add_argument("--hv-percentile", type=float, default=35.0)
    parser.add_argument("--prior-score-percentile", type=float, default=85.0)
    parser.add_argument("--prior-hv-percentile", type=float, default=45.0)
    parser.add_argument("--paddy-score-percentile", type=float, default=88.0)
    parser.add_argument("--paddy-hv-percentile", type=float, default=45.0)
    parser.add_argument("--candidate-score-percentile", type=float, default=90.0)
    parser.add_argument("--candidate-hv-percentile", type=float, default=50.0)
    parser.add_argument("--slope-max", type=float, default=8.0)
    parser.add_argument("--close-pixels", type=int, default=0, help="Binary closing radius for final water mask before vectorization.")
    parser.add_argument("--open-pixels", type=int, default=0, help="Binary opening radius for final water mask after hole filling.")
    parser.add_argument("--paddy-close-pixels", type=int, default=0, help="Binary closing radius for paddy water-like mask.")
    parser.add_argument("--paddy-open-pixels", type=int, default=0, help="Binary opening radius for paddy water-like mask.")
    parser.add_argument("--candidate-close-pixels", type=int, default=0, help="Binary closing radius for low-confidence candidate mask.")
    parser.add_argument("--candidate-open-pixels", type=int, default=0, help="Binary opening radius for low-confidence candidate mask.")
    parser.add_argument("--cartographic-water", action="store_true", help="Export a map-production layer that merges high-confidence and review candidate water.")
    parser.add_argument("--cartographic-include-paddy", action="store_true", help="Include paddy water-like pixels in cartographic water.")
    parser.add_argument("--cartographic-close-pixels", type=int, default=3)
    parser.add_argument("--cartographic-open-pixels", type=int, default=0)
    parser.add_argument("--cartographic-fill-hole-pixels", type=int, default=4096)
    parser.add_argument("--cartographic-min-component-pixels", type=int, default=512)
    parser.add_argument("--min-component-pixels", type=int, default=128)
    parser.add_argument("--fill-hole-pixels", type=int, default=512)
    parser.add_argument("--no-morphology", action="store_true")
    parser.add_argument("--out-vector-gpkg", type=Path, default=None, help="Write cartographic vector products to this GeoPackage.")
    parser.add_argument("--out-vector-shp-dir", type=Path, default=None, help="Write one ESRI Shapefile per cartographic vector layer.")
    parser.add_argument("--min-polygon-area-m2", type=float, default=1000.0)
    parser.add_argument("--simplify-meters", type=float, default=0.0)
    parser.add_argument("--smooth-meters", type=float, default=0.0, help="Vector boundary smoothing distance using buffer(+d)/buffer(-d).")
    parser.add_argument("--min-hole-area-m2", type=float, default=0.0, help="Remove polygon interior holes smaller than this area.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    return run_from_args(parser.parse_args(argv))

