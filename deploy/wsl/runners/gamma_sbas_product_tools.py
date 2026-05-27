#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


def read_gamma_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if parts and parts[0].rstrip(":") == key.rstrip(":"):
            return parts[1]
    raise KeyError(f"{key} not found in {path}")


def read_float32(path: Path, shape: tuple[int, int] | None = None) -> np.ndarray:
    data = np.fromfile(path, dtype=">f4")
    if shape is not None:
        data = data.reshape(shape)
    return data


def read_float32_pixel(path: Path, width: int, x: int, y: int) -> float:
    with path.open("rb") as handle:
        handle.seek((y * width + x) * 4)
        chunk = handle.read(4)
    if len(chunk) != 4:
        return float("nan")
    return float(struct.unpack(">f", chunk)[0])


def write_scaled_float32(input_path: Path, output_path: Path, scale: float) -> None:
    data = np.fromfile(input_path, dtype=">f4")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    (data * float(scale)).astype(">f4", copy=False).tofile(output_path)


def pick_auto_point(rate: np.ndarray, sigma: np.ndarray) -> tuple[int, int]:
    lines, width = rate.shape
    yy, xx = np.indices(rate.shape)
    edge_mask = (
        (xx > width * 0.1)
        & (xx < width * 0.9)
        & (yy > lines * 0.1)
        & (yy < lines * 0.9)
    )
    finite = np.isfinite(rate) & np.isfinite(sigma)
    valid = finite & edge_mask & (rate != 0.0) & (sigma > 0.0)
    if not valid.any():
        raise RuntimeError("No valid pixels available for monitor point selection")

    abs_rate = np.abs(rate[valid])
    sig = sigma[valid]
    rate_min = np.percentile(abs_rate, 85)
    rate_max = np.percentile(abs_rate, 99)
    sigma_max = np.percentile(sig, 40)
    candidate = valid & (np.abs(rate) >= rate_min) & (np.abs(rate) <= rate_max) & (sigma <= sigma_max)
    if not candidate.any():
        candidate = valid

    score = np.zeros(rate.shape, dtype=np.float32)
    score[candidate] = np.abs(rate[candidate]) / (sigma[candidate] + 1.0e-6)
    y, x = np.unravel_index(int(np.argmax(score)), rate.shape)
    return int(x), int(y)


def dem_grid(dem_par: Path) -> dict[str, float | int]:
    return {
        "width": int(read_gamma_value(dem_par, "width")),
        "nlines": int(read_gamma_value(dem_par, "nlines")),
        "corner_lon": float(read_gamma_value(dem_par, "corner_lon")),
        "corner_lat": float(read_gamma_value(dem_par, "corner_lat")),
        "post_lon": float(read_gamma_value(dem_par, "post_lon")),
        "post_lat": float(read_gamma_value(dem_par, "post_lat")),
    }


def radar_to_lonlat(x: int, y: int, dem_par: Path, lookup: Path) -> tuple[float | None, float | None]:
    grid = dem_grid(dem_par)
    width = int(grid["width"])
    lines = int(grid["nlines"])
    lut = np.fromfile(lookup, dtype=">c8").reshape((lines, width))
    rng = lut.real
    az = lut.imag
    valid = np.isfinite(rng) & np.isfinite(az) & (rng > 0.0) & (az > 0.0)
    if not valid.any():
        return None, None

    distance = np.full(rng.shape, np.inf, dtype=np.float32)
    distance[valid] = (rng[valid] - float(x)) ** 2 + (az[valid] - float(y)) ** 2
    gy, gx = np.unravel_index(int(np.argmin(distance)), distance.shape)
    lon = float(grid["corner_lon"]) + (gx + 0.5) * float(grid["post_lon"])
    lat = float(grid["corner_lat"]) + (gy + 0.5) * float(grid["post_lat"])
    return float(lon), float(lat)


def lonlat_to_radar(lon: float, lat: float, dem_par: Path, lookup: Path) -> tuple[int, int]:
    grid = dem_grid(dem_par)
    width = int(grid["width"])
    lines = int(grid["nlines"])
    gx = int(round((lon - float(grid["corner_lon"])) / float(grid["post_lon"]) - 0.5))
    gy = int(round((lat - float(grid["corner_lat"])) / float(grid["post_lat"]) - 0.5))
    gx = max(0, min(width - 1, gx))
    gy = max(0, min(lines - 1, gy))
    lut = np.fromfile(lookup, dtype=">c8").reshape((lines, width))
    value = lut[gy, gx]
    if not (np.isfinite(value.real) and np.isfinite(value.imag) and value.real > 0 and value.imag > 0):
        raise RuntimeError(f"manual lon/lat maps to invalid lookup pixel: lon={lon}, lat={lat}")
    return int(round(float(value.real))), int(round(float(value.imag)))


