#!/usr/bin/env python3
"""Build a publish-style manifest and preview bundle from MintPy outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _decode_date(value):
    return value.decode() if isinstance(value, (bytes, np.bytes_)) else str(value)


def _read_h5_summary(h5_path: Path) -> dict:
    with h5py.File(h5_path, "r") as f:
        datasets = sorted(f.keys())
        attrs = {k: (v.item() if hasattr(v, "item") else v) for k, v in f.attrs.items()}
        serializable_attrs = {}
        for key, value in attrs.items():
            if isinstance(value, bytes):
                serializable_attrs[key] = value.decode()
            elif isinstance(value, np.ndarray):
                serializable_attrs[key] = value.tolist()
            else:
                serializable_attrs[key] = value

        summary = {
            "path": h5_path.name,
            "datasets": datasets,
            "attrs": serializable_attrs,
        }

        if "date" in f:
            summary["dates"] = [_decode_date(x) for x in f["date"][:]]

        return summary


def _write_velocity_preview(geo_velocity_h5: Path, output_png: Path) -> dict:
    with h5py.File(geo_velocity_h5, "r") as f:
        velocity = f["velocity"][:]

    finite = np.isfinite(velocity)
    valid = velocity[finite]

    if valid.size == 0:
        raise RuntimeError(f"No finite velocity values found in {geo_velocity_h5}")

    vmax = float(np.nanpercentile(np.abs(valid), 98))
    vmax = max(vmax, 1e-6)
    vmin = -vmax

    fig = plt.figure(figsize=(10, 7), dpi=150)
    ax = fig.add_subplot(111)
    im = ax.imshow(velocity, cmap="RdBu_r", vmin=vmin, vmax=vmax)
    ax.set_title("Velocity Preview (m/year)")
    ax.set_xticks([])
    ax.set_yticks([])
    cbar = fig.colorbar(im, ax=ax, shrink=0.82)
    cbar.set_label("m/year")
    fig.tight_layout()
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, bbox_inches="tight")
    plt.close(fig)

    return {
        "vmin": vmin,
        "vmax": vmax,
        "valid_pixels": int(valid.size),
    }


def _count_mask_pixels(mask_h5: Path) -> dict:
    with h5py.File(mask_h5, "r") as f:
        dataset_name = "mask" if "mask" in f else "waterMask"
        data = f[dataset_name][:]

    total = int(data.size)
    valid = int(np.count_nonzero(data))
    return {
        "dataset": dataset_name,
        "valid_pixels": valid,
        "total_pixels": total,
        "valid_ratio": valid / total if total else 0.0,
    }


def build_bundle(mintpy_work_dir: Path, publish_dir: Path, group_key: str | None) -> None:
    assets_dir = publish_dir / "assets"
    preview_dir = publish_dir / "preview"
    metadata_dir = publish_dir / "metadata"

    geo_velocity_h5 = assets_dir / "geo_velocity.h5"
    geo_timeseries_h5 = assets_dir / "geo_timeseries.h5"
    geo_temporal_coh_h5 = assets_dir / "geo_temporalCoherence.h5"
    geo_mask_temp_coh_h5 = assets_dir / "geo_maskTempCoh.h5"

    preview_stats = _write_velocity_preview(
        geo_velocity_h5=geo_velocity_h5,
        output_png=preview_dir / "velocity_preview.png",
    )

    with h5py.File(mintpy_work_dir / "timeseries.h5", "r") as ts_file:
        ref_date = ts_file.attrs.get("REF_DATE")
        ref_x = ts_file.attrs.get("REF_X")
        ref_y = ts_file.attrs.get("REF_Y")
        stack_dates = [_decode_date(x) for x in ts_file["date"][:]]

    manifest = {
        "schema_version": "psinsar.publish.v1",
        "catalog_name": "psinsar",
        "mode": "sbas",
        "engine_code": "isce2",
        "processor_code": "isce2_stack_mintpy",
        "group_key": group_key,
        "mintpy_work_dir": str(mintpy_work_dir),
        "publish_dir": str(publish_dir),
        "reference_date": _decode_date(ref_date) if ref_date is not None else None,
        "reference_point": {
            "x": int(ref_x) if ref_x is not None else None,
            "y": int(ref_y) if ref_y is not None else None,
        },
        "stack_dates": stack_dates,
        "artifacts": [
            {"product_type": "timeseries_cube", "path": "assets/geo_timeseries.h5"},
            {"product_type": "velocity_map", "path": "assets/geo_velocity.h5"},
            {"product_type": "velocity_geotiff", "path": "assets/velocity.tif"},
            {"product_type": "temporal_coherence", "path": "assets/geo_temporalCoherence.h5"},
            {"product_type": "temporal_coherence_geotiff", "path": "assets/temporalCoherence.tif"},
            {"product_type": "quality_mask", "path": "assets/geo_maskTempCoh.h5"},
            {"product_type": "quality_mask_geotiff", "path": "assets/maskTempCoh.tif"},
            {"product_type": "preview_png", "path": "preview/velocity_preview.png"},
            {"product_type": "diagnostic_png", "path": "preview/numTriNonzeroIntAmbiguity.png"},
        ],
        "quality": {
            "mask_all_valid": _count_mask_pixels(mintpy_work_dir / "maskAllValid.h5"),
            "mask_temp_coh": _count_mask_pixels(mintpy_work_dir / "maskTempCoh.h5"),
            "velocity_preview": preview_stats,
        },
        "summaries": {
            "geo_velocity": _read_h5_summary(geo_velocity_h5),
            "geo_timeseries": _read_h5_summary(geo_timeseries_h5),
            "geo_temporal_coherence": _read_h5_summary(geo_temporal_coh_h5),
            "geo_mask_temp_coh": _read_h5_summary(geo_mask_temp_coh_h5),
        },
        "metadata_files": [
            "metadata/smallbaselineApp.cfg",
            "metadata/source_quality_summary.json",
        ],
    }

    summary_json = {
        "maskAllValid": manifest["quality"]["mask_all_valid"],
        "maskTempCoh": manifest["quality"]["mask_temp_coh"],
        "preview": manifest["quality"]["velocity_preview"],
    }

    publish_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    (publish_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (metadata_dir / "source_quality_summary.json").write_text(
        json.dumps(summary_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote manifest: {publish_dir / 'manifest.json'}")
    print(f"Wrote quality summary: {metadata_dir / 'source_quality_summary.json'}")
    print(f"Wrote preview: {preview_dir / 'velocity_preview.png'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build publish-style artifacts for MintPy SBAS outputs.")
    parser.add_argument("--mintpy-work-dir", required=True, help="MintPy work directory containing timeseries.h5, velocity.h5, etc.")
    parser.add_argument("--publish-dir", required=True, help="Publish output directory.")
    parser.add_argument("--group-key", default=None, help="Optional stack group key to embed in manifest.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    build_bundle(
        mintpy_work_dir=Path(args.mintpy_work_dir),
        publish_dir=Path(args.publish_dir),
        group_key=args.group_key,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
