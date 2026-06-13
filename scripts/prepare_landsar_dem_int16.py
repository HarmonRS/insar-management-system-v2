#!/usr/bin/env python3
"""
Prepare reusable LandSAR DEM GeoTIFFs.

The script converts large DEM rasters to uncompressed Int16 GeoTIFFs with a
stable nodata value. It streams data by windows, so it can process the 10 m
Heilongjiang DEM and the COPDEM China DEM without loading them into memory.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _configure_proj_lib() -> None:
    candidates = [
        Path(sys.prefix) / "Library" / "share" / "proj",
        Path(sys.prefix) / "lib" / "site-packages" / "rasterio" / "proj_data",
        Path(sys.prefix) / "Lib" / "site-packages" / "rasterio" / "proj_data",
    ]
    for data_dir in candidates:
        if Path(data_dir, "proj.db").is_file():
            os.environ["PROJ_LIB"] = str(data_dir)
            os.environ["PROJ_DATA"] = str(data_dir)
            return

    try:
        import pyproj

        data_dir = pyproj.datadir.get_data_dir()
        if data_dir and Path(data_dir, "proj.db").is_file():
            os.environ["PROJ_LIB"] = str(data_dir)
            os.environ["PROJ_DATA"] = str(data_dir)
    except Exception:
        return


_configure_proj_lib()


try:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import array_bounds
    from rasterio.windows import Window, from_bounds
except Exception as exc:  # pragma: no cover - CLI dependency guard
    raise SystemExit(
        "rasterio/numpy are required. Run this with the project Python, for example:\n"
        r"  C:\ProgramData\anaconda3\envs\InSAR\python.exe scripts\prepare_landsar_dem_int16.py"
    ) from exc


DEFAULT_DEM_ROOT = Path(r"D:\DEM")
DEFAULT_OUTPUT_ROOT = DEFAULT_DEM_ROOT / "landsar_prepared"
DEFAULT_NODATA = -32768
DEFAULT_CRS = CRS.from_epsg(4326)
HEILONGJIANG_10M_DEM = "HeiLongJiang10M_DEM.tif"
HEILONGJIANG_10M_ALIAS = "\u9ed1\u9f99\u6c5f\u770110M_DEM"

SOURCE_ALIASES = {
    "HeiLongJiang10M_DEM": HEILONGJIANG_10M_DEM,
    "Heilongjiang10M_DEM": HEILONGJIANG_10M_DEM,
    HEILONGJIANG_10M_ALIAS: HEILONGJIANG_10M_DEM,
    "COPDEM_GLO30_China_4326_DEM": "COPDEM_GLO30_China_4326_DEM",
}


def _source_path(alias_or_path: str, dem_root: Path) -> Path:
    text = str(alias_or_path or "").strip().strip('"')
    if not text:
        raise ValueError("source must not be empty")

    mapped = SOURCE_ALIASES.get(text, text)
    candidate = Path(mapped)
    if not candidate.is_absolute():
        candidate = dem_root / mapped
    if candidate.exists():
        return candidate

    for suffix in (".tif", ".tiff", ".vrt", ".jp2"):
        with_suffix = candidate.with_suffix(suffix)
        if with_suffix.exists():
            return with_suffix
    raise FileNotFoundError(f"DEM source not found: {alias_or_path} -> {candidate}")


def _safe_stem(alias_or_path: str, source: Path) -> str:
    text = str(alias_or_path or "").strip()
    if text in SOURCE_ALIASES:
        return text
    return source.stem or source.name


def _parse_bbox(value: str | None) -> Optional[tuple[float, float, float, float]]:
    if not value:
        return None
    parts = [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if len(parts) != 4:
        raise ValueError("--bbox must be xmin,ymin,xmax,ymax")
    xmin, ymin, xmax, ymax = (float(part) for part in parts)
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("--bbox requires xmin < xmax and ymin < ymax")
    return xmin, ymin, xmax, ymax


def _align_window(window: Window, width: int, height: int) -> Window:
    col_off = max(0, int(math.floor(window.col_off)))
    row_off = max(0, int(math.floor(window.row_off)))
    col_stop = min(width, int(math.ceil(window.col_off + window.width)))
    row_stop = min(height, int(math.ceil(window.row_off + window.height)))
    if col_stop <= col_off or row_stop <= row_off:
        raise ValueError("requested bbox does not overlap source raster")
    return Window(col_off, row_off, col_stop - col_off, row_stop - row_off)


def _iter_windows(width: int, height: int, block_size: int) -> Iterable[Window]:
    step = max(64, int(block_size))
    for row in range(0, height, step):
        h = min(step, height - row)
        for col in range(0, width, step):
            w = min(step, width - col)
            yield Window(col, row, w, h)


def _convert_array(data: np.ma.MaskedArray | np.ndarray, nodata: int) -> np.ndarray:
    if isinstance(data, np.ma.MaskedArray):
        mask = np.ma.getmaskarray(data)
        array = np.asarray(data.filled(np.nan), dtype="float32")
    else:
        array = np.asarray(data, dtype="float32")
        mask = np.zeros(array.shape, dtype=bool)

    invalid = mask | ~np.isfinite(array)
    rounded = np.rint(array)
    rounded = np.clip(rounded, nodata + 1, 32767)
    out = rounded.astype("int16", copy=False)
    if invalid.any():
        out = out.copy()
        out[invalid] = nodata
    return out


def _format_gib(byte_count: int) -> str:
    return f"{byte_count / (1024 ** 3):.3f} GiB"


def convert_dem(
    source_text: str,
    *,
    dem_root: Path,
    output_root: Path,
    bbox: Optional[tuple[float, float, float, float]],
    suffix: str,
    nodata: int,
    block_size: int,
    overwrite: bool,
    dry_run: bool,
) -> Path:
    source = _source_path(source_text, dem_root)
    stem = _safe_stem(source_text, source)
    if suffix:
        stem = f"{stem}_{suffix.strip('_')}"
    target = output_root / f"{stem}_int16.tif"

    with rasterio.open(source) as src:
        src_crs = src.crs or DEFAULT_CRS
        if bbox:
            window = _align_window(from_bounds(*bbox, transform=src.transform), src.width, src.height)
        else:
            window = Window(0, 0, src.width, src.height)
        window = Window(int(window.col_off), int(window.row_off), int(window.width), int(window.height))
        transform = src.window_transform(window)
        bounds = array_bounds(int(window.height), int(window.width), transform)
        estimated_bytes = int(window.width) * int(window.height) * np.dtype("int16").itemsize

        print(f"Source: {source}")
        print(f"  driver={src.driver} dtype={src.dtypes[0]} size={src.width}x{src.height} crs={src.crs or 'EPSG:4326 assumed'}")
        print(f"  output window={int(window.width)}x{int(window.height)} bounds={tuple(round(v, 8) for v in bounds)}")
        print(f"  target={target}")
        print(f"  estimated raw int16 size={_format_gib(estimated_bytes)}")

        if dry_run:
            return target
        output_root.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise FileExistsError(f"target exists; pass --overwrite to replace it: {target}")

        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            height=int(window.height),
            width=int(window.width),
            count=1,
            dtype="int16",
            crs=src_crs,
            transform=transform,
            nodata=nodata,
            compress="NONE",
            tiled=True,
            blockxsize=512,
            blockysize=512,
            BIGTIFF="YES",
            interleave="band",
        )
        profile.pop("photometric", None)
        profile.pop("predictor", None)

        if target.exists():
            target.unlink()

        with rasterio.open(target, "w", **profile) as dst:
            total_pixels = int(window.width) * int(window.height)
            done_pixels = 0
            last_percent = -1
            for rel_window in _iter_windows(int(window.width), int(window.height), block_size):
                src_window = Window(
                    window.col_off + rel_window.col_off,
                    window.row_off + rel_window.row_off,
                    rel_window.width,
                    rel_window.height,
                )
                data = src.read(1, window=src_window, masked=True)
                dst.write(_convert_array(data, nodata), 1, window=rel_window)
                done_pixels += int(rel_window.width) * int(rel_window.height)
                percent = int(done_pixels * 100 / max(1, total_pixels))
                if percent != last_percent and (percent % 5 == 0 or percent == 100):
                    print(f"  progress={percent}%")
                    last_percent = percent

    actual_size = target.stat().st_size if target.exists() else 0
    print(f"Done: {target} ({_format_gib(actual_size)})")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert large DEMs to reusable LandSAR Int16 GeoTIFFs."
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help=(
            "Source alias/path. Can be repeated. Defaults to HeiLongJiang10M_DEM "
            "and COPDEM_GLO30_China_4326_DEM."
        ),
    )
    parser.add_argument("--dem-root", default=str(DEFAULT_DEM_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument(
        "--bbox",
        default="",
        help="Optional crop bounds as xmin,ymin,xmax,ymax in EPSG:4326. Omit to convert full raster.",
    )
    parser.add_argument("--suffix", default="landsar")
    parser.add_argument("--nodata", type=int, default=DEFAULT_NODATA)
    parser.add_argument("--block-size", type=int, default=2048)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources = args.source or ["HeiLongJiang10M_DEM", "COPDEM_GLO30_China_4326_DEM"]
    bbox = _parse_bbox(args.bbox)
    for source in sources:
        convert_dem(
            source,
            dem_root=Path(args.dem_root),
            output_root=Path(args.output_root),
            bbox=bbox,
            suffix=args.suffix,
            nodata=args.nodata,
            block_size=args.block_size,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
