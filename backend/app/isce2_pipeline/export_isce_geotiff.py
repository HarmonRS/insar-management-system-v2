#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

DEFAULT_WAVELENGTH = 0.23793052222222222
DEFAULT_NODATA = -9999.0
DEFAULT_REFERENCE_MODE = "none"
DEFAULT_REFERENCE_COH_THRESHOLD = 0.30
REFERENCE_MODE_CHOICES = ("none", "coh_median")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export ISCE2 geocoded displacement/coherence products to GeoTIFF."
    )
    parser.add_argument(
        "work_dir",
        type=Path,
        help="ISCE2 work directory containing interferogram/*.geo outputs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for GeoTIFF files. Default: work_dir",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default=None,
        help="Output filename prefix. Default: work_dir basename",
    )
    parser.add_argument(
        "--wavelength",
        type=float,
        default=DEFAULT_WAVELENGTH,
        help="Radar wavelength in meters",
    )
    parser.add_argument(
        "--coh-threshold",
        type=float,
        default=0.05,
        help="Mask pixels with coherence below this threshold in *_disp.tif",
    )
    parser.add_argument(
        "--reference-mode",
        type=str,
        choices=REFERENCE_MODE_CHOICES,
        default=DEFAULT_REFERENCE_MODE,
        help="Optional reference normalization mode for debug exports",
    )
    parser.add_argument(
        "--reference-coh-threshold",
        type=float,
        default=DEFAULT_REFERENCE_COH_THRESHOLD,
        help="Minimum coherence used to select reference pixels for normalization",
    )
    parser.add_argument(
        "--include-disp-full",
        action="store_true",
        help="Also export the coherence-unmasked displacement GeoTIFF for debugging",
    )
    return parser.parse_args()


def write_geotiff(array: np.ndarray, ref_ds: gdal.Dataset, out_path: Path, nodata: float) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        str(out_path),
        ref_ds.RasterXSize,
        ref_ds.RasterYSize,
        1,
        gdal.GDT_Float32,
        options=["COMPRESS=LZW", "TILED=YES"],
    )
    ds.SetGeoTransform(ref_ds.GetGeoTransform())
    ds.SetProjection(ref_ds.GetProjection())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(array.astype(np.float32))
    ds.FlushCache()
    ds = None


def compute_reference_offset(
    disp_m_raw: np.ndarray,
    amp: np.ndarray,
    coh: np.ndarray,
    coh_threshold: float,
    reference_mode: str,
    reference_coh_threshold: float,
) -> tuple[float, dict[str, float | int | str]]:
    amp_valid = np.isfinite(amp) & (amp != 0)
    coh_finite = np.isfinite(coh)
    disp_valid = np.isfinite(disp_m_raw)
    base_mask = amp_valid & coh_finite & disp_valid
    if not base_mask.any():
        raise RuntimeError("No valid displacement pixels available for ISCE2 export.")

    normalized_mode = str(reference_mode or DEFAULT_REFERENCE_MODE).strip().lower()
    if normalized_mode not in REFERENCE_MODE_CHOICES:
        raise ValueError(f"Unsupported reference mode: {reference_mode}")

    stats: dict[str, float | int | str] = {
        "mode": normalized_mode,
        "reference_count": 0,
        "reference_ratio": 0.0,
        "support_ratio": float(base_mask.mean()),
        "selection_threshold": 0.0,
        "fallback": "",
    }
    if normalized_mode == "none":
        return 0.0, stats

    selection_threshold = min(1.0, max(0.0, max(float(coh_threshold), float(reference_coh_threshold))))
    reference_mask = base_mask & (coh >= selection_threshold)
    fallback = ""
    if not reference_mask.any():
        reference_mask = base_mask & (coh > 0)
        fallback = "coh>0"
    if not reference_mask.any():
        reference_mask = amp_valid & disp_valid
        fallback = "amp_only"

    reference_count = int(reference_mask.sum())
    if reference_count <= 0:
        raise RuntimeError("Failed to select any reference pixels for displacement normalization.")

    stats.update(
        {
            "reference_count": reference_count,
            "reference_ratio": float(reference_mask.mean()),
            "selection_threshold": float(selection_threshold),
            "fallback": fallback,
        }
    )
    return float(np.median(disp_m_raw[reference_mask])), stats