def safe_point_id(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:64]
    return text or fallback


def load_diff_files(timeseries_dir: Path) -> list[Path]:
    tab = timeseries_dir / "diff_ts.tab"
    if tab.is_file():
        rows = [line.strip() for line in tab.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        files = [Path(row.split()[0]) for row in rows if row.split()]
        files = [path for path in files if path.is_file()]
        if files:
            return files
    return sorted(timeseries_dir.glob("diff_ts_*.diff"))


def point_records(
    diff_files: list[Path],
    *,
    dates: list[str],
    width: int,
    x: int,
    y: int,
    scale_mm: float,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, path in enumerate(diff_files):
        phase = read_float32_pixel(path, width, x, y)
        away_mm = float(phase * scale_mm) if math.isfinite(phase) else float("nan")
        records.append(
            {
                "date": dates[index] if index < len(dates) else f"step_{index + 1:03d}",
                "phase_rad": float(phase),
                "los_away_mm": away_mm,
                "los_toward_mm": -away_mm if math.isfinite(away_mm) else float("nan"),
            }
        )
    return records


def write_point_outputs(
    point_dir: Path,
    point: dict[str, Any],
    *,
    records: list[dict[str, Any]],
    rate_value: float,
    sigma_value: float,
    wavelength: float,
    reference_date: str,
) -> dict[str, str]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    point_id = str(point["point_id"])
    csv_path = point_dir / f"{point_id}_timeseries.csv"
    json_path = point_dir / f"{point_id}_metadata.json"
    png_path = point_dir / f"{point_id}_timeseries.png"

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "phase_rad", "los_away_mm", "los_toward_mm"])
        writer.writeheader()
        writer.writerows(records)

    metadata = {
        "schema": "insar.sbas-monitor-point/v1",
        "point_id": point_id,
        "selection": point.get("selection"),
        "radar_pixel": {"range": int(point["range_pixel"]), "azimuth": int(point["azimuth_line"])},
        "approx_lonlat": {"lon": point.get("lon"), "lat": point.get("lat")},
        "reference_date": reference_date,
        "los_convention": "toward radar positive; away from radar negative",
        "los_rate_toward_mm_per_year": rate_value,
        "los_sigma_mm_per_year": sigma_value,
        "wavelength_m": wavelength,
        "records": records,
    }
    json_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    dates = [record["date"] for record in records]
    disp = [record["los_toward_mm"] for record in records]
    plt.figure(figsize=(8.0, 4.6), dpi=160)
    plt.plot(dates, disp, marker="o", linewidth=2.0, color="#1f77b4")
    plt.axhline(0, color="#666666", linewidth=0.8)
    plt.grid(True, color="#dddddd", linewidth=0.7)
    plt.title(
        f"LOS displacement time series ({point_id})\n"
        f"toward radar positive, rate={rate_value:.2f} mm/yr, sigma={sigma_value:.2f} mm/yr",
        fontsize=10,
    )
    plt.xlabel("Date")
    plt.ylabel("LOS displacement (mm)")
    plt.tight_layout()
    plt.savefig(png_path)
    plt.close()
    return {"png": str(png_path), "csv": str(csv_path), "metadata": str(json_path)}


def run_phase_to_los(args: argparse.Namespace) -> int:
    write_scaled_float32(Path(args.input), Path(args.output), float(args.scale))
    return 0


