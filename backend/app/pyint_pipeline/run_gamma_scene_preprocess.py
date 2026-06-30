#!/usr/bin/env python3
"""Single-scene Gamma preprocessing to analysis-ready GeoTIFF.

The script is intentionally narrower than the full PyINT DInSAR pipeline:
LT source product -> Gamma SLC -> multilook amplitude -> geocode -> speckle-filtered dB GeoTIFF.
It is executed inside WSL by backend.app.services.lt_gamma_scene_service.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preprocess a SAR scene with Gamma/PyINT.")
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--pyint-home", required=True)
    parser.add_argument("--dem-root", required=True)
    parser.add_argument("--prepared-dem-path", default="")
    parser.add_argument("--dem-resolution-m", type=float, default=30.0)
    parser.add_argument("--target-grid-size-m", type=float, default=30.0)
    parser.add_argument("--dem-lat-ovr", type=float, default=0.0)
    parser.add_argument("--dem-lon-ovr", type=float, default=0.0)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--satellite-family", default="LT1")
    parser.add_argument("--range-looks", type=int, default=2)
    parser.add_argument("--azimuth-looks", type=int, default=2)
    parser.add_argument("--geo-interp", default="1")
    parser.add_argument("--nodata-value", type=float, default=-9999.0)
    parser.add_argument("--to-db", action="store_true")
    parser.add_argument("--speckle-filter-method", default="lee")
    parser.add_argument("--speckle-filter-size", type=int, default=5)
    parser.add_argument("--speckle-filter-enl", type=float, default=0.0)
    return parser.parse_args()


def clamp_float(value: float, minimum: float, maximum: float) -> float:
    if not math.isfinite(value):
        return minimum
    return min(maximum, max(minimum, float(value)))


def format_gamma_number(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def calculate_dem_oversampling(
    *,
    dem_resolution_m: float,
    target_grid_size_m: float,
    dem_lat_ovr: float,
    dem_lon_ovr: float,
) -> dict[str, Any]:
    dem_resolution = float(dem_resolution_m or 30.0)
    target_grid = float(target_grid_size_m or 30.0)
    if dem_resolution <= 0:
        dem_resolution = 30.0
    if target_grid <= 0:
        target_grid = dem_resolution

    derived = dem_resolution / target_grid
    lat_factor = clamp_float(float(dem_lat_ovr or derived), 0.25, 16.0)
    lon_factor = clamp_float(float(dem_lon_ovr or derived), 0.25, 16.0)
    actual_grid = dem_resolution / ((lat_factor + lon_factor) / 2.0)
    return {
        "dem_resolution_m": dem_resolution,
        "target_grid_size_m": target_grid,
        "derived_oversampling": derived,
        "dem_lat_ovr": lat_factor,
        "dem_lon_ovr": lon_factor,
        "actual_grid_size_m": actual_grid,
    }


def meters_per_degree_lon(latitude_deg: float) -> float:
    latitude_rad = math.radians(float(latitude_deg))
    return max(1.0, 111_320.0 * math.cos(latitude_rad))


def inspect_prepared_dem_path(path_text: str) -> dict[str, str]:
    text = str(path_text or "").strip()
    if not text:
        return {"kind": "", "direct_dem_path": "", "source_dem_path": ""}

    path = Path(text)
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path

    if resolved.is_file() and Path(str(resolved) + ".par").is_file():
        return {"kind": "gamma_ready", "direct_dem_path": str(resolved), "source_dem_path": ""}
    if resolved.is_file():
        return {"kind": "source_dem", "direct_dem_path": "", "source_dem_path": str(resolved)}
    return {"kind": "", "direct_dem_path": "", "source_dem_path": str(resolved)}


def read_slc_bbox(
    pyint_home: Path,
    slc_par: Path,
    env: dict[str, str],
    *,
    margin_deg: float = 0.1,
) -> tuple[float, float, float, float]:
    result = subprocess.run(
        ["SLC_corners", str(slc_par)],
        cwd=str(pyint_home),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"SLC_corners failed rc={result.returncode}: {detail}")
    lines = result.stdout.splitlines()
    if len(lines) < 10:
        raise RuntimeError(f"Unexpected SLC_corners output for {slc_par}")
    lat_line = lines[8].rstrip()
    lon_line = lines[9].rstrip()
    min_lat = float(lat_line.split(":")[1].split("  max. ")[0])
    max_lat = float(lat_line.split(":")[2])
    min_lon = float(lon_line.split(":")[1].split("  max. ")[0])
    max_lon = float(lon_line.split(":")[2])
    margin = max(0.0, float(margin_deg or 0.0))
    return min_lon - margin, min_lat - margin, max_lon + margin, max_lat + margin


def build_gamma_dem_from_source(
    *,
    source_dem: Path,
    target_base: Path,
    slc_par: Path,
    pyint_home: Path,
    log_dir: Path,
    env: dict[str, str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    west, south, east, north = read_slc_bbox(pyint_home, slc_par, env)
    log_dir.mkdir(parents=True, exist_ok=True)
    source_open = Path(str(source_dem) + ".vrt") if Path(str(source_dem) + ".vrt").is_file() else source_dem
    clipped_tif = target_base.with_suffix(".prepared_source_clip.tif")
    clipped_aux = Path(str(clipped_tif) + ".aux.xml")
    commands: list[dict[str, Any]] = []
    commands.append(run_logged(
        [
            "gdal_translate",
            "-projwin",
            str(west),
            str(north),
            str(east),
            str(south),
            "-of",
            "GTiff",
            str(source_open),
            str(clipped_tif),
        ],
        cwd=target_base.parent,
        env=env,
        log_dir=log_dir,
        stage="clip_prepared_dem",
    ))
    commands.append(run_logged(
        [
            "makedem.py",
            "-d",
            str(clipped_tif),
            "-p",
            "gamma",
            "-o",
            str(target_base),
        ],
        cwd=target_base.parent,
        env=env,
        log_dir=log_dir,
        stage="convert_prepared_dem",
    ))
    for path in (clipped_tif, clipped_aux):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
    dem_path = Path(str(target_base) + ".dem")
    dem_par_path = Path(str(target_base) + ".dem.par")
    if not dem_path.is_file() or not dem_par_path.is_file():
        raise RuntimeError(f"Prepared source DEM conversion did not create Gamma DEM: {dem_path}")
    return (
        {
            "kind": "source_dem_converted",
            "source_dem_path": str(source_dem),
            "source_open_path": str(source_open),
            "gamma_dem_path": str(dem_path),
            "bbox": {"west": west, "south": south, "east": east, "north": north},
        },
        commands,
    )


def run_logged(command: list[str], *, cwd: Path, env: dict[str, str], log_dir: Path, stage: str) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{stage}.stdout.log"
    stderr_path = log_dir / f"{stage}.stderr.log"
    result = subprocess.run(command, cwd=str(cwd), env=env, text=True, capture_output=True, check=False)
    stdout_path.write_text(result.stdout or "", encoding="utf-8", errors="ignore")
    stderr_path.write_text(result.stderr or "", encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{stage} failed rc={result.returncode}: {' '.join(command)}\n{detail}")
    return {
        "stage": stage,
        "command": command,
        "returncode": result.returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def read_gamma_par(path: Path, key: str) -> str:
    wanted = str(key or "").strip().rstrip(":")
    with path.open("r", encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            stripped = line.strip()
            if not stripped:
                continue
            label = stripped.split()[0].rstrip(":")
            if label != wanted:
                continue
            tail = stripped.split(":", 1)[1] if ":" in stripped else " ".join(stripped.split()[1:])
            tokens = tail.strip().split()
            if tokens:
                return tokens[0]
    raise KeyError(f"Cannot read {key} from {path}")


def calculate_dem_oversampling_from_gamma_dem(
    *,
    dem_par_path: Path,
    target_grid_size_m: float,
    explicit_dem_lat_ovr: float,
    explicit_dem_lon_ovr: float,
) -> dict[str, Any]:
    target_grid = max(1.0, float(target_grid_size_m or 30.0))
    post_lat_deg = abs(float(read_gamma_par(dem_par_path, "post_lat")))
    post_lon_deg = abs(float(read_gamma_par(dem_par_path, "post_lon")))
    corner_lat = float(read_gamma_par(dem_par_path, "corner_lat"))
    nlines = int(float(read_gamma_par(dem_par_path, "nlines")))
    center_lat = corner_lat - (post_lat_deg * max(0, nlines - 1) / 2.0)

    lat_spacing_m = post_lat_deg * 111_320.0
    lon_spacing_m = post_lon_deg * meters_per_degree_lon(center_lat)
    derived_lat = lat_spacing_m / target_grid
    derived_lon = lon_spacing_m / target_grid
    lat_factor = clamp_float(float(explicit_dem_lat_ovr or derived_lat), 0.25, 16.0)
    lon_factor = clamp_float(float(explicit_dem_lon_ovr or derived_lon), 0.25, 16.0)
    actual_lat_m = lat_spacing_m / lat_factor
    actual_lon_m = lon_spacing_m / lon_factor
    return {
        "dem_resolution_m": (lat_spacing_m + lon_spacing_m) / 2.0,
        "target_grid_size_m": target_grid,
        "derived_oversampling": (derived_lat + derived_lon) / 2.0,
        "derived_dem_lat_ovr": derived_lat,
        "derived_dem_lon_ovr": derived_lon,
        "dem_lat_ovr": lat_factor,
        "dem_lon_ovr": lon_factor,
        "actual_grid_size_m": (actual_lat_m + actual_lon_m) / 2.0,
        "actual_lat_grid_size_m": actual_lat_m,
        "actual_lon_grid_size_m": actual_lon_m,
        "source_dem_post_lat_deg": post_lat_deg,
        "source_dem_post_lon_deg": post_lon_deg,
        "source_dem_lat_spacing_m": lat_spacing_m,
        "source_dem_lon_spacing_m": lon_spacing_m,
        "source_dem_center_lat": center_lat,
        "source_dem_par_path": str(dem_par_path),
    }


def discover_lt_inputs(source_path: Path, date: str) -> list[Path]:
    patterns = [f"LT1*{date}*.tar.gz", f"LT1*{date}*.tiff", f"LT1*{date}*.tif"]
    if source_path.is_file():
        return [source_path]
    if not source_path.is_dir():
        raise FileNotFoundError(f"Source path does not exist: {source_path}")
    found: list[Path] = []
    for pattern in patterns:
        found.extend(path for path in source_path.rglob(pattern) if path.is_file())
    return sorted(set(found))


def stage_lt_inputs(source_path: Path, download_dir: Path, date: str) -> list[str]:
    download_dir.mkdir(parents=True, exist_ok=True)
    inputs = discover_lt_inputs(source_path, date)
    if not inputs:
        raise FileNotFoundError(f"No LT inputs for date {date} under {source_path}")

    staged: list[str] = []
    for source in inputs:
        target = download_dir / source.name
        shutil.copy2(source, target)
        staged.append(str(target))

        lower_name = source.name.lower()
        if lower_name.endswith((".tiff", ".tif")):
            base_candidates = [
                source.with_suffix(source.suffix + ".meta.xml"),
                source.with_suffix(".meta.xml"),
                source.with_name(source.stem + ".meta.xml"),
            ]
            for meta in base_candidates:
                if meta.is_file():
                    shutil.copy2(meta, download_dir / meta.name)
                    break
    return staged


def write_template(
    *,
    template_path: Path,
    date: str,
    range_looks: int,
    azimuth_looks: int,
    geo_interp: str,
    dem_path: str,
    prepared_dem_source: str,
    dem_oversampling: dict[str, Any],
) -> None:
    lines = [
        "satelite = LT",
        f"masterDate = {date}",
        f"range_looks = {range_looks}",
        f"azimuth_looks = {azimuth_looks}",
        f"target_grid_size_m = {format_gamma_number(float(dem_oversampling.get('target_grid_size_m') or 0.0))}",
        f"dem_lat_ovr = {format_gamma_number(float(dem_oversampling.get('dem_lat_ovr') or 1.0))}",
        f"dem_lon_ovr = {format_gamma_number(float(dem_oversampling.get('dem_lon_ovr') or 1.0))}",
        "Simphase_rpos = -",
        "Simphase_azpos = -",
        "Simphase_rwin = 256",
        "Simphase_azwin = 256",
        "Simphase_thresh = -",
        f"geo_interp = {geo_interp}",
    ]
    dem = str(dem_path or "").strip()
    if dem and Path(dem).is_file() and Path(dem + ".par").is_file():
        lines.append(f"DEM = {dem}")
    source = str(prepared_dem_source or "").strip()
    if source:
        lines.append(f"prepared_dem_source = {source}")
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def normalize_speckle_filter_method(method: str) -> str:
    text = str(method or "").strip().lower()
    if text in {"", "0", "false", "none", "off", "disabled", "no"}:
        return "none"
    if text in {"lee", "lee_filter"}:
        return "lee"
    raise ValueError(f"Unsupported speckle filter method: {method}")


def normalize_speckle_filter_size(size: int | float | str) -> int:
    try:
        value = int(float(size or 5))
    except Exception:
        value = 5
    value = max(3, min(99, value))
    if value % 2 == 0:
        value += 1
    return value


def moving_sum_axis(values: Any, size: int, axis: int) -> Any:
    import numpy as np

    radius = size // 2
    pad_width = [(0, 0)] * values.ndim
    pad_width[axis] = (radius, size - 1 - radius)
    padded = np.pad(values, pad_width, mode="edge")
    cumulative = np.cumsum(padded, axis=axis, dtype="float64")
    zero_shape = list(cumulative.shape)
    zero_shape[axis] = 1
    cumulative = np.concatenate([np.zeros(zero_shape, dtype="float64"), cumulative], axis=axis)
    length = values.shape[axis]
    start = np.arange(0, length)
    end = np.arange(size, size + length)
    return np.take(cumulative, end, axis=axis) - np.take(cumulative, start, axis=axis)


def box_sum(values: Any, size: int) -> Any:
    return moving_sum_axis(moving_sum_axis(values, size, axis=0), size, axis=1)


def local_power_stats(data: Any, valid: Any, window_size: int) -> tuple[Any, Any, Any]:
    import numpy as np

    values = np.where(valid, data, 0.0).astype("float64", copy=False)
    weights = valid.astype("float64", copy=False)
    count = box_sum(weights, window_size)
    power_sum = box_sum(values, window_size)
    power_sq_sum = box_sum(values * values, window_size)
    mean = np.divide(power_sum, count, out=np.zeros_like(power_sum), where=count > 0)
    mean_sq = np.divide(power_sq_sum, count, out=np.zeros_like(power_sq_sum), where=count > 0)
    variance = np.maximum(mean_sq - mean * mean, 0.0)
    valid_fraction = count / float(window_size * window_size)
    return mean, variance, valid_fraction


def apply_speckle_filter_power(
    data: Any,
    invalid: Any,
    *,
    method: str,
    window_size: int,
    equivalent_number_of_looks: float = 0.0,
) -> tuple[Any, dict[str, Any]]:
    import numpy as np

    normalized_method = normalize_speckle_filter_method(method)
    normalized_size = normalize_speckle_filter_size(window_size)
    record: dict[str, Any] = {
        "enabled": normalized_method != "none",
        "method": normalized_method,
        "window_size": normalized_size,
        "equivalent_number_of_looks": float(equivalent_number_of_looks or 0.0),
        "domain": "linear_power",
    }
    if normalized_method == "none":
        return data, record

    valid = ~invalid
    valid_count = int(np.count_nonzero(valid))
    record["valid_pixels"] = valid_count
    if valid_count == 0:
        record["enabled"] = False
        record["warning"] = "no valid positive pixels to filter"
        return data, record

    local_mean, local_variance, valid_fraction = local_power_stats(data, valid, normalized_size)
    stats_mask = valid & np.isfinite(local_variance) & np.isfinite(local_mean) & (valid_fraction > 0.0)
    enl = float(equivalent_number_of_looks or 0.0)
    if math.isfinite(enl) and enl > 0:
        noise_variance = np.maximum((local_mean * local_mean) / enl, 0.0)
        weight = np.divide(
            np.maximum(local_variance - noise_variance, 0.0),
            local_variance,
            out=np.zeros_like(local_variance),
            where=local_variance > 0,
        )
        record["noise_variance_model"] = "local_mean_squared_over_enl"
    else:
        noise_samples = local_variance[stats_mask]
        global_noise_variance = float(np.nanmedian(noise_samples)) if noise_samples.size else 0.0
        if not math.isfinite(global_noise_variance) or global_noise_variance <= 0:
            record["enabled"] = False
            record["warning"] = "local variance estimate is zero; kept unfiltered power values"
            return data, record
        noise_variance = global_noise_variance
        weight = np.divide(
            local_variance,
            local_variance + noise_variance,
            out=np.zeros_like(local_variance),
            where=(local_variance + noise_variance) > 0,
        )
        record["noise_variance"] = global_noise_variance
        record["noise_variance_model"] = "global_median_local_variance"
    filtered = local_mean + weight * (data.astype("float64", copy=False) - local_mean)
    filtered = np.where(np.isfinite(filtered) & (filtered > 0), filtered, data)
    output = data.astype("float32", copy=True)
    output[stats_mask] = filtered[stats_mask].astype("float32")
    return output, record


def convert_to_db_geotiff(
    source_tif: Path,
    target_tif: Path,
    nodata_value: float,
    *,
    speckle_filter_method: str = "none",
    speckle_filter_size: int = 5,
    speckle_filter_enl: float = 0.0,
) -> dict[str, Any]:
    filter_method = normalize_speckle_filter_method(speckle_filter_method)
    filter_size = normalize_speckle_filter_size(speckle_filter_size)
    try:
        import numpy as np
        import rasterio
    except Exception as exc:
        raise RuntimeError(f"rasterio/numpy unavailable; cannot create filtered dB GeoTIFF: {exc}") from exc

    with rasterio.open(source_tif) as src:
        data = src.read(1).astype("float32")
        profile = src.profile.copy()
        src_nodata = src.nodata

    invalid = ~np.isfinite(data)
    if src_nodata is not None:
        invalid |= data == src_nodata
    invalid |= data <= 0
    filtered_data, speckle_filter = apply_speckle_filter_power(
        data,
        invalid,
        method=filter_method,
        window_size=filter_size,
        equivalent_number_of_looks=float(speckle_filter_enl or 0.0),
    )
    invalid |= ~np.isfinite(filtered_data)
    invalid |= filtered_data <= 0
    db_data = np.full(data.shape, nodata_value, dtype="float32")
    db_data[~invalid] = (10.0 * np.log10(filtered_data[~invalid])).astype("float32")

    profile.update(dtype="float32", count=1, nodata=nodata_value, compress="deflate")
    target_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(target_tif, "w", **profile) as dst:
        dst.write(db_data, 1)
    return {"target": str(target_tif), "backscatter_unit": "gamma_mli_db", "speckle_filter": speckle_filter}


def main() -> int:
    args = parse_args()
    source_path = Path(args.source_path).resolve()
    output_dir = Path(args.output_dir).resolve()
    work_root = Path(args.work_dir).resolve()
    pyint_home = Path(args.pyint_home).resolve()
    dem_root = Path(args.dem_root).resolve()
    project_name = re.sub(r"[^0-9A-Za-z._-]+", "_", args.project_name).strip("._-") or "sar_scene"
    date = re.sub(r"\D", "", str(args.date or ""))[:8]
    if not re.fullmatch(r"20\d{6}", date):
        raise ValueError(f"Invalid scene date: {args.date}")

    scratch_dir = work_root / "scratch"
    template_dir = work_root / "templates"
    project_dir = scratch_dir / project_name
    download_dir = project_dir / "DOWNLOAD"
    log_dir = output_dir / "logs"
    output_dir.mkdir(parents=True, exist_ok=True)
    dem_root.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["SCRATCHDIR"] = str(scratch_dir)
    env["TEMPLATEDIR"] = str(template_dir)
    env["DEMDIR"] = str(dem_root)
    env["PYTHONPATH"] = f"{pyint_home}:{env.get('PYTHONPATH', '')}"
    env["PATH"] = f"{pyint_home / 'pyint'}:{env.get('PATH', '')}"

    staged_inputs = stage_lt_inputs(source_path, download_dir, date)
    dem_oversampling = calculate_dem_oversampling(
        dem_resolution_m=float(args.dem_resolution_m or 30.0),
        target_grid_size_m=float(args.target_grid_size_m or 30.0),
        dem_lat_ovr=float(args.dem_lat_ovr or 0.0),
        dem_lon_ovr=float(args.dem_lon_ovr or 0.0),
    )
    prepared_dem = inspect_prepared_dem_path(args.prepared_dem_path)
    if not prepared_dem.get("kind"):
        raise RuntimeError(f"A prepared DEM is required for LT analysis GeoTIFF production: {args.prepared_dem_path}")

    dem_path = prepared_dem.get("direct_dem_path") or ""
    prepared_dem_conversion: dict[str, Any] | None = None
    template_path = template_dir / f"{project_name}.template"
    write_template(
        template_path=template_path,
        date=date,
        range_looks=max(1, int(args.range_looks)),
        azimuth_looks=max(1, int(args.azimuth_looks)),
        geo_interp=str(args.geo_interp or "1"),
        dem_path=dem_path,
        prepared_dem_source=str(prepared_dem.get("source_dem_path") or ""),
        dem_oversampling=dem_oversampling,
    )

    commands: list[dict[str, Any]] = []
    commands.append(
        run_logged(
            [sys.executable, str(pyint_home / "pyint" / "down2slc_LT1.py"), project_name, date],
            cwd=work_root,
            env=env,
            log_dir=log_dir,
            stage="down2slc_lt1",
        )
    )

    if prepared_dem.get("kind") == "source_dem":
        slc_par = project_dir / "SLC" / date / f"{date}.slc.par"
        if not slc_par.is_file():
            raise FileNotFoundError(f"Gamma SLC parameter file missing before DEM conversion: {slc_par}")
        dem_target_base = dem_root / project_name / project_name
        dem_target_base.parent.mkdir(parents=True, exist_ok=True)
        prepared_dem_conversion, dem_commands = build_gamma_dem_from_source(
            source_dem=Path(str(prepared_dem.get("source_dem_path"))),
            target_base=dem_target_base,
            slc_par=slc_par,
            pyint_home=pyint_home,
            log_dir=log_dir,
            env=env,
        )
        commands.extend(dem_commands)
        dem_path = str(Path(str(dem_target_base) + ".dem"))

    dem_par_path = Path(str(dem_path) + ".par") if dem_path else Path()
    if dem_path and dem_par_path.is_file():
        dem_oversampling = calculate_dem_oversampling_from_gamma_dem(
            dem_par_path=dem_par_path,
            target_grid_size_m=float(args.target_grid_size_m or 30.0),
            explicit_dem_lat_ovr=float(args.dem_lat_ovr or 0.0),
            explicit_dem_lon_ovr=float(args.dem_lon_ovr or 0.0),
        )

    write_template(
        template_path=template_path,
        date=date,
        range_looks=max(1, int(args.range_looks)),
        azimuth_looks=max(1, int(args.azimuth_looks)),
        geo_interp=str(args.geo_interp or "1"),
        dem_path=dem_path,
        prepared_dem_source=str(prepared_dem.get("source_dem_path") or ""),
        dem_oversampling=dem_oversampling,
    )

    commands.append(
        run_logged(
            [sys.executable, str(pyint_home / "pyint" / "generate_rdc_dem.py"), project_name],
            cwd=work_root,
            env=env,
            log_dir=log_dir,
            stage="generate_rdc_dem",
        )
    )

    dem_dir = project_dir / "DEM"
    range_looks = max(1, int(args.range_looks))
    amp = dem_dir / f"{date}_{range_looks}rlks.amp"
    amp_par = dem_dir / f"{date}_{range_looks}rlks.amp.par"
    utm_dem_par = dem_dir / f"{date}_{range_looks}rlks.utm.dem.par"
    utm_to_rdc = dem_dir / f"{date}_{range_looks}rlks.UTM_TO_RDC"
    for required in (amp, amp_par, utm_dem_par, utm_to_rdc):
        if not required.is_file():
            raise FileNotFoundError(f"Required Gamma product missing: {required}")

    width = read_gamma_par(amp_par, "range_samples")
    geo_width = read_gamma_par(utm_dem_par, "width")
    geo_nlines = read_gamma_par(utm_dem_par, "nlines")
    geo_amp = output_dir / "gamma_geo_amp"
    commands.append(
        run_logged(
            [
                "geocode_back",
                str(amp),
                str(width),
                str(utm_to_rdc),
                str(geo_amp),
                str(geo_width),
                str(geo_nlines),
                str(args.geo_interp or "1"),
                "0",
            ],
            cwd=work_root,
            env=env,
            log_dir=log_dir,
            stage="geocode_amp",
        )
    )

    power_tif = output_dir / "analysis_ready_power.tif"
    commands.append(
        run_logged(
            [
                "data2geotiff",
                str(utm_dem_par),
                str(geo_amp),
                "2",
                str(power_tif),
                f"{float(args.nodata_value):g}",
            ],
            cwd=work_root,
            env=env,
            log_dir=log_dir,
            stage="data2geotiff_amp",
        )
    )
    if not power_tif.is_file() or power_tif.stat().st_size <= 0:
        raise RuntimeError(f"data2geotiff did not create output: {power_tif}")

    final_tif = output_dir / "analysis_ready.tif"
    speckle_filter_config = {
        "method": normalize_speckle_filter_method(args.speckle_filter_method),
        "window_size": normalize_speckle_filter_size(args.speckle_filter_size),
    }
    conversion = convert_to_db_geotiff(
        power_tif,
        final_tif,
        float(args.nodata_value),
        speckle_filter_method=args.speckle_filter_method,
        speckle_filter_size=args.speckle_filter_size,
        speckle_filter_enl=float(args.speckle_filter_enl or (range_looks * max(1, int(args.azimuth_looks)))),
    ) if args.to_db else {
        "target": str(final_tif),
        "backscatter_unit": "gamma_mli_power",
        "speckle_filter": {
            "enabled": False,
            **speckle_filter_config,
            "warning": "not applied because --to-db was disabled",
        },
    }
    if not args.to_db:
        shutil.copy2(power_tif, final_tif)

    manifest = {
        "ok": True,
        "satellite_family": args.satellite_family,
        "project_name": project_name,
        "date": date,
        "source_path": str(source_path),
        "staged_inputs": staged_inputs,
        "work_dir": str(work_root),
        "output_dir": str(output_dir),
        "analysis_tif_path": str(final_tif),
        "power_tif_path": str(power_tif),
        "backscatter_unit": conversion.get("backscatter_unit"),
        "gamma_products": {
            "amp": str(amp),
            "amp_par": str(amp_par),
            "utm_dem_par": str(utm_dem_par),
            "utm_to_rdc": str(utm_to_rdc),
            "geo_amp": str(geo_amp),
        },
        "looks": {"range": range_looks, "azimuth": max(1, int(args.azimuth_looks))},
        "speckle_filter": conversion.get("speckle_filter"),
        "processing_steps": {
            "multilook": {
                "enabled": True,
                "range_looks": range_looks,
                "azimuth_looks": max(1, int(args.azimuth_looks)),
            },
            "geocode": {"enabled": True, "interpolation": str(args.geo_interp or "1")},
            "speckle_filter": conversion.get("speckle_filter"),
            "db_conversion": {"enabled": bool(args.to_db), "unit": conversion.get("backscatter_unit")},
        },
        "dem": {
            "prepared_dem_path": str(args.prepared_dem_path or "").strip(),
            "prepared_dem_kind": prepared_dem.get("kind"),
            "gamma_dem_path": dem_path,
            "conversion": prepared_dem_conversion,
            "oversampling": dem_oversampling,
        },
        "commands": commands,
        "conversion": conversion,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "manifest_path": str(manifest_path), "analysis_tif_path": str(final_tif)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
