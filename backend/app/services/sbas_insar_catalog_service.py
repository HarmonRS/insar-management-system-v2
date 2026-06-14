from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import math
import mimetypes
import os
import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from geoalchemy2.shape import from_shape
from shapely.geometry import Polygon
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import ResultAssetORM, ResultCatalogStateORM, ResultIssueORM, ResultProductORM
from .admin_region_lookup_service import lookup_admin_region_for_point
from .landsar_sbas_service import landsar_sbas_service
from .sbas_insar_production_service import sbas_insar_production_service


SBAS_INSAR_CATALOG_NAME = "sbas_insar"
JOB_TYPE_REBUILD_SBAS_INSAR_CATALOG = "REBUILD_SBAS_INSAR_CATALOG"
TASK_TYPE_REBUILD_SBAS_INSAR_CATALOG = "REBUILD_SBAS_INSAR_CATALOG"

_READY_STATUSES = {
    "PRODUCTS_READY",
    "MONITOR_POINTS_READY",
    "WORKFLOW_COMPLETED",
    "LANDSAR_SBAS_COMPLETED",
    "LANDSAR_SBAS_PARTIAL",
}
_REQUIRED_ASSET_ROLES = {"primary_geotiff", "quality_geotiff"}
_LANDSAR_REQUIRED_ASSET_ROLES = {"primary_geotiff"}
_WGS84_GEOGCS_WKT = (
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
)

_CORE_ASSETS = (
    ("run_manifest", "Run manifest", "run_manifest.json", True, False),
    ("stack_manifest", "Stack manifest", "stack_manifest.json", True, False),
    ("workflow_summary", "Workflow summary", "workflow_summary.json", False, False),
    ("product_summary", "Product summary", "product_summary.json", False, False),
    ("quality_summary", "Quality summary", "quality_summary.json", False, False),
    ("monitor_points_summary", "Monitor points summary", "monitor_points_summary.json", False, False),
    (
        "point_vector_summary",
        "LOS point-vector summary",
        "publish/vectors/los_rate_points_summary.json",
        False,
        False,
    ),
    (
        "point_vector_geojson_gz",
        "LOS point-vector GeoJSON.gz",
        "publish/vectors/los_rate_points.geojson.gz",
        False,
        False,
    ),
    (
        "primary_geocoded_preview",
        "LOS velocity preview, toward radar positive",
        "publish/geotiff/los_rate_toward_m_per_year.hls.geo_preview.png",
        False,
        True,
    ),
    (
        "quality_geocoded_preview",
        "LOS velocity sigma preview",
        "publish/geotiff/los_sigma_m_per_year.cc.geo_preview.png",
        False,
        False,
    ),
    (
        "primary_geotiff",
        "LOS velocity GeoTIFF, toward radar positive",
        "publish/geotiff/los_rate_toward_m_per_year.tif",
        True,
        True,
    ),
    (
        "alternate_geotiff",
        "LOS velocity GeoTIFF, away from radar positive",
        "publish/geotiff/los_rate_away_m_per_year.tif",
        False,
        False,
    ),
    (
        "quality_geotiff",
        "LOS velocity sigma GeoTIFF",
        "publish/geotiff/los_sigma_m_per_year.tif",
        True,
        False,
    ),
    (
        "primary_rgb_geotiff",
        "LOS velocity RGB GeoTIFF",
        "publish/geotiff/los_rate_toward_m_per_year.hls.geo_rgb.tif",
        False,
        False,
    ),
    (
        "quality_rgb_geotiff",
        "LOS velocity sigma RGB GeoTIFF",
        "publish/geotiff/los_sigma_m_per_year.cc.geo_rgb.tif",
        False,
        False,
    ),
    ("gamma_phase_rate", "Gamma phase-rate GeoTIFF", "publish/geotiff/ts_rate_rad_per_year.tif", False, False),
    ("gamma_sigma_rate", "Gamma sigma-rate GeoTIFF", "publish/geotiff/sigma_rate_rad_per_year.tif", False, False),
    ("height_correction", "Height correction GeoTIFF", "publish/geotiff/hgt_correction_m.tif", False, False),
)

_LANDSAR_CORE_ASSETS = (
    ("run_manifest", "Run manifest", "run_manifest.json", True, False),
    ("stack_manifest", "Task/Input_Data manifest", "stack_manifest.json", True, False),
    ("workflow_summary", "LandSAR SBAS workflow summary", "workflow_summary.json", True, False),
    ("product_summary", "LandSAR SBAS product summary", "product_summary.json", False, False),
    ("quality_summary", "LandSAR SBAS quality summary", "quality_summary.json", False, False),
    ("command_manifest", "LandSAR SBAS command manifest", "landsar_command_manifest.json", False, False),
    ("native_console_log", "LandSAR SBAS console log", "native_logs", False, False),
    ("primary_preview", "LandSAR LOS preview", "publish/landsar/preview.png", False, True),
    ("primary_geotiff", "LandSAR LOS time-series GeoTIFF", "publish/landsar/los_timeseries.tif", True, True),
    ("secondary_geotiff", "LandSAR post-raster GeoTIFF", "publish/landsar/post_raster.tif", False, False),
)

