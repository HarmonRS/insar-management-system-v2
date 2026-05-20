#!/usr/bin/env python3
"""Single-scene Gamma preprocessing to analysis-ready GeoTIFF.

The script is intentionally narrower than the full PyINT DInSAR pipeline:
LT source product -> Gamma SLC -> multilook amplitude -> geocode -> GeoTIFF.
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
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--satellite-family", default="LT1")
    parser.add_argument("--range-looks", type=int, default=2)
    parser.add_argument("--azimuth-looks", type=int, default=2)
    parser.add_argument("--geo-interp", default="1")
    parser.add_argument("--nodata-value", type=float, default=-9999.0)
    parser.add_argument("--to-db", action="store_true")
    return parser.parse_args()


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
    prepared_dem_path: str,
) -> None:
    lines = [
        "satelite = LT",
        f"masterDate = {date}",
        f"range_looks = {range_looks}",
        f"azimuth_looks = {azimuth_looks}",
        "dem_lat_ovr = 0.5",
        "dem_lon_ovr = 0.5",
        "Simphase_rpos = -",
        "Simphase_azpos = -",
        "Simphase_rwin = 256",
        "Simphase_azwin = 256",
        "Simphase_thresh = -",
        f"geo_interp = {geo_interp}",
    ]
    dem = str(prepared_dem_path or "").strip()
    if dem and Path(dem).is_file() and Path(dem + ".par").is_file():
        lines.append(f"DEM = {dem}")
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def convert_to_db_geotiff(source_tif: Path, target_tif: Path, nodata_value: float) -> dict[str, Any]:
    try:
        import numpy as np
        import rasterio
    except Exception as exc:
        shutil.copy2(source_tif, target_tif)
        return {
            "target": str(target_tif),
            "backscatter_unit": "gamma_mli_power",
            "warning": f"rasterio/numpy unavailable; kept power values: {exc}",
        }

    with rasterio.open(source_tif) as src:
        data = src.read(1).astype("float32")
        profile = src.profile.copy()
        src_nodata = src.nodata

    invalid = ~np.isfinite(data)
    if src_nodata is not None:
        invalid |= data == src_nodata
    invalid |= data <= 0
    db_data = np.full(data.shape, nodata_value, dtype="float32")
    db_data[~invalid] = (10.0 * np.log10(data[~invalid])).astype("float32")

    profile.update(dtype="float32", count=1, nodata=nodata_value, compress="deflate")
    target_tif.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(target_tif, "w", **profile) as dst:
        dst.write(db_data, 1)
    return {"target": str(target_tif), "backscatter_unit": "gamma_mli_db"}


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
    template_path = template_dir / f"{project_name}.template"
    write_template(
        template_path=template_path,
        date=date,
        range_looks=max(1, int(args.range_looks)),
        azimuth_looks=max(1, int(args.azimuth_looks)),
        geo_interp=str(args.geo_interp or "1"),
        prepared_dem_path=args.prepared_dem_path,
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
    conversion = convert_to_db_geotiff(power_tif, final_tif, float(args.nodata_value)) if args.to_db else {
        "target": str(final_tif),
        "backscatter_unit": "gamma_mli_power",
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
        "commands": commands,
        "conversion": conversion,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "manifest_path": str(manifest_path), "analysis_tif_path": str(final_tif)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
