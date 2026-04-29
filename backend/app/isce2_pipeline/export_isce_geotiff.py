#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

DEFAULT_WAVELENGTH = 0.23793052222222222
DEFAULT_NODATA = -9999.0
DEFAULT_REFERENCE_MODE = "coh_median"
DEFAULT_REFERENCE_COH_THRESHOLD = 0.30
DEFAULT_DERAMP_MODE = "plane"
DEFAULT_DERAMP_COH_THRESHOLD = 0.30
REFERENCE_MODE_CHOICES = ("none", "coh_median")
DERAMP_MODE_CHOICES = ("none", "plane")


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
        help="Reference normalization mode applied before final displacement export",
    )
    parser.add_argument(
        "--reference-coh-threshold",
        type=float,
        default=DEFAULT_REFERENCE_COH_THRESHOLD,
        help="Minimum coherence used to select reference pixels for normalization",
    )
    parser.add_argument(
        "--deramp-mode",
        type=str,
        choices=DERAMP_MODE_CHOICES,
        default=DEFAULT_DERAMP_MODE,
        help="Optional long-wavelength ramp removal applied after reference normalization",
    )
    parser.add_argument(
        "--deramp-coh-threshold",
        type=float,
        default=DEFAULT_DERAMP_COH_THRESHOLD,
        help="Minimum coherence used when selecting pixels for deramp fitting",
    )
    parser.add_argument(
        "--include-disp-full",
        action="store_true",
        help="Also export the coherence-unmasked final displacement GeoTIFF",
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


def _resolve_phase_source(work_dir: Path) -> dict[str, str | bool]:
    ionosphere_phase = work_dir / "ionosphere" / "nondispersive.bil.unwCor.filt.geo.vrt"
    ionosphere_mask = work_dir / "ionosphere" / "mask.bil.geo.vrt"
    full_unwrap = work_dir / "interferogram" / "filt_topophase.unw.geo.vrt"
    if ionosphere_phase.exists():
        return {
            "phase_path": str(ionosphere_phase),
            "phase_source": "ionosphere_nondispersive",
            "mask_path": str(ionosphere_mask) if ionosphere_mask.exists() else "",
            "ionosphere_corrected": True,
        }
    return {
        "phase_path": str(full_unwrap),
        "phase_source": "interferogram_unwrapped",
        "mask_path": "",
        "ionosphere_corrected": False,
    }


def _select_support_mask(
    *,
    base_mask: np.ndarray,
    amp_valid: np.ndarray,
    disp_valid: np.ndarray,
    coh: np.ndarray,
    selection_threshold: float,
) -> tuple[np.ndarray, dict[str, float | int | str]]:
    fallback = ""
    support_mask = base_mask & (coh >= selection_threshold)
    if not support_mask.any():
        support_mask = base_mask & (coh > 0)
        fallback = "coh>0"
    if not support_mask.any():
        support_mask = amp_valid & disp_valid
        fallback = "amp_only"
    stats: dict[str, float | int | str] = {
        "selection_threshold": float(selection_threshold),
        "fallback": fallback,
        "support_ratio": float(base_mask.mean()),
        "support_count": int(support_mask.sum()),
        "support_mask_ratio": float(support_mask.mean()),
    }
    return support_mask, stats


def compute_reference_offset(
    disp_m_raw: np.ndarray,
    amp: np.ndarray,
    coh: np.ndarray,
    coh_threshold: float,
    reference_mode: str,
    reference_coh_threshold: float,
) -> tuple[float, np.ndarray, dict[str, float | int | str]]:
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
        return 0.0, base_mask, stats

    selection_threshold = min(1.0, max(0.0, max(float(coh_threshold), float(reference_coh_threshold))))
    reference_mask, mask_stats = _select_support_mask(
        base_mask=base_mask,
        amp_valid=amp_valid,
        disp_valid=disp_valid,
        coh=coh,
        selection_threshold=selection_threshold,
    )

    reference_count = int(reference_mask.sum())
    if reference_count <= 0:
        raise RuntimeError("Failed to select any reference pixels for displacement normalization.")

    stats.update(
        {
            "reference_count": reference_count,
            "reference_ratio": float(reference_mask.mean()),
            "selection_threshold": float(mask_stats["selection_threshold"]),
            "fallback": str(mask_stats["fallback"]),
        }
    )
    return float(np.median(disp_m_raw[reference_mask])), reference_mask, stats


def compute_deramp_surface(
    disp_m: np.ndarray,
    amp: np.ndarray,
    coh: np.ndarray,
    coh_threshold: float,
    deramp_mode: str,
    deramp_coh_threshold: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | int | str | bool]]:
    amp_valid = np.isfinite(amp) & (amp != 0)
    coh_finite = np.isfinite(coh)
    disp_valid = np.isfinite(disp_m)
    base_mask = amp_valid & coh_finite & disp_valid

    normalized_mode = str(deramp_mode or DEFAULT_DERAMP_MODE).strip().lower()
    if normalized_mode not in DERAMP_MODE_CHOICES:
        raise ValueError(f"Unsupported deramp mode: {deramp_mode}")

    empty_surface = np.zeros_like(disp_m, dtype=np.float32)
    stats: dict[str, float | int | str | bool] = {
        "mode": normalized_mode,
        "applied": False,
        "fit_count": 0,
        "fit_ratio": 0.0,
        "selection_threshold": 0.0,
        "fallback": "",
        "sample_step": 0,
        "sample_count": 0,
    }
    if normalized_mode == "none":
        return empty_surface, base_mask, stats
    if not base_mask.any():
        return empty_surface, base_mask, stats

    selection_threshold = min(1.0, max(0.0, max(float(coh_threshold), float(deramp_coh_threshold))))
    fit_mask, mask_stats = _select_support_mask(
        base_mask=base_mask,
        amp_valid=amp_valid,
        disp_valid=disp_valid,
        coh=coh,
        selection_threshold=selection_threshold,
    )
    fit_count = int(fit_mask.sum())
    stats.update(
        {
            "fit_count": fit_count,
            "fit_ratio": float(fit_mask.mean()),
            "selection_threshold": float(mask_stats["selection_threshold"]),
            "fallback": str(mask_stats["fallback"]),
        }
    )
    if fit_count < 3:
        stats["fallback"] = "insufficient_support"
        return empty_surface, fit_mask, stats

    yy, xx = np.indices(disp_m.shape, dtype=np.float64)
    xs = xx[fit_mask]
    ys = yy[fit_mask]
    zs = disp_m[fit_mask].astype(np.float64)
    sample_step = max(1, fit_count // 250_000)
    if sample_step > 1:
        xs = xs[::sample_step]
        ys = ys[::sample_step]
        zs = zs[::sample_step]
    sample_count = int(zs.size)
    stats["sample_step"] = int(sample_step)
    stats["sample_count"] = sample_count
    if sample_count < 3:
        stats["fallback"] = "insufficient_sample"
        return empty_surface, fit_mask, stats

    design = np.column_stack([xs, ys, np.ones_like(xs)])
    coeffs, _, _, _ = np.linalg.lstsq(design, zs, rcond=None)
    plane = (
        coeffs[0] * xx
        + coeffs[1] * yy
        + coeffs[2]
    ).astype(np.float32)
    stats.update(
        {
            "applied": True,
            "coef_x_per_pixel": float(coeffs[0]),
            "coef_y_per_pixel": float(coeffs[1]),
            "intercept_m": float(coeffs[2]),
            "left_right_delta_m": float(coeffs[0] * max(disp_m.shape[1] - 1, 0)),
            "top_bottom_delta_m": float(coeffs[1] * max(disp_m.shape[0] - 1, 0)),
        }
    )
    return plane, fit_mask, stats


def export_products(
    work_dir: Path,
    output_dir: Path,
    prefix: str,
    wavelength: float,
    coh_threshold: float,
    reference_mode: str = DEFAULT_REFERENCE_MODE,
    reference_coh_threshold: float = DEFAULT_REFERENCE_COH_THRESHOLD,
    deramp_mode: str = DEFAULT_DERAMP_MODE,
    deramp_coh_threshold: float = DEFAULT_DERAMP_COH_THRESHOLD,
    include_disp_full: bool = False,
    nodata: float = DEFAULT_NODATA,
) -> dict[str, Path]:
    unw_path = work_dir / "interferogram" / "filt_topophase.unw.geo.vrt"
    cor_path = work_dir / "interferogram" / "topophase.cor.geo.vrt"
    phase_source = _resolve_phase_source(work_dir)
    phase_path = Path(str(phase_source["phase_path"]))
    mask_path = Path(str(phase_source["mask_path"])) if str(phase_source["mask_path"]) else None

    if not unw_path.exists():
        raise FileNotFoundError(f"Missing unwrapped product: {unw_path}")
    if not cor_path.exists():
        raise FileNotFoundError(f"Missing coherence product: {cor_path}")
    if not phase_path.exists():
        raise FileNotFoundError(f"Missing phase source product: {phase_path}")

    unw_ds = gdal.Open(str(unw_path))
    cor_ds = gdal.Open(str(cor_path))
    phase_ds = gdal.Open(str(phase_path))
    mask_ds = gdal.Open(str(mask_path)) if mask_path is not None else None
    if unw_ds is None or cor_ds is None or phase_ds is None:
        raise RuntimeError("Failed to open ISCE2 geo products with GDAL.")

    amp = unw_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    if bool(phase_source["ionosphere_corrected"]):
        phase = phase_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    else:
        phase = unw_ds.GetRasterBand(2).ReadAsArray().astype(np.float32)

    coh_band = 2 if cor_ds.RasterCount >= 2 else 1
    coh = cor_ds.GetRasterBand(coh_band).ReadAsArray().astype(np.float32)
    coh_valid = np.isfinite(coh) & (coh > 0)
    amp_valid = np.isfinite(amp) & (amp != 0)
    ionosphere_mask_valid = None
    if mask_ds is not None:
        ionosphere_mask = mask_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
        ionosphere_mask_valid = np.isfinite(ionosphere_mask) & (ionosphere_mask > 0)

    disp_m_raw = phase * wavelength / (4.0 * np.pi)
    reference_offset_m, reference_mask, reference_stats = compute_reference_offset(
        disp_m_raw=disp_m_raw,
        amp=amp,
        coh=coh,
        coh_threshold=coh_threshold,
        reference_mode=reference_mode,
        reference_coh_threshold=reference_coh_threshold,
    )
    disp_m_ref = disp_m_raw - reference_offset_m
    deramp_surface_m, deramp_mask, deramp_stats = compute_deramp_surface(
        disp_m=disp_m_ref,
        amp=amp,
        coh=coh,
        coh_threshold=coh_threshold,
        deramp_mode=deramp_mode,
        deramp_coh_threshold=deramp_coh_threshold,
    )
    disp_m = disp_m_ref - deramp_surface_m
    disp_m_full = disp_m.copy()

    mask = (~amp_valid) | (~np.isfinite(disp_m)) | (~np.isfinite(coh)) | (coh < coh_threshold)
    if ionosphere_mask_valid is not None:
        mask |= ~ionosphere_mask_valid
    disp_m_raw_masked = disp_m_raw.copy()
    disp_m_raw_masked[mask] = nodata
    disp_m_ref_masked = disp_m_ref.copy()
    disp_m_ref_masked[mask] = nodata
    disp_m_masked = disp_m.copy()
    disp_m_masked[mask] = nodata
    disp_m_full[(~amp_valid) | (~np.isfinite(disp_m_full))] = nodata

    coh_out = coh.copy()
    coh_out[~coh_valid] = nodata

    output_dir.mkdir(parents=True, exist_ok=True)
    out_disp = output_dir / f"{prefix}_disp.tif"
    out_disp_raw = output_dir / f"{prefix}_disp_raw.tif"
    out_disp_ref = output_dir / f"{prefix}_disp_ref.tif"
    out_coh = output_dir / f"{prefix}_coh.tif"
    out_meta = output_dir / f"{prefix}_disp_meta.json"

    write_geotiff(disp_m_raw_masked, unw_ds, out_disp_raw, nodata)
    write_geotiff(disp_m_ref_masked, unw_ds, out_disp_ref, nodata)
    write_geotiff(disp_m_masked, unw_ds, out_disp, nodata)
    write_geotiff(coh_out, cor_ds, out_coh, nodata)
    out_disp_full = None
    if include_disp_full:
        out_disp_full = output_dir / f"{prefix}_disp_full.tif"
        write_geotiff(disp_m_full, unw_ds, out_disp_full, nodata)

    valid_raw_masked = disp_m_raw_masked[disp_m_raw_masked != nodata]
    valid_ref_masked = disp_m_ref_masked[disp_m_ref_masked != nodata]
    valid_disp = disp_m_masked[disp_m_masked != nodata]
    valid_coh = coh_out[coh_out != nodata]
    valid_full = disp_m_full[disp_m_full != nodata] if include_disp_full else np.array([], dtype=np.float32)
    valid_raw = disp_m_raw[amp_valid & np.isfinite(disp_m_raw)]
    using_reference = str(reference_stats["mode"]) != "none"
    using_deramp = bool(deramp_stats["applied"])

    meta_payload = {
        "work_dir": str(work_dir),
        "output_dir": str(output_dir),
        "prefix": prefix,
        "coh_threshold": float(coh_threshold),
        "phase_source": {
            "kind": str(phase_source["phase_source"]),
            "path": str(phase_path),
            "ionosphere_corrected": bool(phase_source["ionosphere_corrected"]),
            "mask_path": str(mask_path) if mask_path is not None else "",
            "mask_applied": bool(ionosphere_mask_valid is not None),
            "mask_valid_ratio": float(ionosphere_mask_valid.mean()) if ionosphere_mask_valid is not None else None,
        },
        "reference": {
            **reference_stats,
            "offset_m": float(reference_offset_m),
            "support_count": int(reference_mask.sum()),
        },
        "deramp": {
            **deramp_stats,
            "support_count": int(deramp_mask.sum()),
        },
        "ranges_m": {
            "raw_valid": [float(valid_raw.min()), float(valid_raw.max())] if valid_raw.size else [],
            "raw_masked": [float(valid_raw_masked.min()), float(valid_raw_masked.max())] if valid_raw_masked.size else [],
            "ref_masked": [float(valid_ref_masked.min()), float(valid_ref_masked.max())] if valid_ref_masked.size else [],
            "final_masked": [float(valid_disp.min()), float(valid_disp.max())] if valid_disp.size else [],
            "final_full": [float(valid_full.min()), float(valid_full.max())] if valid_full.size else [],
        },
    }
    out_meta.write_text(json.dumps(meta_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Work dir:                {work_dir}")
    print(f"Output prefix:           {prefix}")
    print(f"Phase source:            {phase_source['phase_source']}")
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
    print(f"Deramp mode:             {deramp_stats['mode']}")
    if using_deramp:
        print(
            "Deramp coh floor:       "
            f"{float(deramp_stats['selection_threshold']):.2f}"
        )
        print(
            "Deramp pixel ratio:     "
            f"{float(deramp_stats['fit_ratio'])*100:.2f}%"
        )
        print(
            "Deramp plane delta:     "
            f"dx={float(deramp_stats['left_right_delta_m']):.4f} m, "
            f"dy={float(deramp_stats['top_bottom_delta_m']):.4f} m"
        )
        if deramp_stats["fallback"]:
            print(f"Deramp fallback:         {deramp_stats['fallback']}")
    elif deramp_stats["fallback"]:
        print(f"Deramp fallback:         {deramp_stats['fallback']}")
    print(f"Unwrap support ratio:    {amp_valid.mean()*100:.2f}%")
    print(f"Coherence support ratio: {coh_valid.mean()*100:.2f}%")
    print(f"Masked disp ratio:       {(disp_m_masked != nodata).mean()*100:.2f}%")
    if valid_raw.size:
        print(f"Raw disp range:          [{valid_raw.min():.4f}, {valid_raw.max():.4f}] m")
    if valid_raw_masked.size:
        print(f"Raw masked range:        [{valid_raw_masked.min():.4f}, {valid_raw_masked.max():.4f}] m")
    if valid_ref_masked.size:
        label = "Ref disp range" if using_reference else "Ref disp range"
        print(f"{label + ':':24}[{valid_ref_masked.min():.4f}, {valid_ref_masked.max():.4f}] m")
    if valid_disp.size:
        label = "Final disp range" if using_reference or using_deramp else "Disp range"
        print(f"{label + ':':24}[{valid_disp.min():.4f}, {valid_disp.max():.4f}] m")
    if include_disp_full and valid_full.size:
        label = "Final full disp range" if using_reference or using_deramp else "Full disp range"
        print(f"{label + ':':24}[{valid_full.min():.4f}, {valid_full.max():.4f}] m")
    if valid_coh.size:
        print(f"Coherence range:         [{valid_coh.min():.4f}, {valid_coh.max():.4f}]")
    print(f"Wrote:                   {out_disp_raw}")
    print(f"Wrote:                   {out_disp_ref}")
    print(f"Wrote:                   {out_disp}")
    if out_disp_full is not None:
        print(f"Wrote:                   {out_disp_full}")
    print(f"Wrote:                   {out_coh}")
    print(f"Wrote:                   {out_meta}")

    unw_ds = None
    cor_ds = None
    phase_ds = None
    mask_ds = None
    outputs: dict[str, Path] = {
        "disp_raw": out_disp_raw,
        "disp_ref": out_disp_ref,
        "disp": out_disp,
        "coh": out_coh,
        "meta": out_meta,
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
        deramp_mode=args.deramp_mode,
        deramp_coh_threshold=args.deramp_coh_threshold,
        include_disp_full=args.include_disp_full,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
