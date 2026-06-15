"""ENVI raster parsing and GeoTIFF writing."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import Affine
except Exception:  # pragma: no cover - optional at runtime
    rasterio = None
    CRS = None
    Affine = None


ENVI_DTYPES = {
    1: np.uint8,
    2: np.int16,
    3: np.int32,
    4: np.float32,
    5: np.float64,
    12: np.uint16,
    13: np.uint32,
    14: np.int64,
    15: np.uint64,
}


@dataclass(frozen=True)
class EnviInfo:
    path: Path
    hdr_path: Path
    samples: int
    lines: int
    bands: int
    header_offset: int
    dtype: np.dtype
    byte_order: int
    interleave: str
    x0: float
    y0: float
    dx: float
    dy: float
    crs_wkt: str | None

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        left = self.x0
        top = self.y0
        right = left + self.samples * self.dx
        bottom = top - self.lines * self.dy
        return left, bottom, right, top

    @property
    def transform(self):
        if Affine is None:
            return None
        return Affine(self.dx, 0.0, self.x0, 0.0, -self.dy, self.y0)


def read_hdr_text(data_path: Path) -> tuple[Path, str]:
    hdr_path = data_path.with_suffix(data_path.suffix + ".hdr") if data_path.suffix else Path(str(data_path) + ".hdr")
    if not hdr_path.exists():
        alt = data_path.with_suffix(".hdr")
        if alt.exists():
            hdr_path = alt
    if not hdr_path.exists():
        raise FileNotFoundError(f"ENVI header not found for {data_path}")
    return hdr_path, hdr_path.read_text(encoding="utf-8", errors="ignore")


def hdr_value(text: str, key: str, default: str | None = None) -> str:
    match = re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", text)
    if match:
        return match.group(1).strip()
    if default is not None:
        return default
    raise ValueError(f"Missing ENVI header key: {key}")


def parse_map_info(text: str) -> tuple[float, float, float, float]:
    match = re.search(r"(?is)map info\s*=\s*\{(.+?)\}", text)
    if not match:
        raise ValueError("Missing map info in ENVI header")
    parts = [p.strip() for p in match.group(1).replace("\n", " ").split(",")]
    if len(parts) < 7:
        raise ValueError(f"Unexpected map info: {match.group(0)}")
    return float(parts[3]), float(parts[4]), abs(float(parts[5])), abs(float(parts[6]))


def parse_crs_wkt(text: str) -> str | None:
    match = re.search(r"(?is)coordinate system string\s*=\s*\{(.+?)\}", text)
    return match.group(1).strip() if match else None


def parse_envi(path: Path) -> EnviInfo:
    hdr_path, text = read_hdr_text(path)
    dtype_code = int(hdr_value(text, "data type"))
    if dtype_code not in ENVI_DTYPES:
        raise ValueError(f"Unsupported ENVI data type: {dtype_code}")
    x0, y0, dx, dy = parse_map_info(text)
    dtype = np.dtype(ENVI_DTYPES[dtype_code])
    byte_order = int(hdr_value(text, "byte order", "0"))
    if byte_order == 1:
        dtype = dtype.newbyteorder(">")
    else:
        dtype = dtype.newbyteorder("<")
    return EnviInfo(
        path=path,
        hdr_path=hdr_path,
        samples=int(hdr_value(text, "samples")),
        lines=int(hdr_value(text, "lines")),
        bands=int(hdr_value(text, "bands", "1")),
        header_offset=int(hdr_value(text, "header offset", "0")),
        dtype=dtype,
        byte_order=byte_order,
        interleave=hdr_value(text, "interleave", "bsq").lower(),
        x0=x0,
        y0=y0,
        dx=dx,
        dy=dy,
        crs_wkt=parse_crs_wkt(text),
    )


def read_envi_band(info: EnviInfo) -> np.ndarray:
    if info.bands != 1 or info.interleave != "bsq":
        raise ValueError("This baseline expects one-band BSQ ENVI inputs")
    count = info.lines * info.samples
    data = np.memmap(info.path, dtype=info.dtype, mode="r", offset=info.header_offset, shape=(count,))
    arr = np.asarray(data.reshape(info.lines, info.samples), dtype=np.float32)
    arr = arr.copy()
    arr[~np.isfinite(arr)] = np.nan
    return arr


def read_dem_for_sar(dem_info: EnviInfo, sar_info: EnviInfo) -> np.ndarray:
    left, bottom, right, top = sar_info.bounds
    pad = 2
    col0 = max(0, int(math.floor((left - dem_info.x0) / dem_info.dx)) - pad)
    col1 = min(dem_info.samples, int(math.ceil((right - dem_info.x0) / dem_info.dx)) + pad)
    row0 = max(0, int(math.floor((dem_info.y0 - top) / dem_info.dy)) - pad)
    row1 = min(dem_info.lines, int(math.ceil((dem_info.y0 - bottom) / dem_info.dy)) + pad)
    if col1 <= col0 or row1 <= row0:
        raise ValueError("SAR image does not overlap DEM")

    mm = np.memmap(dem_info.path, dtype=dem_info.dtype, mode="r", offset=dem_info.header_offset, shape=(dem_info.lines, dem_info.samples))
    dem_window = np.asarray(mm[row0:row1, col0:col1], dtype=np.float32).copy()
    dem_window[~np.isfinite(dem_window)] = np.nan

    x = sar_info.x0 + (np.arange(sar_info.samples) + 0.5) * sar_info.dx
    y = sar_info.y0 - (np.arange(sar_info.lines) + 0.5) * sar_info.dy
    dem_cols = np.clip(np.rint((x - dem_info.x0) / dem_info.dx - 0.5).astype(np.int64) - col0, 0, dem_window.shape[1] - 1)
    dem_rows = np.clip(np.rint((dem_info.y0 - y) / dem_info.dy - 0.5).astype(np.int64) - row0, 0, dem_window.shape[0] - 1)
    return dem_window[dem_rows[:, None], dem_cols[None, :]]


def write_tif(path: Path, arr: np.ndarray, info: EnviInfo, dtype: str, nodata=None) -> None:
    if rasterio is None:
        return
    crs = None
    if CRS is not None:
        try:
            crs = CRS.from_epsg(4326)
        except Exception:
            crs = None
    profile = {
        "driver": "GTiff",
        "height": arr.shape[0],
        "width": arr.shape[1],
        "count": 1,
        "dtype": dtype,
        "compress": "deflate",
        "predictor": 2 if dtype.startswith("float") else 1,
        "transform": info.transform,
        "nodata": nodata,
    }
    if crs is not None:
        profile["crs"] = crs
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype(dtype), 1)