_EXPERT_GAMMA_CORE_ASSETS = (
    ("run_manifest", "Run manifest", "run_manifest.json", True, False),
    ("stack_manifest", "Stack manifest", "stack_manifest.json", True, False),
    ("workflow_summary", "Workflow summary", "workflow_summary.json", False, False),
    ("monitor_points_summary", "Monitor points summary", "monitor_points_summary.json", False, False),
    (
        "unwrapped_phase_summary",
        "Expert Gamma geocoded unwrapped phase summary",
        "publish/geotiff/unwrapped/unwrapped_phase_summary.json",
        False,
        False,
    ),
    (
        "unwrapped_phase_radar_colorbar",
        "Expert Gamma rmg.cm unwrapped phase radar colorbar",
        "publish/geotiff/unwrapped/unwrapped_phase_rmg_colorbar.png",
        False,
        False,
    ),
    (
        "point_vector_summary",
        "Expert Gamma LOS point-vector summary",
        "publish/vectors/los_rate_points_summary.json",
        False,
        False,
    ),
    (
        "point_vector_geojson_gz",
        "Expert Gamma LOS point-vector GeoJSON.gz",
        "publish/vectors/los_rate_points.geojson.gz",
        False,
        False,
    ),
    (
        "primary_geocoded_preview",
        "Expert Gamma geo_los_def_rate RGB PNG preview",
        "publish/geotiff/geo_los_def_rate_rgb_preview.png",
        False,
        True,
    ),
    (
        "primary_rate_color_preview",
        "Expert Gamma pure geo_los_def_rate hls.cm PNG preview",
        "publish/geotiff/geo_los_def_rate_pure_hls_preview.png",
        False,
        False,
    ),
    (
        "primary_geotiff",
        "Expert Gamma geo_los_def_rate GeoTIFF",
        "publish/geotiff/geo_los_def_rate.tif",
        True,
        True,
    ),
    (
        "primary_rgb_geotiff",
        "Expert Gamma geo_los_def_rate RGB GeoTIFF",
        "publish/geotiff/geo_los_def_rate_rgb.tif",
        False,
        False,
    ),
    (
        "primary_colorbar",
        "Expert Gamma hls.cm deformation-rate colorbar",
        "publish/geotiff/geo_los_def_rate_hls_colorbar.png",
        False,
        False,
    ),
    (
        "monitor_points",
        "Expert Gamma disp_prt_2d point time series",
        "publish/points/disp_point.txt",
        False,
        False,
    ),
    (
        "monitor_point_items",
        "Expert Gamma disp_prt_2d column definitions",
        "publish/points/items.txt",
        False,
        False,
    ),
    (
        "monitor_point_selection",
        "Expert Gamma disp_prt_2d selected radar points",
        "publish/points/disp_point_sel.txt",
        False,
        False,
    ),
    (
        "monitor_point_selection_metadata",
        "Expert Gamma monitor point selection metadata",
        "publish/points/disp_point_selection.json",
        False,
        False,
    ),
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_path(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return payload if isinstance(payload, dict) else {}


def _safe_read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return _read_json(path)
    except Exception:
        return {}


def _safe_float(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _read_gamma_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip().split()[0] if raw_value.strip() else ""
        if key:
            values[key] = value
    return values


def _read_gamma_int_param(path: Path, key: str) -> Optional[int]:
    return _safe_int(_read_gamma_key_values(path).get(key))


def _expert_gamma_work_run_dir(run_dir: Path) -> Path:
    run_id = run_dir.name
    candidates = [
        run_dir,
        Path(settings.GAMMA_SBAS_WORK_ROOT or "") / "runs" / run_id if settings.GAMMA_SBAS_WORK_ROOT else run_dir,
    ]
    seen: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_path(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if (
            (candidate / "sbas" / "disp.TS_tab").is_file()
            and (candidate / "sbas" / "mli.ave.par").is_file()
            and any((candidate / "dem").glob("*.lt_fine"))
        ):
            return candidate
    return run_dir


def _expert_gamma_lookup_path(work_run_dir: Path, manifest: dict[str, Any]) -> Path:
    lt_path = work_run_dir / "dem" / f"{manifest.get('reference_date') or ''}.lt_fine"
    if lt_path.is_file():
        return lt_path
    candidates = sorted((work_run_dir / "dem").glob("*.lt_fine"))
    return candidates[0] if candidates else lt_path


def _resolve_expert_gamma_tab_path(raw_path: str, *, work_run_dir: Path) -> Path:
    path = Path(_wsl_path_to_windows(raw_path))
    if path.is_file():
        return path
    run_id = work_run_dir.name
    parts = list(path.parts)
    if run_id in parts:
        index = parts.index(run_id)
        relative_parts = parts[index + 1 :]
        candidate = work_run_dir.joinpath(*relative_parts)
        if candidate.is_file():
            return candidate
    return path


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius_m = 6371008.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_m * math.asin(min(1.0, math.sqrt(a)))


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _stable_digest(*parts: Any, length: int = 20) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _asset_format(path: str) -> Optional[str]:
    lowered = path.lower()
    if lowered.endswith(".geojson.gz"):
        return "geojson.gz"
    ext = Path(path).suffix.lower()
    return {
        ".bmp": "bmp",
        ".csv": "csv",
        ".geo": "gamma_binary",
        ".gz": "gzip",
        ".json": "json",
        ".log": "log",
        ".png": "png",
        ".sh": "shell",
        ".tif": "geotiff",
        ".tiff": "geotiff",
        ".txt": "text",
    }.get(ext)


def _media_type(path: str) -> Optional[str]:
    lowered = path.lower()
    if lowered.endswith(".geojson.gz"):
        return "application/gzip"
    ext = Path(path).suffix.lower()
    explicit = {
        ".bmp": "image/bmp",
        ".csv": "text/csv",
        ".gz": "application/gzip",
        ".json": "application/json",
        ".log": "text/plain",
        ".png": "image/png",
        ".sh": "text/x-shellscript",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".txt": "text/plain",
    }
    return explicit.get(ext) or mimetypes.guess_type(path)[0]


def _build_rgb_geotiff_preview(source: Path, target: Path) -> Optional[str]:
    if target.is_file():
        return str(target)
    if not source.is_file():
        return None
    try:
        import numpy as np
        import rasterio
        from PIL import Image
        from rasterio.enums import Resampling
    except Exception:
        return None

    try:
        with rasterio.open(source) as src:
            scale = max(src.width / 1600, src.height / 1600, 1)
            out_w = max(1, int(src.width / scale))
            out_h = max(1, int(src.height / scale))
            mask = src.dataset_mask(out_shape=(out_h, out_w)) > 0
            if src.count >= 3:
                data = src.read(
                    [1, 2, 3],
                    out_shape=(3, out_h, out_w),
                    resampling=Resampling.bilinear,
                )
                rgb = np.moveaxis(data, 0, -1)
                if rgb.dtype != np.uint8:
                    rgb = np.clip(rgb, 0, 255).astype("uint8")
            else:
                data = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype("float32")
                finite = np.isfinite(data)
                valid = finite & mask
                if np.any(valid):
                    p2, p98 = np.nanpercentile(data[valid], [2, 98])
                    if not np.isfinite(p2) or not np.isfinite(p98) or p98 <= p2:
                        p2 = float(np.nanmin(data[valid]))
                        p98 = float(np.nanmax(data[valid]))
                    norm = np.clip((data - p2) / max(p98 - p2, 1e-6), 0, 1)
                else:
                    norm = np.zeros_like(data, dtype="float32")
                gray = np.where(np.isfinite(norm), norm * 255, 0).astype("uint8")
                rgb = np.dstack([gray, gray, gray])
            alpha = np.where(mask, 255, 0).astype("uint8")
            image = Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image.thumbnail((1600, 1600), resampling)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "PNG", optimize=True)
            return str(target)
    except Exception:
        return None


def _find_gamma_hls_colormap() -> Optional[Path]:
    candidates = [
        Path(r"\\wsl.localhost\Ubuntu-24.04\usr\local\GAMMA_SOFTWARE-20240627\DISP\cmaps\hls.cm"),
        Path(r"\\wsl$\Ubuntu-24.04\usr\local\GAMMA_SOFTWARE-20240627\DISP\cmaps\hls.cm"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _find_gamma_colormap(name: str) -> Optional[Path]:
    safe_name = Path(str(name or "")).name
    if not safe_name:
        return None
    candidates = [
        Path(r"\\wsl.localhost\Ubuntu-24.04\usr\local\GAMMA_SOFTWARE-20240627\DISP\cmaps") / safe_name,
        Path(r"\\wsl$\Ubuntu-24.04\usr\local\GAMMA_SOFTWARE-20240627\DISP\cmaps") / safe_name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _read_gamma_colormap(path: Path) -> list[tuple[int, int, int]]:
    colors: list[tuple[int, int, int]] = []
    if not path.is_file():
        return colors
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        try:
            red, green, blue = (max(0, min(255, int(float(value)))) for value in parts[:3])
        except ValueError:
            continue
        colors.append((red, green, blue))
    return colors


def _build_gamma_colormap_colorbar(
    target: Path,
    *,
    colormap_name: str,
    min_value: float,
    max_value: float,
    unit: str,
    title: str,
) -> Optional[str]:
    if target.is_file():
        return str(target)
    source = _find_gamma_colormap(colormap_name)
    colors = _read_gamma_colormap(source) if source else []
    if not colors:
        return None
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        width = 920
        bar_height = 34
        label_height = 78
        margin_x = 48
        margin_top = 14
        target.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (width, bar_height + label_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        gradient_width = width - margin_x * 2
        color_array = np.asarray(colors, dtype=np.uint8)
        for x in range(gradient_width):
            idx = int(round((x / max(1, gradient_width - 1)) * (len(color_array) - 1)))
            draw.line(
                [(margin_x + x, margin_top), (margin_x + x, margin_top + bar_height)],
                fill=tuple(int(value) for value in color_array[idx]) + (255,),
            )
        draw.rectangle(
            [margin_x, margin_top, margin_x + gradient_width, margin_top + bar_height],
            outline=(15, 23, 42, 180),
            width=1,
        )
        try:
            font = ImageFont.truetype("arial.ttf", 14)
            small_font = ImageFont.truetype("arial.ttf", 12)
        except Exception:
            font = ImageFont.load_default()
            small_font = font
        ticks = [
            (min_value, f"{min_value:g}"),
            (0.0, "0"),
            (max_value, f"{max_value:g}"),
        ]
        for value, label in ticks:
            ratio = (value - min_value) / max(max_value - min_value, 1e-6)
            x = margin_x + int(round(ratio * gradient_width))
            draw.line([(x, margin_top + bar_height), (x, margin_top + bar_height + 7)], fill=(15, 23, 42, 220), width=1)
            text = f"{label} {unit}".strip()
            bbox = draw.textbbox((0, 0), text, font=font)
            draw.text((x - (bbox[2] - bbox[0]) / 2, margin_top + bar_height + 10), text, fill=(15, 23, 42, 255), font=font)
        source_label = f"{title} / {colormap_name}"
        source_bbox = draw.textbbox((0, 0), source_label, font=small_font)
        draw.text(
            (width - margin_x - (source_bbox[2] - source_bbox[0]), margin_top + bar_height + 34),
            source_label,
            fill=(71, 85, 105, 255),
            font=small_font,
        )
        image.save(target, "PNG", optimize=True)
        return str(target)
    except Exception:
        return None


def _build_gamma_hls_colorbar(target: Path, *, min_mm_year: float = -80.0, max_mm_year: float = 80.0) -> Optional[str]:
    if target.is_file():
        return str(target)
    source = _find_gamma_hls_colormap()
    colors = _read_gamma_colormap(source) if source else []
    if not colors:
        return None
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        width = 900
        bar_height = 34
        label_height = 58
        margin_x = 46
        margin_top = 14
        target.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGBA", (width, bar_height + label_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        gradient_width = width - margin_x * 2
        color_array = np.asarray(colors, dtype=np.uint8)
        for x in range(gradient_width):
            idx = int(round((x / max(1, gradient_width - 1)) * (len(color_array) - 1)))
            draw.line(
                [(margin_x + x, margin_top), (margin_x + x, margin_top + bar_height)],
                fill=tuple(int(value) for value in color_array[idx]) + (255,),
            )
        draw.rectangle(
            [margin_x, margin_top, margin_x + gradient_width, margin_top + bar_height],
            outline=(15, 23, 42, 180),
            width=1,
        )
        ticks = [
            (min_mm_year, f"{min_mm_year:g}"),
            (0.0, "0"),
            (max_mm_year, f"{max_mm_year:g}"),
        ]
        try:
            font = ImageFont.truetype("arial.ttf", 14)
            small_font = ImageFont.truetype("arial.ttf", 12)
        except Exception:
            font = ImageFont.load_default()
            small_font = font
        for value, label in ticks:
            ratio = (value - min_mm_year) / max(max_mm_year - min_mm_year, 1e-6)
            x = margin_x + int(round(ratio * gradient_width))
            draw.line([(x, margin_top + bar_height), (x, margin_top + bar_height + 7)], fill=(15, 23, 42, 220), width=1)
            text = f"{label} mm/yr"
            bbox = draw.textbbox((0, 0), text, font=font)
            draw.text((x - (bbox[2] - bbox[0]) / 2, margin_top + bar_height + 10), text, fill=(15, 23, 42, 255), font=font)
        source_label = "Gamma hls.cm"
        source_bbox = draw.textbbox((0, 0), source_label, font=small_font)
        draw.text(
            (width - margin_x - (source_bbox[2] - source_bbox[0]), margin_top + bar_height + 33),
            source_label,
            fill=(71, 85, 105, 255),
            font=small_font,
        )
        image.save(target, "PNG", optimize=True)
        return str(target)
    except Exception:
        return None


def _build_gamma_hls_rate_preview(
    source: Path,
    target: Path,
    *,
    coverage_source: Optional[Path] = None,
    min_native: float = -0.08,
    max_native: float = 0.08,
) -> Optional[str]:
    if target.is_file():
        return str(target)
    if not source.is_file():
        return None
    colormap_path = _find_gamma_hls_colormap()
    colors = _read_gamma_colormap(colormap_path) if colormap_path else []
    if not colors:
        return None
    try:
        import numpy as np
        import rasterio
        from PIL import Image
        from rasterio.enums import Resampling
    except Exception:
        return None

    try:
        with rasterio.open(source) as src:
            scale = max(src.width / 1600, src.height / 1600, 1)
            out_w = max(1, int(src.width / scale))
            out_h = max(1, int(src.height / scale))
            data = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest).astype("float32")
            coverage = np.ones((out_h, out_w), dtype=bool)
            if coverage_source and coverage_source.is_file():
                with rasterio.open(coverage_source) as coverage_src:
                    if coverage_src.width == src.width and coverage_src.height == src.height and coverage_src.count >= 3:
                        coverage_rgb = coverage_src.read(
                            [1, 2, 3],
                            out_shape=(3, out_h, out_w),
                            resampling=Resampling.nearest,
                        )
                        coverage = np.any(coverage_rgb != 0, axis=0)
            valid = np.isfinite(data) & coverage & (data != 0.0)
            ratio = np.clip((data - min_native) / max(max_native - min_native, 1e-12), 0.0, 1.0)
            color_array = np.asarray(colors, dtype=np.uint8)
            indices = np.rint(ratio * (len(color_array) - 1)).astype(np.int32)
            rgb = color_array[np.clip(indices, 0, len(color_array) - 1)]
            alpha = np.where(valid, 255, 0).astype(np.uint8)
            image = Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            image.thumbnail((1600, 1600), resampling)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "PNG", optimize=True)
            return str(target)
    except Exception:
        return None


def _parse_expert_disp_point_table(disp_point_path: Path) -> list[dict[str, Any]]:
    if not disp_point_path.is_file():
        return []
    import csv

    rows = list(csv.reader(disp_point_path.read_text(encoding="utf-8", errors="ignore").splitlines()))
    if len(rows) < 2:
        return []
    header = [str(value or "").strip() for value in rows[0]]
    dates = [value for value in header[5:] if value]
    points: list[dict[str, Any]] = []
    for index, row in enumerate(rows[1:], start=1):
        if len(row) < 5:
            continue
        values = [str(value or "").strip() for value in row]
        try:
            img_x = int(float(values[0]))
            img_y = int(float(values[1]))
        except ValueError:
            continue
        displacements: list[dict[str, Any]] = []
        for date_text, value_text in zip(dates, values[5:]):
            try:
                displacement = float(value_text)
            except ValueError:
                continue
            date_clean = date_text.strip()
            if len(date_clean) == 8 and date_clean.isdigit():
                date_iso = f"{date_clean[0:4]}-{date_clean[4:6]}-{date_clean[6:8]}"
            else:
                date_iso = date_clean
            displacements.append({"date": date_iso, "displacement_mm": displacement})
        points.append(
            {
                "point_id": f"expert_point_{index:03d}",
                "img_x": img_x,
                "img_y": img_y,
                "height_m": _safe_float(values[2]) if len(values) > 2 else None,
                "deformation_rate_mm_per_year": _safe_float(values[3]) if len(values) > 3 else None,
                "stdev_residual_phase_rad": _safe_float(values[4]) if len(values) > 4 else None,
                "displacements": displacements,
            }
        )
    return points


def _read_expert_sbas_dates(run_dir: Path) -> list[str]:
    rmli_tab = run_dir / "sbas" / "RMLI_tab"
    dates: list[str] = []
    if rmli_tab.is_file():
        for line in rmli_tab.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if not parts:
                continue
            match = re.search(r"(\d{8})", Path(parts[0]).name)
            if match:
                raw = match.group(1)
                dates.append(f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}")
    if dates:
        return dates
    points = _parse_expert_disp_point_table(run_dir / "publish" / "points" / "disp_point.txt")
    for point in points:
        displacements = point.get("displacements") or []
        if displacements:
            return [str(item.get("date") or "") for item in displacements if item.get("date")]
    return []


def _read_radar_float32(path: Path, *, width: int, img_x: int, img_y: int) -> Optional[float]:
    if width <= 0 or img_x < 0 or img_y < 0 or not path.is_file():
        return None
    offset = (img_y * width + img_x) * 4
    try:
        if offset < 0 or offset + 4 > path.stat().st_size:
            return None
        with path.open("rb") as fp:
            fp.seek(offset)
            payload = fp.read(4)
        if len(payload) != 4:
            return None
        value = struct.unpack(">f", payload)[0]
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _read_geo_rate_window(
    *,
    source_path: Path,
    coverage_path: Path,
    center_row: int,
    center_col: int,
    radius: int,
    width: int,
    height: int,
) -> tuple[Any, Any, int, int]:
    import numpy as np
    import rasterio

    row0 = max(0, center_row - radius)
    row1 = min(height - 1, center_row + radius)
    col0 = max(0, center_col - radius)
    col1 = min(width - 1, center_col + radius)
    if row0 > row1 or col0 > col1:
        return None, None, row0, col0
    window = rasterio.windows.Window(col0, row0, col1 - col0 + 1, row1 - row0 + 1)
    with rasterio.open(source_path) as src:
        rate = src.read(1, window=window)
    coverage = None
    if coverage_path.is_file():
        with rasterio.open(coverage_path) as cov:
            if cov.width == width and cov.height == height and cov.count >= 3:
                rgb = cov.read([1, 2, 3], window=window)
                coverage = np.any(rgb != 0, axis=0)
    return rate, coverage, row0, col0


def _query_expert_gamma_point_timeseries(run_dir: Path, *, lon: float, lat: float) -> dict[str, Any]:
    source_path = run_dir / "publish" / "geotiff" / "geo_los_def_rate.tif"
    coverage_path = run_dir / "publish" / "geotiff" / "geo_los_def_rate_rgb.tif"
    work_run_dir = _expert_gamma_work_run_dir(run_dir)
    work_manifest = _safe_read_json(work_run_dir / "run_manifest.json")
    if not work_manifest:
        work_manifest = _safe_read_json(run_dir / "run_manifest.json")
    lt_path = _expert_gamma_lookup_path(work_run_dir, work_manifest)
    mli_par = work_run_dir / "sbas" / "mli.ave.par"
    disp_tab = work_run_dir / "sbas" / "disp.TS_tab"
    if not source_path.is_file():
        raise FileNotFoundError("geo_los_def_rate.tif is missing")
    if not lt_path.is_file():
        raise FileNotFoundError("Gamma lookup table *.lt_fine is missing")
    if not mli_par.is_file():
        raise FileNotFoundError("mli.ave.par is missing")
    if not disp_tab.is_file():
        raise FileNotFoundError("disp.TS_tab is missing")

    try:
        import numpy as np
        import rasterio
    except Exception as exc:
        raise RuntimeError(f"point_query_dependency_unavailable: {exc}") from exc

    with rasterio.open(source_path) as src:
        width = int(src.width)
        height = int(src.height)
        if lon < src.bounds.left or lon > src.bounds.right or lat < src.bounds.bottom or lat > src.bounds.top:
            raise ValueError("requested WGS84 coordinate is outside product geocoded bounds")
        center_row, center_col = src.index(lon, lat)
        transform = src.transform

    valid_choice: Optional[dict[str, Any]] = None
    for radius in [0, 1, 2, 4, 8, 16, 32, 64, 128, 256]:
        rate, coverage, row0, col0 = _read_geo_rate_window(
            source_path=source_path,
            coverage_path=coverage_path,
            center_row=int(center_row),
            center_col=int(center_col),
            radius=radius,
            width=width,
            height=height,
        )
        if rate is None:
            continue
        valid = np.isfinite(rate) & (rate != 0.0)
        if coverage is not None:
            valid &= coverage
        if not np.any(valid):
            continue
        rows, cols = np.where(valid)
        abs_rows = rows + row0
        abs_cols = cols + col0
        distances = (abs_rows - int(center_row)) ** 2 + (abs_cols - int(center_col)) ** 2
        best_index = int(np.argmin(distances))
        matched_row = int(abs_rows[best_index])
        matched_col = int(abs_cols[best_index])
        matched_lon, matched_lat = rasterio.transform.xy(transform, matched_row, matched_col, offset="center")
        rate_value = float(rate[rows[best_index], cols[best_index]])
        valid_choice = {
            "geo_row": matched_row,
            "geo_col": matched_col,
            "lon": float(matched_lon),
            "lat": float(matched_lat),
            "los_rate_mm_per_year": rate_value * 1000.0,
            "source_native_m_per_year": rate_value,
            "search_radius_pixels": radius,
            "distance_m": _haversine_m(lon, lat, float(matched_lon), float(matched_lat)),
        }
        break

    if valid_choice is None:
        raise ValueError("no valid deformation pixel found near requested WGS84 coordinate")

    lt_offset = (int(valid_choice["geo_row"]) * width + int(valid_choice["geo_col"])) * 8
    if lt_offset < 0 or lt_offset + 8 > lt_path.stat().st_size:
        raise ValueError("matched geocoded pixel is outside lookup table")
    with lt_path.open("rb") as fp:
        fp.seek(lt_offset)
        range_value, azimuth_value = struct.unpack(">ff", fp.read(8))
    if not math.isfinite(range_value) or not math.isfinite(azimuth_value):
        raise ValueError("lookup table returned invalid radar coordinates")

    radar_width = _read_gamma_int_param(mli_par, "range_samples") or _read_gamma_int_param(mli_par, "width") or 0
    radar_lines = _read_gamma_int_param(mli_par, "azimuth_lines") or _read_gamma_int_param(mli_par, "nlines") or 0
    img_x = int(round(range_value))
    img_y = int(round(azimuth_value))
    if radar_width <= 0 or radar_lines <= 0:
        raise ValueError("invalid radar image dimensions")
    img_x = min(max(0, img_x), radar_width - 1)
    img_y = min(max(0, img_y), radar_lines - 1)

    raw_disp_paths = [
        _resolve_expert_gamma_tab_path(line.strip(), work_run_dir=work_run_dir)
        for line in disp_tab.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip()
    ]
    dates = _read_expert_sbas_dates(work_run_dir)
    if not dates:
        dates = _read_expert_sbas_dates(run_dir)
    displacements: list[dict[str, Any]] = []
    for index, disp_path in enumerate(raw_disp_paths):
        value_m = _read_radar_float32(disp_path, width=radar_width, img_x=img_x, img_y=img_y)
        date = dates[index] if index < len(dates) else f"epoch_{index + 1:03d}"
        if value_m is None:
            displacements.append({"date": date, "displacement_mm": None})
        else:
            displacements.append({"date": date, "displacement_mm": value_m * 1000.0})

    if not any(item.get("displacement_mm") is not None for item in displacements):
        raise ValueError("matched radar pixel has no readable displacement values")

    return {
        "schema": "insar.gamma-sbas-point-query/v1",
        "source_tool": "disp.TS_tab_radar_pixel_sample",
        "source_run_dir": str(run_dir),
        "work_run_dir": str(work_run_dir),
        "query": {"lon": lon, "lat": lat},
        "matched": {
            **valid_choice,
            "used_nearest": int(valid_choice["geo_row"]) != int(center_row)
            or int(valid_choice["geo_col"]) != int(center_col),
            "input_geo_row": int(center_row),
            "input_geo_col": int(center_col),
            "radar_range": float(range_value),
            "radar_azimuth": float(azimuth_value),
            "img_x": img_x,
            "img_y": img_y,
            "radar_width": radar_width,
            "radar_lines": radar_lines,
        },
        "unit": "mm",
        "rate_unit": "mm/yr",
        "displacement_count": len(displacements),
        "displacements": displacements,
    }


def _locate_radar_points_in_geocoded_product(run_dir: Path, points: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    targets: list[tuple[str, float, float]] = []
    for point in points:
        point_id = str(point.get("point_id") or "").strip()
        img_x = _safe_float(point.get("img_x"))
        img_y = _safe_float(point.get("img_y"))
        if point_id and img_x is not None and img_y is not None:
            targets.append((point_id, img_x, img_y))
    if not targets:
        return {}

    source_path = run_dir / "publish" / "geotiff" / "geo_los_def_rate.tif"
    coverage_path = run_dir / "publish" / "geotiff" / "geo_los_def_rate_rgb.tif"
    work_run_dir = _expert_gamma_work_run_dir(run_dir)
    manifest = _safe_read_json(work_run_dir / "run_manifest.json")
    if not manifest:
        manifest = _safe_read_json(run_dir / "run_manifest.json")
    lt_path = _expert_gamma_lookup_path(work_run_dir, manifest)
    if not source_path.is_file() or not lt_path.is_file():
        return {}

    try:
        import numpy as np
        import rasterio
        from rasterio.windows import Window
    except Exception:
        return {}

    try:
        with rasterio.open(source_path) as src:
            width = int(src.width)
            height = int(src.height)
            transform = src.transform
            expected_size = width * height * 2 * 4
            if lt_path.stat().st_size < expected_size:
                return {}

            lookup = np.memmap(lt_path, dtype=">f4", mode="r", shape=(height, width, 2))
            coarse_step = 16
            coarse_best: dict[str, dict[str, Any]] = {
                point_id: {"distance2": math.inf, "row": None, "col": None}
                for point_id, _, _ in targets
            }
            for row0 in range(0, height, coarse_step * 128):
                row1 = min(height, row0 + coarse_step * 128)
                sampled = lookup[row0:row1:coarse_step, 0:width:coarse_step, :]
                if sampled.size == 0:
                    continue
                range_chunk = np.asarray(sampled[:, :, 0], dtype=np.float32)
                azimuth_chunk = np.asarray(sampled[:, :, 1], dtype=np.float32)
                valid = np.isfinite(range_chunk) & np.isfinite(azimuth_chunk) & (range_chunk >= 0.0) & (azimuth_chunk >= 0.0)
                if not np.any(valid):
                    continue
                for point_id, img_x, img_y in targets:
                    distances = (range_chunk - np.float32(img_x)) ** 2 + (azimuth_chunk - np.float32(img_y)) ** 2
                    distances = np.where(valid, distances, np.float32(np.inf))
                    flat_index = int(np.argmin(distances))
                    value = float(distances.flat[flat_index])
                    if value >= coarse_best[point_id]["distance2"]:
                        continue
                    local_row, local_col = np.unravel_index(flat_index, distances.shape)
                    coarse_best[point_id] = {
                        "distance2": value,
                        "row": int(row0 + local_row * coarse_step),
                        "col": int(local_col * coarse_step),
                    }

            results: dict[str, dict[str, Any]] = {}
            coverage_src = None
            if coverage_path.is_file():
                try:
                    candidate = rasterio.open(coverage_path)
                    if candidate.width == width and candidate.height == height and candidate.count >= 3:
                        coverage_src = candidate
                    else:
                        candidate.close()
                except Exception:
                    coverage_src = None

            try:
                refine_radius = coarse_step * 12
                for point_id, img_x, img_y in targets:
                    seed = coarse_best.get(point_id) or {}
                    seed_row = seed.get("row")
                    seed_col = seed.get("col")
                    if seed_row is None or seed_col is None:
                        continue
                    row0 = max(0, int(seed_row) - refine_radius)
                    row1 = min(height, int(seed_row) + refine_radius + 1)
                    col0 = max(0, int(seed_col) - refine_radius)
                    col1 = min(width, int(seed_col) + refine_radius + 1)
                    local = lookup[row0:row1, col0:col1, :]
                    range_local = np.asarray(local[:, :, 0], dtype=np.float32)
                    azimuth_local = np.asarray(local[:, :, 1], dtype=np.float32)
                    valid = np.isfinite(range_local) & np.isfinite(azimuth_local) & (range_local >= 0.0) & (azimuth_local >= 0.0)
                    if not np.any(valid):
                        continue
                    distances = (range_local - np.float32(img_x)) ** 2 + (azimuth_local - np.float32(img_y)) ** 2
                    distances = np.where(valid, distances, np.float32(np.inf))
                    flat_index = int(np.argmin(distances))
                    value = float(distances.flat[flat_index])
                    if not math.isfinite(value):
                        continue
                    local_row, local_col = np.unravel_index(flat_index, distances.shape)
                    row = int(row0 + local_row)
                    col = int(col0 + local_col)
                    lon, lat = rasterio.transform.xy(transform, row, col, offset="center")
                    rate_mm_per_year = None
                    coverage_valid = None
                    try:
                        rate_window = src.read(1, window=Window(col, row, 1, 1))
                        rate_value = float(rate_window[0, 0])
                        if math.isfinite(rate_value):
                            rate_mm_per_year = rate_value * 1000.0
                    except Exception:
                        rate_mm_per_year = None
                    if coverage_src is not None:
                        try:
                            rgb = coverage_src.read([1, 2, 3], window=Window(col, row, 1, 1))
                            coverage_valid = bool(np.any(rgb != 0))
                        except Exception:
                            coverage_valid = None
                    results[point_id] = {
                        "lon": float(lon),
                        "lat": float(lat),
                        "geo_row": row,
                        "geo_col": col,
                        "geo_match_distance_px": math.sqrt(value),
                        "geo_los_rate_mm_per_year": rate_mm_per_year,
                        "geo_coverage_valid": coverage_valid,
                        "geo_source": "gamma_lt_fine_nearest_inverse",
                    }
            finally:
                if coverage_src is not None:
                    coverage_src.close()
                del lookup
            return results
    except Exception:
        return {}


def _file_summary(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }


def _build_expert_gamma_primary_geotiff_stats(path: Path) -> dict[str, Any]:
    stats = {
        **_file_summary(path),
        "schema": "insar.gamma-sbas-expert-primary-geotiff-stats/v1",
        "source": "expert_gamma_geo_los_def_rate",
        "unit": "mm/yr",
        "native_unit": "m/yr",
        "scale_to_unit": 1000.0,
        "zero_is_valid": False,
        "validity_rule": "expert_rgb_coverage_finite_nonzero_values",
    }
    if not path.is_file():
        return stats

    try:
        import numpy as np
        import rasterio
        from rasterio.windows import Window
    except Exception as exc:
        stats["error"] = f"raster_stats_dependency_unavailable: {exc}"
        return stats

    try:
        coverage_path = path.with_name("geo_los_def_rate_rgb.tif")
        coverage_mask_source = str(coverage_path) if coverage_path.is_file() else None
        valid_count = 0
        zero_count = 0
        nonzero_count = 0
        sample_count = 0
        value_sum = 0.0
        value_sumsq = 0.0
        value_min: Optional[float] = None
        value_max: Optional[float] = None
        percentile_chunks: list[Any] = []
        max_percentile_samples = 5_000_000
        tile_size = 1024

        with rasterio.open(path) as src:
            coverage_src = None
            if coverage_path.is_file():
                try:
                    candidate = rasterio.open(coverage_path)
                    if candidate.width == src.width and candidate.height == src.height and candidate.count >= 3:
                        coverage_src = candidate
                    else:
                        candidate.close()
                except Exception:
                    coverage_src = None
            total_count = int(src.width * src.height)
            stats.update(
                {
                    "width": int(src.width),
                    "height": int(src.height),
                    "band_count": int(src.count),
                    "dtype": str(src.dtypes[0]) if src.dtypes else None,
                    "crs": str(src.crs) if src.crs else None,
                    "nodata": _safe_float(src.nodata),
                    "metadata_nodata_applied": _safe_float(src.nodata) == 0.0,
                    "coverage_mask_source": coverage_mask_source if coverage_src is not None else None,
                    "bounds": {
                        "left": _safe_float(src.bounds.left),
                        "bottom": _safe_float(src.bounds.bottom),
                        "right": _safe_float(src.bounds.right),
                        "top": _safe_float(src.bounds.top),
                    },
                    "total_count": total_count,
                }
            )
            for row_off in range(0, src.height, tile_size):
                for col_off in range(0, src.width, tile_size):
                    window = Window(
                        col_off=col_off,
                        row_off=row_off,
                        width=min(tile_size, src.width - col_off),
                        height=min(tile_size, src.height - row_off),
                    )
                    data = src.read(1, window=window, masked=False)
                    values = np.asarray(data, dtype=np.float64).reshape(-1)
                    if values.size == 0:
                        continue
                    finite = np.isfinite(values)
                    if coverage_src is not None:
                        coverage_rgb = coverage_src.read([1, 2, 3], window=window, masked=False)
                        coverage = np.any(coverage_rgb != 0, axis=0).reshape(-1)
                    else:
                        coverage = np.ones(values.shape, dtype=bool)
                    covered_values = values[finite & coverage]
                    zero_count += int(np.count_nonzero(covered_values == 0.0))
                    values = values[finite & coverage & (values != 0.0)]
                    if values.size == 0:
                        continue
                    scaled = values * 1000.0
                    valid_count += int(scaled.size)
                    nonzero_count += int(np.count_nonzero(values != 0.0))
                    value_sum += float(scaled.sum(dtype=np.float64))
                    value_sumsq += float(np.square(scaled, dtype=np.float64).sum(dtype=np.float64))
                    chunk_min = float(scaled.min())
                    chunk_max = float(scaled.max())
                    value_min = chunk_min if value_min is None else min(value_min, chunk_min)
                    value_max = chunk_max if value_max is None else max(value_max, chunk_max)
                    if sample_count < max_percentile_samples:
                        remaining = max_percentile_samples - sample_count
                        if scaled.size <= remaining:
                            sample = scaled
                        else:
                            step = max(1, int(np.ceil(scaled.size / remaining)))
                            sample = scaled[::step][:remaining]
                        percentile_chunks.append(sample.astype(np.float64, copy=True))
                        sample_count += int(sample.size)

            if coverage_src is not None:
                coverage_src.close()

        stats["valid_count"] = valid_count
        stats["zero_count"] = zero_count
        stats["nonzero_count"] = nonzero_count
        stats["valid_ratio"] = (valid_count / stats["total_count"]) if stats.get("total_count") else None
        if valid_count <= 0:
            return stats

        mean = value_sum / valid_count
        variance = max((value_sumsq / valid_count) - (mean * mean), 0.0)
        stats.update(
            {
                "min": value_min,
                "max": value_max,
                "mean": mean,
                "stddev": float(variance ** 0.5),
                "sample_count": sample_count,
                "percentiles_sampled": sample_count < valid_count,
            }
        )

        if percentile_chunks:
            percentile_values = np.concatenate(percentile_chunks)
            p01, p05, median, p95, p99 = np.percentile(percentile_values, [1, 5, 50, 95, 99])
            stats.update(
                {
                    "p01": float(p01),
                    "p05": float(p05),
                    "median": float(median),
                    "p95": float(p95),
                    "p99": float(p99),
                }
            )
        return stats
    except Exception as exc:
        stats["error"] = f"raster_stats_failed: {exc}"
        return stats


def _build_expert_gamma_quality_summary(run_dir: Path) -> dict[str, Any]:
    primary_stats = _build_expert_gamma_primary_geotiff_stats(
        run_dir / "publish" / "geotiff" / "geo_los_def_rate.tif"
    )
    return {
        "schema": "insar.gamma-sbas-derived-quality-summary/v1",
        "source": "derived_from_expert_gamma_outputs",
        "note": "Catalog/UI inspection stats derived from expert geo_los_def_rate.tif; quality_summary.json is not required for the expert workflow.",
        "primary_geotiff": primary_stats,
    }


def _pixel_center(transform: tuple[float, float, float, float, float, float], row: int, col: int) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    x = c + (col + 0.5) * a + (row + 0.5) * b
    y = f + (col + 0.5) * d + (row + 0.5) * e
    return float(x), float(y)


def _normalize_crs_label(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    upper = text.upper()
    if "EPSG" in upper and "4326" in upper:
        return "EPSG:4326"
    if "WGS 84" in upper or "WGS_1984" in upper:
        return "EPSG:4326"
    return text[:240]


def _build_expert_gamma_point_vector(run_dir: Path, *, summary_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    source_path = run_dir / "publish" / "geotiff" / "geo_los_def_rate.tif"
    coverage_path = run_dir / "publish" / "geotiff" / "geo_los_def_rate_rgb.tif"
    output_path = run_dir / "publish" / "vectors" / "los_rate_points.geojson.gz"
    summary_path = run_dir / "publish" / "vectors" / "los_rate_points_summary.json"
    previous_summary = _safe_read_json(summary_path)
    context = summary_context or {}
    stack_dates = context.get("stack_dates") or []
    admin_region = context.get("admin_region") if isinstance(context.get("admin_region"), dict) else {}
    admin_names = admin_region.get("names") if isinstance(admin_region.get("names"), dict) else {}
    fields = [
        "run_id",
        "row",
        "col",
        "lon",
        "lat",
        "los_rate_mm_per_year",
        "source_native_m_per_year",
        "date_start",
        "date_end",
        "reference_date",
        "admin_province",
        "admin_city",
    ]
    summary: dict[str, Any] = {
        "schema": "insar.gamma-sbas-expert-point-vector-summary/v1",
        "generated_at": previous_summary.get("generated_at") or _utcnow().isoformat(timespec="seconds") + "Z",
        "ready": False,
        "feature_count": 0,
        "output_geojson_gz": str(output_path),
        "output_size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
        "fields": fields,
        "source_geotiffs": {
            "geo_los_def_rate": str(source_path),
            "coverage_mask": str(coverage_path) if coverage_path.is_file() else None,
        },
        "unit": "mm/yr",
        "native_unit": "m/yr",
        "scale_to_unit": 1000.0,
        "zero_is_valid": False,
        "validity_rule": "expert_rgb_coverage_finite_nonzero_values",
        "date_start": context.get("date_start") or (stack_dates[0] if stack_dates else None),
        "date_end": context.get("date_end") or (stack_dates[-1] if stack_dates else None),
        "reference_date": context.get("reference_date"),
        "admin_region": {
            "province": admin_names.get("province") or admin_region.get("province"),
            "city": admin_names.get("city") or admin_region.get("city"),
        },
        "los_convention": context.get("los_sign_convention") or "Gamma expert geo_los_def_rate output; sign follows the expert workflow.",
        "frontend_policy": "download_only; do not render full point GeoJSON in browser",
    }
    if not source_path.is_file():
        _write_json_if_changed(summary_path, {**summary, "error": "source_geotiff_missing"})
        return summary
    if output_path.is_file() and summary_path.is_file():
        input_paths = [source_path]
        if coverage_path.is_file():
            input_paths.append(coverage_path)
        output_mtime = output_path.stat().st_mtime
        if (
            previous_summary.get("validity_rule") == "expert_rgb_coverage_finite_nonzero_values"
            and previous_summary.get("zero_is_valid") is False
            and all(output_mtime >= item.stat().st_mtime for item in input_paths)
        ):
            return previous_summary

    try:
        import numpy as np
        import rasterio
        from rasterio.windows import Window
    except Exception as exc:
        summary["error"] = f"point_vector_dependency_unavailable: {exc}"
        _write_json_if_changed(summary_path, summary)
        return summary

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        feature_count = 0
        zero_count = 0
        tile_size = 512
        with rasterio.open(source_path) as src:
            coverage_src = None
            if coverage_path.is_file():
                candidate = rasterio.open(coverage_path)
                if candidate.width == src.width and candidate.height == src.height and candidate.count >= 3:
                    coverage_src = candidate
                else:
                    candidate.close()
            transform = tuple(float(value) for value in src.transform.to_gdal())
            # Convert GDAL geotransform (c, a, b, f, d, e) to affine tuple used by _pixel_center.
            transform = (transform[1], transform[2], transform[0], transform[4], transform[5], transform[3])
            nodata = _safe_float(src.nodata)
            summary.update(
                {
                    "width": int(src.width),
                    "height": int(src.height),
                    "crs": _normalize_crs_label(str(src.crs) if src.crs else None),
                    "nodata": nodata,
                    "metadata_nodata_applied": nodata == 0.0,
                    "coverage_mask_source": str(coverage_path) if coverage_src is not None else None,
                    "total_count": int(src.width * src.height),
                }
            )
            with gzip.open(output_path, "wt", encoding="utf-8", compresslevel=6) as handle:
                handle.write('{"type":"FeatureCollection","features":[\n')
                first = True
                for row_off in range(0, src.height, tile_size):
                    for col_off in range(0, src.width, tile_size):
                        window = Window(
                            col_off=col_off,
                            row_off=row_off,
                            width=min(tile_size, src.width - col_off),
                            height=min(tile_size, src.height - row_off),
                        )
                        data = src.read(1, window=window, masked=False).astype("float64", copy=False)
                        finite = np.isfinite(data)
                        coverage = np.ones(data.shape, dtype=bool)
                        if coverage_src is not None:
                            rgb = coverage_src.read([1, 2, 3], window=window, masked=False)
                            coverage = np.any(rgb != 0, axis=0)
                        covered = finite & coverage
                        zero_count += int(np.count_nonzero(data[covered] == 0.0))
                        valid = covered & (data != 0.0)
                        rows, cols = np.where(valid)
                        for local_row, local_col in zip(rows.tolist(), cols.tolist()):
                            row = int(row_off + local_row)
                            col = int(col_off + local_col)
                            lon, lat = _pixel_center(transform, row, col)
                            native_value = float(data[local_row, local_col])
                            properties = {
                                "run_id": context.get("run_id") or run_dir.name,
                                "row": row,
                                "col": col,
                                "lon": lon,
                                "lat": lat,
                                "los_rate_mm_per_year": native_value * 1000.0,
                                "source_native_m_per_year": native_value,
                                "date_start": summary.get("date_start"),
                                "date_end": summary.get("date_end"),
                                "reference_date": summary.get("reference_date"),
                                "admin_province": summary["admin_region"].get("province"),
                                "admin_city": summary["admin_region"].get("city"),
                            }
                            feature = {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                                "properties": properties,
                            }
                            if not first:
                                handle.write(",\n")
                            handle.write(json.dumps(feature, ensure_ascii=False, separators=(",", ":")))
                            first = False
                            feature_count += 1
                handle.write("\n]}\n")
            if coverage_src is not None:
                coverage_src.close()
        summary.update(
            {
                "ready": output_path.is_file() and output_path.stat().st_size > 0,
                "feature_count": feature_count,
                "zero_count": zero_count,
                "output_size_bytes": output_path.stat().st_size if output_path.is_file() else 0,
            }
        )
        _write_json_if_changed(summary_path, summary)
        return summary
    except Exception as exc:
        summary["error"] = f"point_vector_export_failed: {exc}"
        _write_json_if_changed(summary_path, summary)
        return summary


def _write_text_if_changed(path: Path, text: str) -> bool:
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8", errors="ignore") == text:
                return False
        except Exception:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def _write_json_if_changed(path: Path, payload: dict[str, Any]) -> bool:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return _write_text_if_changed(path, text)


_MONITOR_POINT_SELECTION_DEFINITIONS = [
    (
        "toward_high_rate_low_sigma",
        "趋近雷达高形变低残差点",
        "rate > 0，且绝对速率位于高分位，残差低，用于检查明显正向形变区域。",
    ),
    (
        "away_high_rate_low_sigma",
        "远离雷达高形变低残差点",
        "rate < 0，且绝对速率位于高分位，残差低，用于检查明显负向形变区域。",
    ),
    (
        "high_abs_rate_low_sigma",
        "高绝对速率低残差点",
        "不区分正负，优先选择绝对速率高且残差低的有效点。",
    ),
    (
        "stable_low_sigma",
        "近零低残差代表点",
        "绝对速率位于低分位且残差低，用于对照相对稳定区域。",
    ),
    (
        "center_valid",
        "覆盖区中心有效点",
        "从有效像元中选取最接近雷达网格中心的点，用于空间位置对照。",
    ),
]


def _read_expert_monitor_point_selection(points_dir: Path) -> dict[tuple[int, int], dict[str, Any]]:
    selection_path = points_dir / "disp_point_sel.txt"
    rows: list[dict[str, Any]] = []
    if selection_path.is_file():
        for index, line in enumerate(selection_path.read_text(encoding="utf-8", errors="ignore").splitlines()):
            parts = line.strip().split()
            if len(parts) < 2:
                continue
            try:
                img_x = int(float(parts[0]))
                img_y = int(float(parts[1]))
            except ValueError:
                continue
            key, label, description = (
                _MONITOR_POINT_SELECTION_DEFINITIONS[index]
                if index < len(_MONITOR_POINT_SELECTION_DEFINITIONS)
                else (
                    f"extra_representative_{index + 1:03d}",
                    f"补充代表点 {index + 1}",
                    "自动选点数量超过内置策略说明时的补充代表点。",
                )
            )
            rows.append(
                {
                    "selection_rank": index + 1,
                    "selection_key": key,
                    "selection_label": label,
                    "selection_description": description,
                    "img_x": img_x,
                    "img_y": img_y,
                }
            )
    payload = {
        "schema": "insar.gamma-sbas-expert-monitor-point-selection/v1",
        "generated_at": _utcnow().isoformat(timespec="seconds") + "Z",
        "source": "disp_point_sel.txt",
        "selection_count": len(rows),
        "strategy": "auto_representative_points",
        "strategy_note": "自动选取趋近/远离雷达高形变、绝对高形变、近零稳定和中心有效点；时序仍由 Gamma disp_prt_2d 输出。",
        "points": rows,
    }
    if rows:
        _write_json_if_changed(points_dir / "disp_point_selection.json", payload)
    return {(int(item["img_x"]), int(item["img_y"])): item for item in rows}


def _wsl_path_to_windows(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith("/mnt/") and len(text) > 6 and text[6] == "/":
        drive = text[5].upper()
        return f"{drive}:{text[6:]}".replace("/", "\\")
    return text


def _build_expert_unwrapped_phase_radar_browse(
    run_dir: Path,
    source_paths: list[Path],
    *,
    width: int,
    lines: int,
) -> dict[str, dict[str, Any]]:
    unwrapped_dir = run_dir / "publish" / "geotiff" / "unwrapped"
    browse_by_pair: dict[str, dict[str, Any]] = {}
    if not source_paths or width <= 0 or lines <= 0:
        return browse_by_pair
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return browse_by_pair

    colormap_path = _find_gamma_colormap("rmg.cm")
    colors = _read_gamma_colormap(colormap_path) if colormap_path else []
    if not colors:
        return browse_by_pair
    color_array = np.asarray(colors, dtype=np.uint8)
    unwrapped_dir.mkdir(parents=True, exist_ok=True)
    _build_gamma_colormap_colorbar(
        unwrapped_dir / "unwrapped_phase_rmg_colorbar.png",
        colormap_name="rmg.cm",
        min_value=-6.28,
        max_value=6.28,
        unit="rad",
        title="Gamma unwrapped phase browse",
    )
    for source in source_paths:
        pair_id = source.name.replace(".unw.atmsub_1", "")
        output_bmp = unwrapped_dir / f"{source.name}.rdc_rmg.bmp"
        output_png = unwrapped_dir / f"{source.name}.rdc_rmg_preview.png"
        item = {
            "pair_id": pair_id,
            "radar_coordinates": True,
            "browse_command": "rasdt_pwr <unw.atmsub_1> mli.ave <width> 1 - 1 1 -6.28 6.28 1 rmg.cm ... 1.0 0.35 24",
            "colormap": "Gamma rmg.cm",
            "display_range_rad": [-6.28, 6.28],
            "bmp": _file_summary(output_bmp),
            "preview": _file_summary(output_png),
            "ready": False,
        }
        if not source.is_file():
            item["error"] = "source_missing"
            browse_by_pair[pair_id] = item
            continue
        if (
            output_png.is_file()
            and output_png.stat().st_mtime >= source.stat().st_mtime
            and (not output_bmp.is_file() or output_bmp.stat().st_mtime >= source.stat().st_mtime)
        ):
            item["bmp"] = _file_summary(output_bmp)
            item["preview"] = _file_summary(output_png)
            item["ready"] = output_png.stat().st_size > 0
            browse_by_pair[pair_id] = item
            continue
        try:
            data = np.fromfile(source, dtype=">f4", count=int(width) * int(lines)).reshape((int(lines), int(width)))
            valid = np.isfinite(data) & (data != 0.0)
            wrapped = ((data + 6.28) % 12.56) - 6.28
            ratio = np.clip((wrapped + 6.28) / 12.56, 0.0, 1.0)
            indices = np.rint(ratio * (len(color_array) - 1)).astype(np.int32)
            rgb = color_array[np.clip(indices, 0, len(color_array) - 1)]
            rgb = np.where(valid[..., None], rgb, 0).astype(np.uint8)
            image = Image.fromarray(rgb, mode="RGB")
            image.save(output_bmp, "BMP")
            preview = image.copy()
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            preview.thumbnail((1600, 1600), resampling)
            preview.save(output_png, "PNG", optimize=True)
            item.update(
                {
                    "bmp": _file_summary(output_bmp),
                    "preview": _file_summary(output_png),
                    "valid_count": int(np.count_nonzero(valid)),
                    "ready": output_png.is_file() and output_png.stat().st_size > 0,
                }
            )
        except Exception as exc:
            item["error"] = str(exc)
        browse_by_pair[pair_id] = item
    return browse_by_pair


def _build_expert_unwrapped_phase_derivatives(run_dir: Path) -> dict[str, Any]:
    unwrapped_dir = run_dir / "publish" / "geotiff" / "unwrapped"
    summary_path = unwrapped_dir / "unwrapped_phase_summary.json"
    final_tab = run_dir / "sbas" / "final_unw_tab"
    manifest = _safe_read_json(run_dir / "run_manifest.json")
    stack_manifest = _safe_read_json(run_dir / "stack_manifest.json")
    stack = stack_manifest.get("stack") if isinstance(stack_manifest.get("stack"), dict) else {}
    reference_date = str(
        manifest.get("reference_date")
        or stack.get("reference_date")
        or (manifest.get("coregistration") or {}).get("reference_date")
        or ""
    ).strip()
    dem_par = run_dir / "dem" / f"{reference_date}_seg.dem_par" if reference_date else next(iter(sorted((run_dir / "dem").glob("*_seg.dem_par"))), run_dir / "dem" / "_missing_seg.dem_par")
    lookup = run_dir / "dem" / f"{reference_date}.lt_fine" if reference_date else next(iter(sorted((run_dir / "dem").glob("*.lt_fine"))), run_dir / "dem" / "_missing.lt_fine")
    mli_par = run_dir / "sbas" / "mli.ave.par"
    previous = _safe_read_json(summary_path)
    source_paths: list[Path] = []
    if final_tab.is_file():
        for line in final_tab.read_text(encoding="utf-8", errors="ignore").splitlines():
            text = line.strip().split()
            if text:
                source_paths.append(Path(_wsl_path_to_windows(text[0])))

    summary: dict[str, Any] = {
        "schema": "insar.gamma-sbas-expert-unwrapped-phase-summary/v1",
        "generated_at": previous.get("generated_at") or _utcnow().isoformat(timespec="seconds") + "Z",
        "source_stage": "final_unw_tab",
        "source_tab": _file_summary(final_tab),
        "source_count": len(source_paths),
        "ready": False,
        "products": [],
        "note": "Geocoded GeoTIFF derivatives from the final unwrapped phase files consumed by the expert Gamma SBAS inversion.",
    }
    if not source_paths:
        _write_json_if_changed(summary_path, summary)
        return summary

    outputs = [
        unwrapped_dir / f"{path.name}.geo.tif"
        for path in source_paths
    ]
    radar_outputs = [
        unwrapped_dir / f"{path.name}.rdc_rmg_preview.png"
        for path in source_paths
    ]
    radar_colorbar = unwrapped_dir / "unwrapped_phase_rmg_colorbar.png"
    previous_products = previous.get("products") if isinstance(previous.get("products"), list) else []
    previous_has_radar_browse = (
        len(previous_products) == len(source_paths)
        and all((item.get("radar_browse") or {}).get("ready") for item in previous_products if isinstance(item, dict))
    )
    if (
        summary_path.is_file()
        and all(output.is_file() for output in outputs)
        and all(output.is_file() for output in radar_outputs)
        and radar_colorbar.is_file()
        and previous_has_radar_browse
        and all(
            output.stat().st_mtime >= source.stat().st_mtime
            for source, output in zip(source_paths, outputs)
            if source.is_file()
        )
        and all(
            output.stat().st_mtime >= source.stat().st_mtime
            for source, output in zip(source_paths, radar_outputs)
            if source.is_file()
        )
    ):
        return previous

    try:
        import numpy as np
        import rasterio
        from PIL import Image
        from rasterio.transform import from_origin
    except Exception as exc:
        summary["error"] = f"unwrapped_phase_dependency_unavailable: {exc}"
        _write_json_if_changed(summary_path, summary)
        return summary

    def read_gamma_param(path: Path, key: str) -> Optional[str]:
        if not path.is_file():
            return None
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if parts and parts[0].rstrip(":") == key.rstrip(":") and len(parts) > 1:
                return parts[1]
        return None

    try:
        rdc_width = _safe_int(read_gamma_param(mli_par, "range_samples"))
        rdc_lines = _safe_int(read_gamma_param(mli_par, "azimuth_lines"))
        dem_width = _safe_int(read_gamma_param(dem_par, "width"))
        dem_lines = _safe_int(read_gamma_param(dem_par, "nlines"))
        corner_lon = _safe_float(read_gamma_param(dem_par, "corner_lon"))
        corner_lat = _safe_float(read_gamma_param(dem_par, "corner_lat"))
        post_lon = _safe_float(read_gamma_param(dem_par, "post_lon"))
        post_lat = _safe_float(read_gamma_param(dem_par, "post_lat"))
        if None in (rdc_width, rdc_lines, dem_width, dem_lines, corner_lon, corner_lat, post_lon, post_lat):
            raise RuntimeError("required Gamma geometry parameters are missing")
        if not lookup.is_file():
            raise FileNotFoundError(str(lookup))

        unwrapped_dir.mkdir(parents=True, exist_ok=True)
        radar_browse_by_pair = _build_expert_unwrapped_phase_radar_browse(
            run_dir,
            source_paths,
            width=int(rdc_width),
            lines=int(rdc_lines),
        )
        lut = np.fromfile(lookup, dtype=">c8").reshape((int(dem_lines), int(dem_width)))
        rng = np.rint(lut.real).astype(np.int32)
        az = np.rint(lut.imag).astype(np.int32)
        valid_lut = (
            np.isfinite(lut.real)
            & np.isfinite(lut.imag)
            & (rng >= 0)
            & (rng < int(rdc_width))
            & (az >= 0)
            & (az < int(rdc_lines))
        )
        del lut
        transform = from_origin(float(corner_lon), float(corner_lat), abs(float(post_lon)), abs(float(post_lat)))
        products: list[dict[str, Any]] = []
        preview_limit = 6
        for source in source_paths:
            if not source.is_file():
                products.append({"source": str(source), "ready": False, "error": "source_missing"})
                continue
            pair_id = source.name.replace(".unw.atmsub_1", "")
            output_tif = unwrapped_dir / f"{source.name}.geo.tif"
            output_preview = unwrapped_dir / f"{source.name}.geo_preview.png"
            data = np.fromfile(source, dtype=">f4", count=int(rdc_width) * int(rdc_lines)).reshape((int(rdc_lines), int(rdc_width)))
            geo = np.full((int(dem_lines), int(dem_width)), np.nan, dtype=np.float32)
            geo[valid_lut] = data[az[valid_lut], rng[valid_lut]]
            finite_nonzero = np.isfinite(geo) & (geo != 0.0)
            with rasterio.open(
                output_tif,
                "w",
                driver="GTiff",
                width=int(dem_width),
                height=int(dem_lines),
                count=1,
                dtype="float32",
                crs=_WGS84_GEOGCS_WKT,
                transform=transform,
                nodata=np.nan,
                compress="deflate",
            ) as dst:
                dst.write(geo, 1)
            if np.any(finite_nonzero) and len(products) < preview_limit:
                valid_values = geo[finite_nonzero]
                p02, p98 = np.nanpercentile(valid_values, [2, 98])
                if not np.isfinite(p02) or not np.isfinite(p98) or p98 <= p02:
                    p02 = float(np.nanmin(valid_values))
                    p98 = float(np.nanmax(valid_values))
                norm = np.clip((geo - p02) / max(p98 - p02, 1e-6), 0, 1)
                gray = np.where(finite_nonzero, norm * 255, 0).astype("uint8")
                alpha = np.where(finite_nonzero, 255, 0).astype("uint8")
                rgba = np.dstack([gray, gray, gray, alpha])
                image = Image.fromarray(rgba, mode="RGBA")
                resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
                image.thumbnail((1600, 1600), resampling)
                image.save(output_preview, "PNG", optimize=True)
            products.append(
                {
                    "pair_id": pair_id,
                    "source": str(source),
                    "geotiff": _file_summary(output_tif),
                    "preview": _file_summary(output_preview),
                    "radar_browse": radar_browse_by_pair.get(pair_id) or {},
                    "valid_count": int(np.count_nonzero(finite_nonzero)),
                    "unit": "rad",
                    "description": "Final model-corrected unwrapped phase used by Gamma SBAS inversion, geocoded for inspection.",
                    "ready": output_tif.is_file() and output_tif.stat().st_size > 0,
                }
            )
            del data, geo, finite_nonzero
        summary.update(
            {
                "generated_at": _utcnow().isoformat(timespec="seconds") + "Z",
                "ready": bool(products) and all(item.get("ready") for item in products),
                "products": products,
            }
        )
        _write_json_if_changed(summary_path, summary)
        return summary
    except Exception as exc:
        summary["error"] = f"unwrapped_phase_export_failed: {exc}"
        _write_json_if_changed(summary_path, summary)
        return summary


def _write_expert_monitor_point_derivatives(run_dir: Path) -> dict[str, Any]:
    points_dir = run_dir / "publish" / "points"
    disp_point_path = points_dir / "disp_point.txt"
    items_path = points_dir / "items.txt"
    selection_by_xy = _read_expert_monitor_point_selection(points_dir)
    points = _parse_expert_disp_point_table(disp_point_path)
    geo_locations = _locate_radar_points_in_geocoded_product(run_dir, points)
    out_dir = run_dir / "publish" / "monitor_points"
    summary_path = run_dir / "monitor_points_summary.json"
    monitor_outputs: list[dict[str, Any]] = []
    if points:
        out_dir.mkdir(parents=True, exist_ok=True)
        expected_names = set()
        for index in range(1, len(points) + 1):
            point_id = f"expert_point_{index:03d}"
            expected_names.update(
                {
                    f"{point_id}_timeseries.csv",
                    f"{point_id}_timeseries.png",
                    f"{point_id}_metadata.json",
                }
            )
        for old_path in out_dir.iterdir():
            if old_path.is_file() and old_path.name not in expected_names:
                try:
                    old_path.unlink()
                except Exception:
                    pass

    for point in points:
        point_id = str(point["point_id"])
        csv_path = out_dir / f"{point_id}_timeseries.csv"
        png_path = out_dir / f"{point_id}_timeseries.png"
        metadata_path = out_dir / f"{point_id}_metadata.json"
        displacements = point.get("displacements") or []
        csv_lines = ["date,displacement_mm"]
        csv_lines.extend(f"{item['date']},{item['displacement_mm']:.6f}" for item in displacements)
        curve_needs_refresh = _write_text_if_changed(csv_path, "\n".join(csv_lines) + "\n")
        selection = selection_by_xy.get((int(point.get("img_x") or 0), int(point.get("img_y") or 0)), {})
        geo_location = geo_locations.get(point_id) or {}

        metadata = {
            "schema": "insar.gamma-sbas-expert-monitor-point/v1",
            "point_id": point_id,
            "source_tool": "disp_prt_2d",
            "selection_rank": selection.get("selection_rank"),
            "selection_key": selection.get("selection_key"),
            "selection_label": selection.get("selection_label"),
            "selection_description": selection.get("selection_description"),
            "img_x": point.get("img_x"),
            "img_y": point.get("img_y"),
            "lon": geo_location.get("lon"),
            "lat": geo_location.get("lat"),
            "geo_row": geo_location.get("geo_row"),
            "geo_col": geo_location.get("geo_col"),
            "geo_match_distance_px": geo_location.get("geo_match_distance_px"),
            "geo_los_rate_mm_per_year": geo_location.get("geo_los_rate_mm_per_year"),
            "geo_coverage_valid": geo_location.get("geo_coverage_valid"),
            "geo_source": geo_location.get("geo_source"),
            "height_m": point.get("height_m"),
            "deformation_rate_mm_per_year": point.get("deformation_rate_mm_per_year"),
            "stdev_residual_phase_rad": point.get("stdev_residual_phase_rad"),
            "displacement_count": len(displacements),
            "displacements": displacements,
            "source_files": {
                "items": str(items_path),
                "disp_point": str(disp_point_path),
            },
        }
        _write_json_if_changed(metadata_path, metadata)

        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from matplotlib.dates import DateFormatter

            if curve_needs_refresh or not png_path.is_file():
                x_values = [datetime.fromisoformat(str(item["date"])) for item in displacements]
                x_labels = [str(item["date"]) for item in displacements]
                y_values = [float(item["displacement_mm"]) for item in displacements]
                fig_width = max(8.0, len(x_values) * 1.2)
                fig, ax = plt.subplots(figsize=(fig_width, 4.5), dpi=150)
                ax.plot(x_values, y_values, marker="o", markersize=5, linewidth=1.8, color="#1d4ed8")
                ax.axhline(0, color="#94a3b8", linewidth=0.8)
                ax.set_title(
                    f"Gamma SBAS {point_id}  rate={point.get('deformation_rate_mm_per_year') or 0:.3f} mm/yr",
                    fontsize=10,
                )
                ax.set_xlabel("Date")
                ax.set_ylabel("Displacement (mm)")
                ax.grid(True, alpha=0.25)
                ax.set_xticks(x_values)
                ax.set_xticklabels(x_labels, rotation=35, ha="right")
                ax.xaxis.set_major_formatter(DateFormatter("%Y-%m-%d"))
                fig.tight_layout()
                fig.savefig(png_path)
                plt.close(fig)
        except Exception:
            if not png_path.is_file():
                png_path.write_bytes(b"")

        monitor_outputs.append(
            {
                "point_id": point_id,
                "metadata": metadata,
                "files": {
                    "png": _file_summary(png_path),
                    "csv": _file_summary(csv_path),
                    "metadata": _file_summary(metadata_path),
                },
            }
        )

    summary = {
        "schema": "insar.gamma-sbas-expert-monitor-points-summary/v1",
        "generated_at": _utcnow().isoformat(timespec="seconds") + "Z",
        "mode": "expert_disp_prt_2d",
        "source_tool": "disp_prt_2d",
        "source_files": {
            "items": _file_summary(items_path),
            "disp_point": _file_summary(disp_point_path),
            "selection": _file_summary(points_dir / "disp_point_selection.json"),
        },
        "monitor_points": [
            {
                "point_id": point.get("point_id"),
                "selection_rank": selection_by_xy.get((int(point.get("img_x") or 0), int(point.get("img_y") or 0)), {}).get("selection_rank"),
                "selection_key": selection_by_xy.get((int(point.get("img_x") or 0), int(point.get("img_y") or 0)), {}).get("selection_key"),
                "selection_label": selection_by_xy.get((int(point.get("img_x") or 0), int(point.get("img_y") or 0)), {}).get("selection_label"),
                "selection_description": selection_by_xy.get((int(point.get("img_x") or 0), int(point.get("img_y") or 0)), {}).get("selection_description"),
                "img_x": point.get("img_x"),
                "img_y": point.get("img_y"),
                "lon": (geo_locations.get(str(point.get("point_id"))) or {}).get("lon"),
                "lat": (geo_locations.get(str(point.get("point_id"))) or {}).get("lat"),
                "geo_row": (geo_locations.get(str(point.get("point_id"))) or {}).get("geo_row"),
                "geo_col": (geo_locations.get(str(point.get("point_id"))) or {}).get("geo_col"),
                "geo_match_distance_px": (geo_locations.get(str(point.get("point_id"))) or {}).get("geo_match_distance_px"),
                "geo_los_rate_mm_per_year": (geo_locations.get(str(point.get("point_id"))) or {}).get("geo_los_rate_mm_per_year"),
                "geo_coverage_valid": (geo_locations.get(str(point.get("point_id"))) or {}).get("geo_coverage_valid"),
                "geo_source": (geo_locations.get(str(point.get("point_id"))) or {}).get("geo_source"),
                "height_m": point.get("height_m"),
                "deformation_rate_mm_per_year": point.get("deformation_rate_mm_per_year"),
                "stdev_residual_phase_rad": point.get("stdev_residual_phase_rad"),
                "displacement_count": len(point.get("displacements") or []),
                "displacements": point.get("displacements") or [],
            }
            for point in points
        ],
        "monitor_outputs": monitor_outputs,
        "ready": bool(monitor_outputs)
        and all(
            (item.get("files") or {}).get("png", {}).get("exists")
            and (item.get("files") or {}).get("csv", {}).get("exists")
            and (item.get("files") or {}).get("metadata", {}).get("exists")
            for item in monitor_outputs
        ),
    }
    if points:
        _write_json_if_changed(summary_path, summary)
    return summary


def _bbox_polygon(
    min_lon: Optional[float],
    min_lat: Optional[float],
    max_lon: Optional[float],
    max_lat: Optional[float],
):
    if None in (min_lon, min_lat, max_lon, max_lat):
        return None
    if min_lon == max_lon or min_lat == max_lat:
        return None
    return Polygon(
        [
            (min_lon, min_lat),
            (max_lon, min_lat),
            (max_lon, max_lat),
            (min_lon, max_lat),
            (min_lon, min_lat),
        ]
    )


def _stack_dates_from_manifest(stack_manifest: dict[str, Any], manifest: dict[str, Any], stack: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for source in (
        stack_manifest.get("dates"),
        stack.get("dates"),
        manifest.get("dates"),
        [scene.get("date") for scene in stack_manifest.get("scenes") or [] if isinstance(scene, dict)],
        [scene.get("date") for scene in manifest.get("scenes") or [] if isinstance(scene, dict)],
    ):
        if not isinstance(source, list):
            continue
        for item in source:
            text = str(item or "").strip()
            if text:
                values.append(text)
    return sorted(dict.fromkeys(values))


class SbasInsarCatalogService:
    def get_run_root(self) -> str:
        root = Path(settings.GAMMA_SBAS_PRODUCT_ROOT or Path(settings.TIMESERIES_PRODUCT_DIR) / "sbas")
        run_root = root / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        return _normalize_path(run_root)

    def get_work_run_root(self) -> str:
        root = Path(settings.GAMMA_SBAS_WORK_ROOT or Path(settings.BACKEND_DIR) / "runtime" / "sbas_insar_production")
        run_root = root / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        return _normalize_path(run_root)

    def get_run_roots(self) -> list[str]:
        roots = [self.get_run_root()]
        work_root = self.get_work_run_root()
        if work_root not in roots:
            roots.append(work_root)
        try:
            landsar_root = landsar_sbas_service.configured_run_root()
            if landsar_root not in roots:
                roots.append(landsar_root)
        except Exception:
            pass
        return roots

    def _iter_run_manifest_paths(self, run_root: Optional[str] = None) -> list[str]:
        roots = [run_root] if run_root else self.get_run_roots()
        manifest_paths_by_run: dict[str, str] = {}
        for raw_root in roots:
            root = Path(raw_root)
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/run_manifest.json")):
                if not self._is_publish_ready(path.parent, _safe_read_json(path)):
                    continue
                run_id = path.parent.name
                manifest_paths_by_run.setdefault(run_id, _normalize_path(path))
        return list(manifest_paths_by_run.values())

    @staticmethod
    def _is_landsar_manifest(manifest: dict[str, Any]) -> bool:
        return str(manifest.get("processor_code") or "").strip().lower() == "landsar_sbas"

    @staticmethod
    def _is_expert_gamma_manifest(manifest: dict[str, Any], run_dir: Optional[Path] = None) -> bool:
        execution_mode = str(manifest.get("execution_mode") or "").strip().lower()
        if execution_mode == "expert_manifest_script_workflow":
            return True
        if run_dir is not None and (run_dir / "publish" / "geotiff" / "geo_los_def_rate.tif").is_file():
            return True
        return False

    def _asset_definitions_for_manifest(self, manifest: dict[str, Any], run_dir: Optional[Path] = None):
        if self._is_landsar_manifest(manifest):
            return _LANDSAR_CORE_ASSETS
        if self._is_expert_gamma_manifest(manifest, run_dir):
            return _EXPERT_GAMMA_CORE_ASSETS
        return _CORE_ASSETS

    def _required_roles_for_manifest(self, manifest: dict[str, Any], run_dir: Optional[Path] = None) -> set[str]:
        if self._is_expert_gamma_manifest(manifest, run_dir):
            return {"primary_geotiff"}
        return _LANDSAR_REQUIRED_ASSET_ROLES if self._is_landsar_manifest(manifest) else _REQUIRED_ASSET_ROLES

    def _is_publish_ready(self, run_dir: Path, manifest: dict[str, Any]) -> bool:
        status = str(manifest.get("status") or "").strip().upper()
        asset_defs = self._asset_definitions_for_manifest(manifest, run_dir)
        required_roles = self._required_roles_for_manifest(manifest, run_dir)
        required_outputs_ready = all(
            (run_dir / relative_path).is_file()
            for role, _name, relative_path, is_required, _is_primary in asset_defs
            if role in required_roles and is_required
        )
        return status in _READY_STATUSES or required_outputs_ready

    def _tree_fingerprint(self, manifest_paths: list[str]) -> str:
        records: list[dict[str, Any]] = []
        for raw_path in manifest_paths:
            manifest_path = Path(raw_path)
            run_dir = manifest_path.parent
            manifest_payload = _safe_read_json(manifest_path)
            tracked_paths = [
                manifest_path,
                run_dir / "monitor_points_summary.json",
            ]
            if not self._is_expert_gamma_manifest(manifest_payload, run_dir):
                tracked_paths.extend(
                    [
                        run_dir / "product_summary.json",
                        run_dir / "quality_summary.json",
                    ]
                )
            else:
                tracked_paths.extend(
                    [
                        run_dir / "diff_dir" / "bprep_file.png",
                        run_dir / "diff_dir" / "mean.cc_mask.bmp",
                        run_dir / "sbas" / "final_unw_tab",
                    ]
                )
                diff_dir = run_dir / "diff_dir"
                if diff_dir.is_dir():
                    tracked_paths.extend(sorted(diff_dir.glob("*.adf.unw.bmp")))
            tracked_paths.extend(
                run_dir / relative_path
                for _role, _name, relative_path, _required, _primary in self._asset_definitions_for_manifest(
                    manifest_payload, run_dir
                )
                if not (run_dir / relative_path).is_dir()
            )
            for path in tracked_paths:
                if not path.exists():
                    continue
                stat = path.stat()
                records.append(
                    {
                        "path": str(path.relative_to(run_dir)).replace("\\", "/"),
                        "run": run_dir.name,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
        encoded = json.dumps(records, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    async def _get_or_create_catalog_state(self, db: AsyncSession, *, storage_root: str) -> ResultCatalogStateORM:
        result = await db.execute(
            select(ResultCatalogStateORM).where(ResultCatalogStateORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = ResultCatalogStateORM(
                catalog_name=SBAS_INSAR_CATALOG_NAME,
                product_family="timeseries",
                storage_root=storage_root,
                status="READY",
                needs_rebuild=False,
            )
            db.add(state)
            await db.flush()
        elif state.storage_root != storage_root:
            state.storage_root = storage_root
        if state.product_family != "timeseries":
            state.product_family = "timeseries"
        return state

    def _asset_row(
        self,
        run_dir: Path,
        *,
        role: str,
        name: str,
        relative_path: str,
        is_required: bool,
        is_primary: bool,
    ) -> ResultAssetORM:
        absolute_path = run_dir / relative_path
        exists = absolute_path.is_file()
        return ResultAssetORM(
            asset_role=role[:32],
            asset_name=name,
            relative_path=relative_path.replace("\\", "/"),
            absolute_path=_normalize_path(absolute_path),
            format=_asset_format(relative_path),
            media_type=_media_type(relative_path),
            is_required=is_required,
            is_primary=is_primary,
            exists_flag=exists,
            file_size=absolute_path.stat().st_size if exists else None,
            srid=4326 if (
                (relative_path.lower().endswith((".tif", ".tiff")) and "/geotiff/" in relative_path)
                or (relative_path.lower().endswith((".tif", ".tiff")) and "/landsar/" in relative_path)
                or relative_path.lower().endswith(".geojson.gz")
            ) else None,
        )

    def _monitor_asset_rows(self, run_dir: Path) -> list[ResultAssetORM]:
        monitor_dir = run_dir / "publish" / "monitor_points"
        if not monitor_dir.is_dir():
            return []
        rows: list[ResultAssetORM] = []
        for path in sorted(monitor_dir.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in {".png", ".csv", ".json"}:
                continue
            role = {
                ".png": "monitor_point_curve",
                ".csv": "monitor_point_csv",
                ".json": "monitor_point_metadata",
            }[suffix]
            relative_path = str(path.relative_to(run_dir)).replace("\\", "/")
            rows.append(
                self._asset_row(
                    run_dir,
                    role=role,
                    name=path.name,
                    relative_path=relative_path,
                    is_required=False,
                    is_primary=False,
                )
            )
        return rows

    def _unwrapped_phase_asset_rows(self, run_dir: Path) -> list[ResultAssetORM]:
        unwrapped_dir = run_dir / "publish" / "geotiff" / "unwrapped"
        if not unwrapped_dir.is_dir():
            return []
        rows: list[ResultAssetORM] = []
        for path in sorted(unwrapped_dir.iterdir()):
            if not path.is_file():
                continue
            lowered = path.name.lower()
            if lowered in {"unwrapped_phase_summary.json", "unwrapped_phase_rmg_colorbar.png"}:
                continue
            if lowered.endswith(".rdc_rmg_preview.png"):
                role = "unwrapped_phase_radar_preview"
                name = f"Radar-coordinate rmg unwrapped phase preview {path.name}"
            elif lowered.endswith(".rdc_rmg.bmp"):
                role = "unwrapped_phase_radar_bmp"
                name = f"Gamma radar-coordinate rmg unwrapped phase BMP {path.name}"
            elif lowered.endswith((".tif", ".tiff")):
                role = "unwrapped_phase_geotiff"
                name = f"Geocoded unwrapped phase {path.name}"
            elif lowered.endswith(".png"):
                role = "unwrapped_phase_preview"
                name = f"Unwrapped phase preview {path.name}"
            else:
                continue
            relative_path = str(path.relative_to(run_dir)).replace("\\", "/")
            rows.append(
                self._asset_row(
                    run_dir,
                    role=role,
                    name=name,
                    relative_path=relative_path,
                    is_required=False,
                    is_primary=False,
                )
            )
        return rows

    def _gamma_intermediate_qc_asset_rows(self, run_dir: Path) -> list[ResultAssetORM]:
        rows: list[ResultAssetORM] = []

        static_assets = (
            (
                "gamma_qc_baseline_plot",
                "Gamma baseline network plot",
                run_dir / "diff_dir" / "bprep_file.png",
            ),
            (
                "gamma_qc_mean_coherence",
                "Gamma mean coherence mask",
                run_dir / "diff_dir" / "mean.cc_mask.bmp",
            ),
        )
        for role, name, path in static_assets:
            if not path.is_file():
                continue
            rows.append(
                self._asset_row(
                    run_dir,
                    role=role,
                    name=f"{name} {path.name}",
                    relative_path=str(path.relative_to(run_dir)).replace("\\", "/"),
                    is_required=False,
                    is_primary=False,
                )
            )

        diff_dir = run_dir / "diff_dir"
        final_tab = run_dir / "sbas" / "final_unw_tab"
        pair_ids: list[str] = []
        if final_tab.is_file():
            try:
                for line in final_tab.read_text(encoding="utf-8", errors="ignore").splitlines():
                    raw_path = line.strip().split()[0] if line.strip() else ""
                    if not raw_path:
                        continue
                    name = Path(_wsl_path_to_windows(raw_path)).name
                    pair_id = name.replace(".unw.atmsub_1", "").replace(".unw", "")
                    if pair_id and pair_id not in pair_ids:
                        pair_ids.append(pair_id)
            except OSError:
                pair_ids = []

        unwrapped_paths: list[Path] = []
        for pair_id in pair_ids:
            path = diff_dir / f"{pair_id}.adf.unw.bmp"
            if path.is_file():
                unwrapped_paths.append(path)
        if not unwrapped_paths and diff_dir.is_dir():
            unwrapped_paths = sorted(diff_dir.glob("*.adf.unw.bmp"))

        if len(unwrapped_paths) > 3:
            last_index = len(unwrapped_paths) - 1
            indexes = sorted({round(index * last_index / 2) for index in range(3)})
            unwrapped_paths = [unwrapped_paths[index] for index in indexes]

        for path in unwrapped_paths:
            rows.append(
                self._asset_row(
                    run_dir,
                    role="gamma_qc_unwrapped_phase",
                    name=f"Gamma representative filtered unwrapped phase {path.name}",
                    relative_path=str(path.relative_to(run_dir)).replace("\\", "/"),
                    is_required=False,
                    is_primary=False,
                )
            )
        return rows

    def _build_product(self, manifest_path: str) -> ResultProductORM:
        manifest_file = Path(manifest_path)
        run_dir = manifest_file.parent
        manifest = _read_json(manifest_file)
        if not self._is_publish_ready(run_dir, manifest):
            raise ValueError(f"run is not publish-ready: {manifest.get('status') or 'UNKNOWN'}")
        if self._is_landsar_manifest(manifest):
            return self._build_landsar_product(run_dir, manifest_file, manifest)

        stack_manifest = _safe_read_json(run_dir / "stack_manifest.json")
        try:
            detail = sbas_insar_production_service.get_run_detail(run_dir.name)
            coverage = detail.get("geographic_coverage") or {}
        except FileNotFoundError:
            coverage = sbas_insar_production_service._build_run_geographic_coverage(run_dir, manifest)
        monitor_summary = _safe_read_json(run_dir / "monitor_points_summary.json")
        workflow_summary = _safe_read_json(run_dir / "workflow_summary.json")
        is_expert_gamma = self._is_expert_gamma_manifest(manifest, run_dir)
        product_summary = {} if is_expert_gamma else _safe_read_json(run_dir / "product_summary.json")
        quality_summary = (
            _build_expert_gamma_quality_summary(run_dir)
            if is_expert_gamma
            else _safe_read_json(run_dir / "quality_summary.json")
        )
        point_vector_summary = _safe_read_json(run_dir / "publish" / "vectors" / "los_rate_points_summary.json")
        asset_definitions = self._asset_definitions_for_manifest(manifest, run_dir)
        if is_expert_gamma:
            _build_rgb_geotiff_preview(
                run_dir / "publish" / "geotiff" / "geo_los_def_rate_rgb.tif",
                run_dir / "publish" / "geotiff" / "geo_los_def_rate_rgb_preview.png",
            )
            _build_gamma_hls_rate_preview(
                run_dir / "publish" / "geotiff" / "geo_los_def_rate.tif",
                run_dir / "publish" / "geotiff" / "geo_los_def_rate_pure_hls_preview.png",
                coverage_source=run_dir / "publish" / "geotiff" / "geo_los_def_rate_rgb.tif",
                min_native=-0.08,
                max_native=0.08,
            )
            _build_gamma_hls_colorbar(
                run_dir / "publish" / "geotiff" / "geo_los_def_rate_hls_colorbar.png",
                min_mm_year=-80.0,
                max_mm_year=80.0,
            )
            point_vector_context = {
                "run_id": str(manifest.get("run_id") or run_dir.name).strip() or run_dir.name,
                "reference_date": str(
                    manifest.get("reference_date")
                    or (stack_manifest.get("stack") or {}).get("reference_date")
                    or ""
                ).strip() or None,
                "stack_dates": _stack_dates_from_manifest(stack_manifest, manifest, stack_manifest.get("stack") or {}),
                "los_sign_convention": "Gamma expert geo_los_def_rate output; sign and unit semantics follow the expert workflow.",
                "admin_region": coverage.get("admin_region"),
            }
            point_vector_context["date_start"] = (
                point_vector_context["stack_dates"][0] if point_vector_context["stack_dates"] else None
            )
            point_vector_context["date_end"] = (
                point_vector_context["stack_dates"][-1] if point_vector_context["stack_dates"] else None
            )
            point_vector_summary = _build_expert_gamma_point_vector(
                run_dir,
                summary_context=point_vector_context,
            )
            unwrapped_phase_summary = _build_expert_unwrapped_phase_derivatives(run_dir)
            monitor_summary = _write_expert_monitor_point_derivatives(run_dir) or monitor_summary
        else:
            unwrapped_phase_summary = {}

        bbox = coverage.get("bbox") or {}
        min_lon = _safe_float(bbox.get("min_lon"))
        min_lat = _safe_float(bbox.get("min_lat"))
        max_lon = _safe_float(bbox.get("max_lon"))
        max_lat = _safe_float(bbox.get("max_lat"))
        poly = _bbox_polygon(min_lon, min_lat, max_lon, max_lat)

        run_id = str(manifest.get("run_id") or run_dir.name).strip() or run_dir.name
        stack = stack_manifest.get("stack") or manifest.get("stack") or {}
        stack_id = str(manifest.get("stack_id") or stack_manifest.get("stack_id") or stack.get("stack_id") or "").strip()
        stack_dates = _stack_dates_from_manifest(stack_manifest, manifest, stack)
        reference_date = str(
            manifest.get("reference_date")
            or stack.get("reference_date")
            or (manifest.get("coregistration") or {}).get("reference_date")
            or ""
        ).strip() or None
        display_name = stack_id or f"Gamma SBAS {run_id}"
        product_id = str(manifest.get("product_id") or "").strip() or f"gamma_sbas_{run_id}"
        if len(product_id) > 64:
            product_id = f"gamma_sbas_{_stable_digest(product_id, run_dir, length=32)}"

        assets: list[ResultAssetORM] = [
            self._asset_row(
                run_dir,
                role=role,
                name=name,
                relative_path=relative_path,
                is_required=is_required,
                is_primary=is_primary,
            )
            for role, name, relative_path, is_required, is_primary in asset_definitions
        ]
        assets.extend(self._monitor_asset_rows(run_dir))
        if is_expert_gamma:
            assets.extend(self._unwrapped_phase_asset_rows(run_dir))
            assets.extend(self._gamma_intermediate_qc_asset_rows(run_dir))
        preview_asset = next((asset for asset in assets if asset.asset_role == "primary_geocoded_preview" and asset.exists_flag), None)
        primary_asset = next((asset for asset in assets if asset.asset_role == "primary_geotiff" and asset.exists_flag), None)
        missing_required = [asset for asset in assets if asset.is_required and not asset.exists_flag]
        default_los_product = product_summary.get("default_los_product")
        los_sign_convention = product_summary.get("los_sign_convention")
        if is_expert_gamma:
            default_los_product = default_los_product or "geo_los_def_rate"
            los_sign_convention = (
                los_sign_convention
                or "Gamma expert geo_los_def_rate output; sign and unit semantics follow the expert workflow."
            )
            color_policy = {
                "schema": "insar.gamma-sbas-color-policy/v1",
                "source": "expert_gamma_command",
                "browse_command": "rasdt_pwr los_def_rate ... -0.08 0.08 0 hls.cm ... 24",
                "colormap": "Gamma hls.cm",
                "data_range_native": [-0.08, 0.08],
                "display_range_mm_per_year": [-80.0, 80.0],
                "note": "The RGB browse GeoTIFF is generated by Gamma with hls.cm. Treat it as the expert browse standard unless the project defines a separate cartographic standard.",
            }
        else:
            default_los_product = default_los_product or "los_rate_toward_m_per_year"
            los_sign_convention = los_sign_convention or "toward radar positive; away from radar negative"
            color_policy = product_summary.get("color_policy")

        produced_at = (
            _parse_datetime(monitor_summary.get("generated_at"))
            or _parse_datetime(product_summary.get("generated_at"))
            or _parse_datetime(workflow_summary.get("generated_at"))
            or _parse_datetime(manifest.get("updated_at"))
            or _parse_datetime(manifest.get("created_at"))
        )
        center = coverage.get("center") or {}
        admin_region = coverage.get("admin_region") or lookup_admin_region_for_point(center.get("lon"), center.get("lat"))
        scene_count = (
            _safe_int(manifest.get("scene_count"))
            or len(stack_manifest.get("scenes") or [])
            or len(stack_dates)
        )

        summary_json = {
            "schema": "insar.gamma-sbas-result-catalog-summary/v1",
            "run_id": run_id,
            "stack_id": stack_id or None,
            "stack": stack,
            "reference_date": reference_date,
            "stack_dates": stack_dates,
            "stack_size": len(stack_dates),
            "date_start": stack_dates[0] if stack_dates else None,
            "date_end": stack_dates[-1] if stack_dates else None,
            "scene_count": scene_count,
            "pair_count": _safe_int(manifest.get("pair_count")),
            "status": manifest.get("status"),
            "next_stage": manifest.get("next_stage"),
            "los_sign_convention": los_sign_convention,
            "default_los_product": default_los_product,
            "color_policy": color_policy,
            "center": center or None,
            "admin_region": admin_region,
            "geographic_coverage": coverage,
            "quality": quality_summary,
            "monitor_points": monitor_summary,
            "point_vector": point_vector_summary,
            "unwrapped_phase": unwrapped_phase_summary,
            "workflow": {
                "status": ((manifest.get("workflow") or {}).get("status")),
                "summary": ((manifest.get("workflow") or {}).get("summary")) or workflow_summary,
            },
            "source_run_dir": str(run_dir),
        }

        product = ResultProductORM(
            product_id=product_id,
            catalog_name=SBAS_INSAR_CATALOG_NAME,
            product_family="timeseries",
            product_type="sbas_insar",
            display_name=display_name,
            task_name="Gamma SBAS-InSAR",
            task_alias=run_id,
            stack_key=stack_id or run_id,
            run_key=run_id,
            profile_code=str(stack.get("relative_orbit") or manifest.get("relative_orbit") or "").strip() or None,
            engine_code="gamma",
            engine_version=str((manifest.get("engine") or {}).get("version") or "").strip() or None,
            package_schema=str(manifest.get("schema") or "").strip() or "insar.gamma-sbas-run/v1",
            package_layout="gamma_sbas_expert_workflow_run",
            processor_code="gamma_ipta_sbas",
            runtime_id=settings.GAMMA_SBAS_RUNTIME_ID,
            status="READY" if not missing_required else "INCOMPLETE",
            health_status="OK" if not missing_required else "WARN",
            publish_dir=_normalize_path(run_dir / "publish"),
            manifest_path=_normalize_path(manifest_file),
            source_primary_path=primary_asset.absolute_path if primary_asset else None,
            native_output_dir=_normalize_path(run_dir),
            preview_path=preview_asset.absolute_path if preview_asset else None,
            primary_asset_path=primary_asset.absolute_path if primary_asset else None,
            summary_json=summary_json,
            tags_json={
                "sensor": stack.get("satellite") or manifest.get("platform"),
                "orbit_direction": stack.get("orbit_direction") or manifest.get("direction"),
                "product": "Gamma SBAS",
                "workflow_mode": "expert_document" if is_expert_gamma else "legacy_gamma",
                "admin_region": (admin_region or {}).get("display_name") if isinstance(admin_region, dict) else None,
            },
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            geom=from_shape(poly, srid=4326) if poly is not None else None,
            coverage_polygon=(coverage.get("geojson") or coverage.get("scene_footprints_geojson")),
            produced_at=produced_at,
            published_at=produced_at,
        )
        for asset in assets:
            product.assets.append(asset)
            if asset.is_required and not asset.exists_flag:
                product.issues.append(
                    ResultIssueORM(
                        asset=asset,
                        issue_code="MISSING_REQUIRED_ASSET",
                        severity="ERROR",
                        status="OPEN",
                        scope="file",
                        message=f"Required SBAS asset is missing: {asset.relative_path}",
                    )
                )
        if not preview_asset:
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_PREVIEW",
                    severity="WARN",
                    status="OPEN",
                    scope="product",
                    message="Primary geocoded preview PNG is missing.",
                )
            )
        if poly is None:
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_COVERAGE",
                    severity="WARN",
                    status="OPEN",
                    scope="product",
                    message="No valid EPSG:4326 geographic coverage bbox was found.",
                )
            )
        return product

    def _build_landsar_product(self, run_dir: Path, manifest_file: Path, manifest: dict[str, Any]) -> ResultProductORM:
        try:
            detail = landsar_sbas_service.get_run_detail(run_dir.name)
        except Exception:
            detail = {}
        coverage = detail.get("geographic_coverage") or manifest.get("geographic_coverage") or {}
        stack_manifest = _safe_read_json(run_dir / "stack_manifest.json")
        product_summary = _safe_read_json(run_dir / "product_summary.json")
        quality_summary = _safe_read_json(run_dir / "quality_summary.json")
        workflow_summary = _safe_read_json(run_dir / "workflow_summary.json")

        bbox = coverage.get("bbox") or {}
        min_lon = _safe_float(bbox.get("min_lon"))
        min_lat = _safe_float(bbox.get("min_lat"))
        max_lon = _safe_float(bbox.get("max_lon"))
        max_lat = _safe_float(bbox.get("max_lat"))
        poly = _bbox_polygon(min_lon, min_lat, max_lon, max_lat)

        run_id = str(manifest.get("run_id") or run_dir.name).strip() or run_dir.name
        stack_id = str(manifest.get("stack_id") or run_id).strip()
        stack_dates = _stack_dates_from_manifest(stack_manifest, manifest, {})
        if not stack_dates:
            stack_dates = [str(item or "").strip() for item in manifest.get("dates") or [] if str(item or "").strip()]
        display_name = str(manifest.get("run_label") or f"LandSAR SBAS {run_id}").strip()
        product_id = str(manifest.get("product_id") or "").strip() or f"landsar_sbas_{run_id}"
        if len(product_id) > 64:
            product_id = f"landsar_sbas_{_stable_digest(product_id, run_dir, length=32)}"

        assets: list[ResultAssetORM] = [
            self._asset_row(
                run_dir,
                role=role,
                name=name,
                relative_path=relative_path,
                is_required=is_required,
                is_primary=is_primary,
            )
            for role, name, relative_path, is_required, is_primary in _LANDSAR_CORE_ASSETS
            if not (run_dir / relative_path).is_dir()
        ]
        native_logs_dir = run_dir / "native_logs"
        if native_logs_dir.is_dir():
            for path in sorted(native_logs_dir.rglob("*")):
                if not path.is_file():
                    continue
                relative_path = str(path.relative_to(run_dir)).replace("\\", "/")
                assets.append(
                    self._asset_row(
                        run_dir,
                        role="native_log" if path.suffix.lower() == ".log" else "native_parameter",
                        name=path.name,
                        relative_path=relative_path,
                        is_required=False,
                        is_primary=False,
                    )
                )
        task_publish_root = run_dir / "publish" / "landsar"
        if task_publish_root.is_dir():
            for path in sorted(task_publish_root.rglob("*")):
                if not path.is_file():
                    continue
                relative_path = str(path.relative_to(run_dir)).replace("\\", "/")
                if relative_path in {item.relative_path for item in assets}:
                    continue
                role = "landsar_task_geotiff" if path.suffix.lower() in {".tif", ".tiff"} else "landsar_task_asset"
                assets.append(
                    self._asset_row(
                        run_dir,
                        role=role,
                        name=path.name,
                        relative_path=relative_path,
                        is_required=False,
                        is_primary=False,
                    )
                )

        preview_asset = next((asset for asset in assets if asset.asset_role == "primary_preview" and asset.exists_flag), None)
        primary_asset = next((asset for asset in assets if asset.asset_role == "primary_geotiff" and asset.exists_flag), None)
        missing_required = [asset for asset in assets if asset.is_required and not asset.exists_flag]

        produced_at = (
            _parse_datetime(workflow_summary.get("ended_at"))
            or _parse_datetime(product_summary.get("generated_at"))
            or _parse_datetime(manifest.get("ended_at"))
            or _parse_datetime(manifest.get("created_at"))
        )
        center = coverage.get("center") or {}
        admin_region = coverage.get("admin_region") or lookup_admin_region_for_point(center.get("lon"), center.get("lat"))
        scene_count = _safe_int(manifest.get("scene_count")) or len(stack_dates)
        task_count = _safe_int(manifest.get("task_count"))

        summary_json = {
            "schema": "insar.landsar-sbas-result-catalog-summary/v1",
            "run_id": run_id,
            "stack_id": stack_id or None,
            "reference_date": stack_dates[0] if stack_dates else None,
            "stack_dates": stack_dates,
            "stack_size": len(stack_dates),
            "date_start": manifest.get("date_start") or (stack_dates[0] if stack_dates else None),
            "date_end": manifest.get("date_end") or (stack_dates[-1] if stack_dates else None),
            "scene_count": scene_count,
            "task_count": task_count,
            "pair_count": _safe_int(manifest.get("pair_count")),
            "status": manifest.get("status"),
            "next_stage": manifest.get("next_stage"),
            "los_sign_convention": product_summary.get("los_sign_convention") or "LandSAR LOS output; semantics pending algorithm confirmation.",
            "default_los_product": product_summary.get("default_los_product") or "los_timeseries",
            "center": center or None,
            "admin_region": admin_region,
            "geographic_coverage": coverage,
            "quality": quality_summary,
            "workflow": workflow_summary,
            "source_run_dir": str(run_dir),
            "output_semantics_note": product_summary.get("output_semantics_note"),
        }

        product = ResultProductORM(
            product_id=product_id,
            catalog_name=SBAS_INSAR_CATALOG_NAME,
            product_family="timeseries",
            product_type="sbas_insar",
            display_name=display_name,
            task_name="LandSAR SBAS-InSAR",
            task_alias=run_id,
            stack_key=stack_id or run_id,
            run_key=run_id,
            profile_code="lt1_landsar_sbas",
            engine_code="landsar",
            engine_version=None,
            package_schema=str(manifest.get("schema") or "").strip() or "insar.landsar-sbas-run/v1",
            package_layout="landsar_sbas_console_run",
            processor_code="landsar_sbas",
            runtime_id="landsar_console",
            status="READY" if not missing_required else "INCOMPLETE",
            health_status="OK" if not missing_required else "WARN",
            publish_dir=_normalize_path(run_dir / "publish"),
            manifest_path=_normalize_path(manifest_file),
            source_primary_path=primary_asset.absolute_path if primary_asset else None,
            native_output_dir=_normalize_path(run_dir),
            preview_path=preview_asset.absolute_path if preview_asset else None,
            primary_asset_path=primary_asset.absolute_path if primary_asset else None,
            summary_json=summary_json,
            tags_json={
                "sensor": "LT1",
                "product": "LandSAR SBAS",
                "processor_code": "landsar_sbas",
                "admin_region": (admin_region or {}).get("display_name") if isinstance(admin_region, dict) else None,
            },
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            geom=from_shape(poly, srid=4326) if poly is not None else None,
            coverage_polygon=(coverage.get("geojson") or coverage.get("scene_footprints_geojson")),
            produced_at=produced_at,
            published_at=produced_at,
        )
        for asset in assets:
            product.assets.append(asset)
            if asset.is_required and not asset.exists_flag:
                product.issues.append(
                    ResultIssueORM(
                        asset=asset,
                        issue_code="MISSING_REQUIRED_ASSET",
                        severity="ERROR",
                        status="OPEN",
                        scope="file",
                        message=f"Required LandSAR SBAS asset is missing: {asset.relative_path}",
                    )
                )
        if not preview_asset:
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_PREVIEW",
                    severity="WARN",
                    status="OPEN",
                    scope="product",
                    message="LandSAR SBAS preview PNG is missing.",
                )
            )
        if poly is None:
            product.issues.append(
                ResultIssueORM(
                    issue_code="MISSING_COVERAGE",
                    severity="WARN",
                    status="OPEN",
                    scope="product",
                    message="No valid EPSG:4326 geographic coverage bbox was found.",
                )
            )
        return product

    async def rebuild_catalog(self, db: AsyncSession, *, full_rebuild: bool = True) -> dict[str, Any]:
        run_root = self.get_run_root()
        run_roots = self.get_run_roots()
        manifest_paths = await asyncio.to_thread(self._iter_run_manifest_paths)
        fingerprint = await asyncio.to_thread(self._tree_fingerprint, manifest_paths)
        state = await self._get_or_create_catalog_state(db, storage_root=run_root)
        state.status = "REBUILDING"
        state.needs_rebuild = False
        state.last_message = "SBAS catalog rebuild in progress"
        await db.commit()

        if full_rebuild:
            await db.execute(delete(ResultProductORM).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME))
            await db.commit()

        registered = 0
        failed = 0
        issue_count = 0
        details: list[dict[str, Any]] = []
        for manifest_path in manifest_paths:
            try:
                product = await asyncio.to_thread(self._build_product, manifest_path)
                product_issue_count = len(product.issues)
                product_id = product.product_id
                product_status = product.status
                db.add(product)
                await db.flush()
                await db.commit()
                registered += 1
                issue_count += product_issue_count
                details.append(
                    {
                        "manifest_path": manifest_path,
                        "product_id": product_id,
                        "status": product_status,
                        "issues": product_issue_count,
                    }
                )
            except Exception as exc:
                await db.rollback()
                failed += 1
                issue_count += 1
                details.append({"manifest_path": manifest_path, "status": "error", "message": str(exc)})

        await db.commit()
        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        )
        db_count = int(db_count_result.scalar_one() or 0)
        fingerprint = await asyncio.to_thread(self._tree_fingerprint, manifest_paths)
        state = await self._get_or_create_catalog_state(db, storage_root=run_root)
        state.manifest_count = len(manifest_paths)
        state.manifest_fingerprint = fingerprint
        state.db_count = db_count
        state.issue_count = issue_count
        state.needs_rebuild = False
        state.status = "READY" if failed == 0 else "WARN"
        now = _utcnow()
        state.last_full_rebuild_at = now
        state.last_incremental_scan_at = now
        state.last_message = (
            f"SBAS catalog rebuild finished: runs={len(manifest_paths)}, "
            f"registered={registered}, failed={failed}, issues={issue_count}"
        )
        await db.commit()
        return {
            "catalog_name": SBAS_INSAR_CATALOG_NAME,
            "storage_root": run_root,
            "storage_roots": run_roots,
            "run_count": len(manifest_paths),
            "manifest_count": len(manifest_paths),
            "manifest_fingerprint": fingerprint,
            "registered": registered,
            "failed": failed,
            "issue_count": issue_count,
            "details": details,
        }

    async def list_products(
        self,
        db: AsyncSession,
        *,
        limit: int = 100,
        offset: int = 0,
        status: Optional[str] = None,
        query: Optional[str] = None,
        admin_region: Optional[str] = None,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 100), 500))
        safe_offset = max(0, int(offset or 0))
        stmt = select(ResultProductORM).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        count_stmt = select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        if status:
            stmt = stmt.where(ResultProductORM.status == status)
            count_stmt = count_stmt.where(ResultProductORM.status == status)
        if query:
            like_value = f"%{query.strip()}%"
            predicate = or_(
                ResultProductORM.display_name.ilike(like_value),
                ResultProductORM.product_id.ilike(like_value),
                ResultProductORM.run_key.ilike(like_value),
                ResultProductORM.stack_key.ilike(like_value),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        if admin_region:
            like_value = f"%{admin_region.strip()}%"
            predicate = or_(
                cast(ResultProductORM.summary_json, String).ilike(like_value),
                cast(ResultProductORM.tags_json, String).ilike(like_value),
            )
            stmt = stmt.where(predicate)
            count_stmt = count_stmt.where(predicate)
        total_result = await db.execute(count_stmt)
        total = int(total_result.scalar_one() or 0)
        result = await db.execute(
            stmt.order_by(ResultProductORM.published_at.desc().nullslast(), ResultProductORM.id.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        items: list[dict[str, Any]] = []
        for product in result.scalars().all():
            summary = product.summary_json or {}
            items.append(
                {
                    "id": product.id,
                    "product_id": product.product_id,
                    "display_name": product.display_name,
                    "run_key": product.run_key,
                    "stack_key": product.stack_key,
                    "engine_code": product.engine_code,
                    "processor_code": product.processor_code,
                    "runtime_id": product.runtime_id,
                    "status": product.status,
                    "health_status": product.health_status,
                    "preview_path": product.preview_path,
                    "primary_asset_path": product.primary_asset_path,
                    "reference_date": summary.get("reference_date"),
                    "date_start": summary.get("date_start"),
                    "date_end": summary.get("date_end"),
                    "stack_dates": summary.get("stack_dates") or [],
                    "stack_size": summary.get("stack_size") or len(summary.get("stack_dates") or []),
                    "scene_count": summary.get("scene_count"),
                    "pair_count": summary.get("pair_count"),
                    "los_sign_convention": summary.get("los_sign_convention"),
                    "color_policy": summary.get("color_policy"),
                    "center": summary.get("center") or ((summary.get("geographic_coverage") or {}).get("center")),
                    "admin_region": summary.get("admin_region") or ((summary.get("geographic_coverage") or {}).get("admin_region")),
                    "min_lon": product.min_lon,
                    "min_lat": product.min_lat,
                    "max_lon": product.max_lon,
                    "max_lat": product.max_lat,
                    "published_at": product.published_at,
                }
            )
        return {
            "items": items,
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(items) < total,
        }

    async def get_product_detail(self, db: AsyncSession, *, product_db_id: int) -> Optional[dict[str, Any]]:
        result = await db.execute(select(ResultProductORM).where(ResultProductORM.id == product_db_id))
        product = result.scalar_one_or_none()
        if product is None or product.catalog_name != SBAS_INSAR_CATALOG_NAME:
            return None
        assets_result = await db.execute(
            select(ResultAssetORM)
            .where(ResultAssetORM.product_ref_id == product.id)
            .order_by(ResultAssetORM.is_primary.desc(), ResultAssetORM.asset_role.asc(), ResultAssetORM.id.asc())
        )
        issues_result = await db.execute(
            select(ResultIssueORM)
            .where(ResultIssueORM.product_ref_id == product.id)
            .order_by(ResultIssueORM.severity.asc(), ResultIssueORM.id.asc())
        )
        summary = product.summary_json or {}
        return {
            "id": product.id,
            "product_id": product.product_id,
            "catalog_name": product.catalog_name,
            "product_type": product.product_type,
            "display_name": product.display_name,
            "run_key": product.run_key,
            "run_id": summary.get("run_id") or product.run_key,
            "stack_key": product.stack_key,
            "profile_code": product.profile_code,
            "engine_code": product.engine_code,
            "engine_version": product.engine_version,
            "package_schema": product.package_schema,
            "package_layout": product.package_layout,
            "processor_code": product.processor_code,
            "runtime_id": product.runtime_id,
            "status": product.status,
            "health_status": product.health_status,
            "publish_dir": product.publish_dir,
            "manifest_path": product.manifest_path,
            "source_primary_path": product.source_primary_path,
            "native_output_dir": product.native_output_dir,
            "preview_path": product.preview_path,
            "primary_asset_path": product.primary_asset_path,
            "reference_date": summary.get("reference_date"),
            "date_start": summary.get("date_start"),
            "date_end": summary.get("date_end"),
            "stack_dates": summary.get("stack_dates") or [],
            "stack_size": summary.get("stack_size") or len(summary.get("stack_dates") or []),
            "scene_count": summary.get("scene_count"),
            "pair_count": summary.get("pair_count"),
            "los_sign_convention": summary.get("los_sign_convention"),
            "default_los_product": summary.get("default_los_product"),
            "color_policy": summary.get("color_policy"),
            "quality": summary.get("quality"),
            "monitor_points": summary.get("monitor_points"),
            "point_vector": summary.get("point_vector"),
            "unwrapped_phase": summary.get("unwrapped_phase"),
            "workflow": summary.get("workflow"),
            "geographic_coverage": summary.get("geographic_coverage"),
            "center": summary.get("center") or ((summary.get("geographic_coverage") or {}).get("center")),
            "admin_region": summary.get("admin_region") or ((summary.get("geographic_coverage") or {}).get("admin_region")),
            "coverage_polygon": product.coverage_polygon,
            "min_lon": product.min_lon,
            "min_lat": product.min_lat,
            "max_lon": product.max_lon,
            "max_lat": product.max_lat,
            "produced_at": product.produced_at,
            "published_at": product.published_at,
            "registered_at": product.registered_at,
            "updated_at": product.updated_at,
            "assets": [
                {
                    "id": asset.id,
                    "asset_role": asset.asset_role,
                    "asset_name": asset.asset_name,
                    "relative_path": asset.relative_path,
                    "absolute_path": asset.absolute_path,
                    "format": asset.format,
                    "media_type": asset.media_type,
                    "is_required": asset.is_required,
                    "is_primary": asset.is_primary,
                    "exists_flag": asset.exists_flag,
                    "file_size": asset.file_size,
                    "srid": asset.srid,
                }
                for asset in assets_result.scalars().all()
            ],
            "issues": [
                {
                    "id": issue.id,
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "status": issue.status,
                    "scope": issue.scope,
                    "message": issue.message,
                    "detected_at": issue.detected_at,
                }
                for issue in issues_result.scalars().all()
            ],
        }

    async def query_point_timeseries(
        self,
        db: AsyncSession,
        *,
        product_db_id: int,
        lon: float,
        lat: float,
    ) -> Optional[dict[str, Any]]:
        result = await db.execute(select(ResultProductORM).where(ResultProductORM.id == product_db_id))
        product = result.scalar_one_or_none()
        if product is None or product.catalog_name != SBAS_INSAR_CATALOG_NAME:
            return None
        manifest_path = str(product.manifest_path or "").strip()
        run_dir = Path(manifest_path).parent if manifest_path else Path(str(product.native_output_dir or ""))
        if not run_dir.is_dir():
            raise FileNotFoundError("SBAS run directory not found")
        manifest = _safe_read_json(run_dir / "run_manifest.json")
        if not self._is_expert_gamma_manifest(manifest, run_dir):
            raise ValueError("point time-series query is only available for expert Gamma SBAS products")
        return _query_expert_gamma_point_timeseries(run_dir, lon=lon, lat=lat)

    async def get_asset(self, db: AsyncSession, *, product_db_id: int, asset_id: int) -> Optional[ResultAssetORM]:
        result = await db.execute(
            select(ResultAssetORM)
            .join(ResultProductORM, ResultProductORM.id == ResultAssetORM.product_ref_id)
            .where(
                ResultProductORM.id == product_db_id,
                ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME,
                ResultAssetORM.id == asset_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_catalog_status(self, db: AsyncSession) -> dict[str, Any]:
        run_root = self.get_run_root()
        run_roots = self.get_run_roots()
        manifest_paths = await asyncio.to_thread(self._iter_run_manifest_paths)
        fingerprint = await asyncio.to_thread(self._tree_fingerprint, manifest_paths)
        state = await self._get_or_create_catalog_state(db, storage_root=run_root)
        db_count_result = await db.execute(
            select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
        )
        db_count = int(db_count_result.scalar_one() or 0)
        needs_rebuild = (
            state.manifest_count != len(manifest_paths)
            or state.db_count != db_count
            or state.manifest_fingerprint != fingerprint
        )
        state.manifest_count = len(manifest_paths)
        state.db_count = db_count
        state.needs_rebuild = needs_rebuild
        state.last_incremental_scan_at = _utcnow()
        state.status = "WARN" if needs_rebuild else "READY"
        state.last_message = (
            f"SBAS catalog rebuild required: runs={len(manifest_paths)}, db={db_count}"
            if needs_rebuild
            else "SBAS catalog is in sync"
        )
        await db.commit()
        return {
            "catalog_name": state.catalog_name,
            "product_family": state.product_family,
            "storage_root": state.storage_root,
            "storage_roots": run_roots,
            "status": state.status,
            "needs_rebuild": state.needs_rebuild,
            "run_count": len(manifest_paths),
            "manifest_count": state.manifest_count,
            "manifest_fingerprint": state.manifest_fingerprint,
            "current_manifest_fingerprint": fingerprint,
            "db_count": db_count,
            "issue_count": state.issue_count,
            "last_message": state.last_message,
            "last_boot_check_at": state.last_boot_check_at,
            "last_full_rebuild_at": state.last_full_rebuild_at,
            "last_incremental_scan_at": state.last_incremental_scan_at,
        }

    async def bootstrap_catalog_on_startup_clean(self) -> dict[str, Any]:
        from ..database import AsyncSessionLocal

        if AsyncSessionLocal is None:
            raise RuntimeError("Database session factory is not initialized.")
        async with AsyncSessionLocal() as db:
            run_root = self.get_run_root()
            run_roots = self.get_run_roots()
            manifest_paths = await asyncio.to_thread(self._iter_run_manifest_paths)
            fingerprint = await asyncio.to_thread(self._tree_fingerprint, manifest_paths)
            state = await self._get_or_create_catalog_state(db, storage_root=run_root)
            db_count_result = await db.execute(
                select(func.count(ResultProductORM.id)).where(ResultProductORM.catalog_name == SBAS_INSAR_CATALOG_NAME)
            )
            db_count = int(db_count_result.scalar_one() or 0)
            needs_rebuild = (
                state.manifest_count != len(manifest_paths)
                or state.db_count != db_count
                or state.manifest_fingerprint != fingerprint
            )
            state.last_boot_check_at = _utcnow()
            await db.commit()

            rebuilt = False
            result: dict[str, Any] = {}
            if needs_rebuild and settings.RESULT_CATALOG_AUTO_REBUILD_ON_STARTUP:
                result = await self.rebuild_catalog(db, full_rebuild=True)
                rebuilt = True
                db_count = int(result.get("registered") or db_count)
            else:
                state.manifest_count = len(manifest_paths)
                state.db_count = db_count
                state.manifest_fingerprint = fingerprint if not needs_rebuild else state.manifest_fingerprint
                state.needs_rebuild = needs_rebuild
                state.status = "WARN" if needs_rebuild else "READY"
                state.last_message = "SBAS boot check complete"
                await db.commit()

            return {
                "storage_root": run_root,
                "storage_roots": run_roots,
                "manifest_count": len(manifest_paths),
                "current_manifest_fingerprint": fingerprint,
                "indexed_manifest_fingerprint": state.manifest_fingerprint,
                "db_count": db_count,
                "needs_rebuild": needs_rebuild and not rebuilt,
                "rebuilt": rebuilt,
                "queued": False,
                "registered": result.get("registered"),
                "failed": result.get("failed"),
            }


sbas_insar_catalog_service = SbasInsarCatalogService()