def run_monitor_points(args: argparse.Namespace) -> int:
    timeseries_dir = Path(args.timeseries_dir)
    export_dir = Path(args.export_dir)
    point_dir = Path(args.point_dir)
    mli_par = Path(args.mli_par)
    slc_par = Path(args.slc_par) if args.slc_par else mli_par
    dem_par = Path(args.dem_par)
    lookup = Path(args.lookup)
    monitor_config_path = Path(args.monitor_config)
    summary_path = Path(args.summary_path)
    point_dir.mkdir(parents=True, exist_ok=True)

    width = int(read_gamma_value(mli_par, "range_samples"))
    lines = int(read_gamma_value(mli_par, "azimuth_lines"))
    shape = (lines, width)
    dates = [item.strip() for item in str(args.dates or "").split(",") if item.strip()]
    reference_date = str(args.reference_date or "").strip()

    radar_freq = float(read_gamma_value(slc_par, "radar_frequency"))
    wavelength = 299792458.0 / radar_freq
    scale_mm = wavelength / (4.0 * math.pi) * 1000.0

    rate_toward = read_float32(export_dir / "los_rate_toward_mm_per_year.rdc", shape)
    sigma = read_float32(export_dir / "los_sigma_mm_per_year.rdc", shape)
    diff_files = load_diff_files(timeseries_dir)
    if not diff_files:
        raise RuntimeError(f"No diff_ts files found in {timeseries_dir}")

    config = {}
    if monitor_config_path.is_file():
        config = json.loads(monitor_config_path.read_text(encoding="utf-8"))
    mode = str(config.get("mode") or "auto_low_sigma_high_rate")
    selected_points: list[dict[str, Any]] = []

    if mode == "manual_lonlat" and config.get("points"):
        for index, raw in enumerate(config.get("points") or []):
            lon = float(raw["lon"])
            lat = float(raw["lat"])
            x, y = lonlat_to_radar(lon, lat, dem_par, lookup)
            selected_points.append(
                {
                    "point_id": safe_point_id(raw.get("point_id"), f"manual_{index + 1:03d}"),
                    "selection": "manual_lonlat_nearest_lookup_pixel",
                    "range_pixel": x,
                    "azimuth_line": y,
                    "lon": lon,
                    "lat": lat,
                }
            )
    else:
        x, y = pick_auto_point(rate_toward, sigma)
        lon, lat = radar_to_lonlat(x, y, dem_par, lookup)
        selected_points.append(
            {
                "point_id": "auto_low_sigma_high_rate",
                "selection": "automatic_low_sigma_high_rate_non_edge",
                "range_pixel": x,
                "azimuth_line": y,
                "lon": lon,
                "lat": lat,
            }
        )

    outputs: list[dict[str, Any]] = []
    for point in selected_points:
        x = int(point["range_pixel"])
        y = int(point["azimuth_line"])
        if not (0 <= x < width and 0 <= y < lines):
            raise ValueError(f"pixel out of bounds: x={x}, y={y}, width={width}, lines={lines}")
        records = point_records(diff_files, dates=dates, width=width, x=x, y=y, scale_mm=scale_mm)
        rate_value = float(rate_toward[y, x])
        sigma_value = float(sigma[y, x])
        files = write_point_outputs(
            point_dir,
            point,
            records=records,
            rate_value=rate_value,
            sigma_value=sigma_value,
            wavelength=wavelength,
            reference_date=reference_date,
        )
        outputs.append(
            {
                **point,
                "los_rate_toward_mm_per_year": rate_value,
                "los_sigma_mm_per_year": sigma_value,
                "record_count": len(records),
                "files": files,
            }
        )

    summary = {
        "schema": "insar.gamma-sbas-monitor-points-summary/v1",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "ready": bool(outputs),
        "mode": mode,
        "reference_date": reference_date,
        "width": width,
        "lines": lines,
        "wavelength_m": wavelength,
        "diff_ts_count": len(diff_files),
        "date_count": len(dates),
        "monitor_points": outputs,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gamma SBAS product helper tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    phase = subparsers.add_parser("phase-to-los")
    phase.add_argument("input")
    phase.add_argument("output")
    phase.add_argument("scale", type=float)
    phase.set_defaults(func=run_phase_to_los)

    monitor = subparsers.add_parser("monitor-points")
    monitor.add_argument("--monitor-config", required=True)
    monitor.add_argument("--timeseries-dir", required=True)
    monitor.add_argument("--export-dir", required=True)
    monitor.add_argument("--point-dir", required=True)
    monitor.add_argument("--mli-par", required=True)
    monitor.add_argument("--slc-par", required=True)
    monitor.add_argument("--dem-par", required=True)
    monitor.add_argument("--lookup", required=True)
    monitor.add_argument("--dates", default="")
    monitor.add_argument("--reference-date", default="")
    monitor.add_argument("--summary-path", required=True)
    monitor.set_defaults(func=run_monitor_points)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