def export_products(
    work_dir: Path,
    output_dir: Path,
    prefix: str,
    wavelength: float,
    coh_threshold: float,
    reference_mode: str = DEFAULT_REFERENCE_MODE,
    reference_coh_threshold: float = DEFAULT_REFERENCE_COH_THRESHOLD,
    include_disp_full: bool = False,
    nodata: float = DEFAULT_NODATA,
) -> dict[str, Path]:
    unw_path = work_dir / "interferogram" / "filt_topophase.unw.geo.vrt"
    cor_path = work_dir / "interferogram" / "topophase.cor.geo.vrt"

    if not unw_path.exists():
        raise FileNotFoundError(f"Missing unwrapped product: {unw_path}")
    if not cor_path.exists():
        raise FileNotFoundError(f"Missing coherence product: {cor_path}")

    unw_ds = gdal.Open(str(unw_path))
    cor_ds = gdal.Open(str(cor_path))
    if unw_ds is None or cor_ds is None:
        raise RuntimeError("Failed to open ISCE2 geo products with GDAL.")

    amp = unw_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    phase = unw_ds.GetRasterBand(2).ReadAsArray().astype(np.float32)

    coh_band = 2 if cor_ds.RasterCount >= 2 else 1
    coh = cor_ds.GetRasterBand(coh_band).ReadAsArray().astype(np.float32)
    coh_valid = np.isfinite(coh) & (coh > 0)
    amp_valid = np.isfinite(amp) & (amp != 0)

    disp_m_raw = phase * wavelength / (4.0 * np.pi)
    reference_offset_m, reference_stats = compute_reference_offset(
        disp_m_raw=disp_m_raw,
        amp=amp,
        coh=coh,
        coh_threshold=coh_threshold,
        reference_mode=reference_mode,
        reference_coh_threshold=reference_coh_threshold,
    )
    disp_m = disp_m_raw - reference_offset_m
    disp_m_full = disp_m.copy()

    mask = (~amp_valid) | (~np.isfinite(disp_m)) | (~np.isfinite(coh)) | (coh < coh_threshold)
    disp_m_masked = disp_m.copy()
    disp_m_masked[mask] = nodata
    disp_m_full[(~amp_valid) | (~np.isfinite(disp_m_full))] = nodata

    coh_out = coh.copy()
    coh_out[~coh_valid] = nodata

    output_dir.mkdir(parents=True, exist_ok=True)
    out_disp = output_dir / f"{prefix}_disp.tif"
    out_coh = output_dir / f"{prefix}_coh.tif"

    write_geotiff(disp_m_masked, unw_ds, out_disp, nodata)
    write_geotiff(coh_out, cor_ds, out_coh, nodata)
    out_disp_full = None
    if include_disp_full:
        out_disp_full = output_dir / f"{prefix}_disp_full.tif"
        write_geotiff(disp_m_full, unw_ds, out_disp_full, nodata)

    valid_disp = disp_m_masked[disp_m_masked != nodata]
    valid_coh = coh_out[coh_out != nodata]
    valid_full = disp_m_full[disp_m_full != nodata] if include_disp_full else np.array([], dtype=np.float32)
    valid_raw = disp_m_raw[amp_valid & np.isfinite(disp_m_raw)]
    using_reference = str(reference_stats["mode"]) != "none"

    print(f"Work dir:                {work_dir}")
    print(f"Output prefix:           {prefix}")
    print(f"Coherence threshold:     {coh_threshold}")
    print(f"Reference mode:          {reference_stats['mode']}")
    if using_reference:
        print(
            "Reference coh floor:    "
            f"{float(reference_stats['selection_threshold']):.2f}"
        )
        print(
            "Reference pixel ratio:  "
            f"{float(reference_stats['reference_ratio'])*100:.2f}%"
        )
        print(f"Reference offset:        {reference_offset_m:.4f} m")
        if reference_stats["fallback"]:
            print(f"Reference fallback:      {reference_stats['fallback']}")
    print(f"Unwrap support ratio:    {amp_valid.mean()*100:.2f}%")
    print(f"Coherence support ratio: {coh_valid.mean()*100:.2f}%")
    print(f"Masked disp ratio:       {(disp_m_masked != nodata).mean()*100:.2f}%")
    if valid_raw.size:
        print(f"Raw disp range:          [{valid_raw.min():.4f}, {valid_raw.max():.4f}] m")
    if valid_disp.size:
        label = "Norm disp range" if using_reference else "Disp range"
        print(f"{label + ':':24}[{valid_disp.min():.4f}, {valid_disp.max():.4f}] m")
    if include_disp_full and valid_full.size:
        label = "Norm full disp range" if using_reference else "Full disp range"
        print(f"{label + ':':24}[{valid_full.min():.4f}, {valid_full.max():.4f}] m")
    if valid_coh.size:
        print(f"Coherence range:         [{valid_coh.min():.4f}, {valid_coh.max():.4f}]")
    print(f"Wrote:                   {out_disp}")
    if out_disp_full is not None:
        print(f"Wrote:                   {out_disp_full}")
    print(f"Wrote:                   {out_coh}")

    unw_ds = None
    cor_ds = None
    outputs: dict[str, Path] = {
        "disp": out_disp,
        "coh": out_coh,
    }
    if out_disp_full is not None:
        outputs["disp_full"] = out_disp_full
    return outputs


def main() -> int:
    args = parse_args()
    work_dir = args.work_dir.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else work_dir
    prefix = args.prefix or work_dir.name

    export_products(
        work_dir=work_dir,
        output_dir=output_dir,
        prefix=prefix,
        wavelength=args.wavelength,
        coh_threshold=args.coh_threshold,
        reference_mode=args.reference_mode,
        reference_coh_threshold=args.reference_coh_threshold,
        include_disp_full=args.include_disp_full,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
