"""Convert GF3 SARscape native geocoded outputs to platform GeoTIFFs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from geoalchemy2.shape import from_shape
from shapely.geometry import Polygon
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings, split_env_paths
from ..models import ManagedRootORM, RadarDataORM, SARSceneGeoORM, SourceProductAssetORM
from .data_service import DataService, extract_geotiff_bounds
from .gf3_native_inventory_service import (
    NATIVE_MANIFEST_NAME,
    POLARIZATION_PRIORITY,
    SKIP_DIR_NAMES,
    scan_gf3_sarscape_native_roots,
)
from .image_service import image_service
from .sar_analysis_ready_service import register_analysis_ready_tif

STANDARD_MANIFEST_NAME = "gf3_standard_manifest.json"
STANDARD_MANIFEST_SCHEMA = "gf3_standard_geotiff.v1"
CONVERTER_NAME = "gf3_sarscape_geo_to_tif"
CONVERTER_VERSION = "v1"
SOURCE_ASSET_FORMAT = "GF3_SARSCAPE_L2"
SOURCE_ASSET_QUICKLOOK_FORMAT = "GF3_SARSCAPE_QUICKLOOK"
SOURCE_ASSET_NATIVE_PREVIEW_FORMAT = "GF3_SARSCAPE_NATIVE_PREVIEW"
QUICKLOOK_PREVIEW_CACHE_VERSION = "gf3_native_webp.v2"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _path_text(value: Any) -> str:
    text = str(value or "").strip()
    return os.path.normpath(text) if text else ""


def _db_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _date_to_naive_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return None


def _path_kind(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith("\\\\"):
        return "unc"
    if len(text) >= 3 and text[1:3] in {":\\", ":/"} and text[0].isalpha():
        return "windows"
    if text.startswith("/mnt/"):
        return "wsl_mount"
    if text.startswith("/"):
        return "posix"
    return "relative"


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path))).lower()


def _has_native_geo_assets(path: Path) -> bool:
    try:
        return any(item.is_file() and item.name.lower().endswith("_geo") for item in path.iterdir())
    except OSError:
        return False


def _discover_gf3_sarscape_scan_roots(native_dirs: list[str] | tuple[str, ...] | None) -> list[str]:
    """Expand stable GF3 roots into SARscape ``*_geo`` result pools before scanning.

    Operators usually configure a durable GF3 root rather than a date folder.
    Date-specific SARscape output pools live below it as ``YYYYMMDD_geo``. Prefer
    direct ``*_geo`` children and fall back to recursive discovery.
    """
    roots: list[str] = []
    seen: set[str] = set()

    def add(path: str | Path) -> None:
        text = os.path.normpath(str(path))
        key = _path_key(text)
        if key and key not in seen:
            seen.add(key)
            roots.append(text)

    for raw in native_dirs or []:
        text = str(raw or "").strip()
        if not text:
            continue

        if any(ch in text for ch in "*?"):
            glob_root = Path(os.path.normpath(text))
            try:
                matches = sorted(glob_root.parent.glob(glob_root.name), key=lambda item: item.name.lower())
            except OSError:
                matches = []
            if matches:
                for match in matches:
                    if match.is_dir():
                        add(match)
                continue
            add(glob_root)
            continue

        root = Path(os.path.normpath(text))
        try:
            is_dir = root.is_dir()
        except OSError:
            is_dir = False
        if not is_dir:
            add(root)
            continue

        if root.name.lower().endswith("_geo"):
            add(root)

        try:
            immediate_geo_roots = sorted(
                (
                    child
                    for child in root.iterdir()
                    if child.is_dir() and child.name.lower().endswith("_geo")
                ),
                key=lambda item: item.name.lower(),
            )
        except OSError:
            immediate_geo_roots = []

        if immediate_geo_roots:
            for child in immediate_geo_roots:
                add(child)
            continue

        if _has_native_geo_assets(root):
            add(root)
            continue

        found_recursive = 0
        for current_dir, dir_names, _file_names in os.walk(root):
            dir_names[:] = [
                name
                for name in dir_names
                if name not in SKIP_DIR_NAMES and not name.startswith(".SARscape")
                and not name.startswith(".gf3_")
            ]
            selected: list[str] = []
            remaining: list[str] = []
            for name in dir_names:
                if name.lower().endswith("_geo"):
                    selected.append(name)
                else:
                    remaining.append(name)
            for name in selected:
                add(Path(current_dir) / name)
            found_recursive += len(selected)
            dir_names[:] = remaining

        if found_recursive == 0:
            add(root)

    return roots


def _source_asset_uid(path: str) -> str:
    normalized = os.path.normpath(str(path or "").strip())
    digest = hashlib.sha1(normalized.lower().encode("utf-8", errors="ignore")).hexdigest()
    return f"source:{digest[:32]}"


def _safe_slug(value: Any, *, default: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text).strip("._-")
    return safe or default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as stream:
        json.dump(_json_safe(payload), stream, ensure_ascii=False, indent=2, default=str, allow_nan=False)
    os.replace(tmp_path, path)


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _file_fingerprint(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "path": str(path),
        "size": int(stat.st_size),
        "mtime": float(stat.st_mtime),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _tree_stats(path: Path) -> dict[str, Any]:
    if path.is_file():
        info = _file_fingerprint(path)
        return {
            "size_bytes": info.get("size") if info else None,
            "mtime_epoch": info.get("mtime") if info else None,
        }

    total = 0
    newest: float | None = None
    try:
        iterator = path.rglob("*")
        for item in iterator:
            try:
                if not item.is_file():
                    continue
                stat = item.stat()
            except OSError:
                continue
            total += int(stat.st_size)
            mtime = float(stat.st_mtime)
            newest = mtime if newest is None else max(newest, mtime)
    except OSError:
        return {"size_bytes": None, "mtime_epoch": None}
    return {"size_bytes": total, "mtime_epoch": newest}


async def _find_managed_root_for_path(db: AsyncSession, path: str) -> ManagedRootORM | None:
    target = os.path.normcase(os.path.normpath(str(path or "")))
    if not target:
        return None
    result = await db.execute(
        select(ManagedRootORM)
        .where(ManagedRootORM.enabled == True)  # noqa: E712
        .order_by(func.length(ManagedRootORM.path).desc())
    )
    for root in result.scalars().all():
        root_path = os.path.normcase(os.path.normpath(str(root.path or "")))
        if target == root_path or target.startswith(root_path + os.sep):
            return root
    return None


def _batch_name(scene_manifest: dict[str, Any]) -> str:
    raw = scene_manifest.get("batch_name") or (scene_manifest.get("metadata") or {}).get("imaging_date")
    return _safe_slug(raw, default="unknown_batch")


def _standard_scene_dir(scene_manifest: dict[str, Any], storage_root: Path) -> Path:
    return storage_root / _batch_name(scene_manifest) / _safe_slug(scene_manifest.get("scene_name"))


def _target_tif_path(scene_manifest: dict[str, Any], asset: dict[str, Any], storage_root: Path) -> Path:
    pol = _safe_slug(asset.get("polarization"), default="UNKNOWN").upper()
    return _standard_scene_dir(scene_manifest, storage_root) / f"{pol}_L2.tif"


def _preview_path(scene_manifest: dict[str, Any], asset: dict[str, Any], storage_root: Path) -> Path:
    pol = _safe_slug(asset.get("polarization"), default="UNKNOWN").upper()
    return _standard_scene_dir(scene_manifest, storage_root) / f"preview_{pol}.png"


def _quality_path(scene_manifest: dict[str, Any], asset: dict[str, Any], storage_root: Path) -> Path:
    pol = _safe_slug(asset.get("polarization"), default="UNKNOWN").upper()
    return _standard_scene_dir(scene_manifest, storage_root) / f"quality_{pol}.json"


def _source_changed(asset: dict[str, Any], target_tif: Path, existing_asset: dict[str, Any] | None) -> bool:
    source_path = Path(_path_text(asset.get("path")))
    source_fp = _file_fingerprint(source_path)
    if not target_tif.is_file() or target_tif.stat().st_size <= 0:
        return True
    if not existing_asset:
        return True
    if str(existing_asset.get("source_native") or "") != str(source_path):
        return True
    if (existing_asset.get("source_fingerprint") or {}) != source_fp:
        return True
    if str(existing_asset.get("converter_version") or "") != CONVERTER_VERSION:
        return True
    return False


def _existing_manifest_asset(standard_manifest: dict[str, Any] | None, polarization: str) -> dict[str, Any] | None:
    if not standard_manifest:
        return None
    target_pol = str(polarization or "").upper()
    for item in standard_manifest.get("assets") or []:
        if str(item.get("polarization") or "").upper() == target_pol:
            return item
    return None


def _convert_native_asset_to_tif(asset: dict[str, Any], target_tif: Path) -> dict[str, Any]:
    source_path = Path(_path_text(asset.get("path")))
    if not source_path.is_file():
        raise FileNotFoundError(f"GF3 native data file does not exist: {source_path}")
    hdr_path = Path(_path_text(asset.get("hdr")))
    if not hdr_path.is_file():
        raise FileNotFoundError(f"GF3 native ENVI header does not exist: {hdr_path}")

    target_tif.parent.mkdir(parents=True, exist_ok=True)
    tmp_tif = target_tif.with_name(f".{target_tif.name}.tmp.tif")
    if tmp_tif.exists():
        tmp_tif.unlink()

    try:
        from osgeo import gdal

        src_ds = gdal.Open(str(source_path), gdal.GA_ReadOnly)
        if src_ds is None:
            raise RuntimeError(f"GDAL cannot open GF3 native dataset: {source_path}")

        creation_options = ["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"]
        if bool(settings.SAR_ANALYSIS_OUTPUT_COG):
            creation_options.append("COPY_SRC_OVERVIEWS=YES")
        translated = gdal.Translate(
            str(tmp_tif),
            src_ds,
            format="GTiff",
            creationOptions=creation_options,
        )
        src_ds = None
        if translated is None:
            raise RuntimeError(f"GDAL Translate failed for GF3 native dataset: {source_path}")
        translated.FlushCache()
        translated = None
    except ImportError:
        import rasterio

        with rasterio.open(source_path) as src:
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                tiled=True,
                compress="deflate",
                BIGTIFF="IF_SAFER",
            )
            with rasterio.open(tmp_tif, "w", **profile) as dst:
                for band_idx in range(1, src.count + 1):
                    for _block_index, window in src.block_windows(band_idx):
                        dst.write(src.read(band_idx, window=window), band_idx, window=window)
                dst.update_tags(**src.tags())
                for band_idx in range(1, src.count + 1):
                    dst.update_tags(band_idx, **src.tags(band_idx))

    os.replace(tmp_tif, target_tif)

    return {
        "path": str(target_tif),
        "source_native": str(source_path),
        "source_fingerprint": _file_fingerprint(source_path),
        "converter_name": CONVERTER_NAME,
        "converter_version": CONVERTER_VERSION,
    }


def _raster_quality(path: Path) -> dict[str, Any]:
    try:
        import numpy as np
        import rasterio
    except Exception as exc:
        return {"ok": False, "warning": f"rasterio unavailable: {exc}"}

    with rasterio.open(path) as src:
        if src.height > 2048 or src.width > 2048:
            scale = min(1024 / src.width, 1024 / src.height)
            out_width = max(1, int(src.width * scale))
            out_height = max(1, int(src.height * scale))
            sampled = src.read(1, out_shape=(out_height, out_width), masked=True)
        else:
            sampled = src.read(1, masked=True)

        valid = sampled.compressed() if hasattr(sampled, "compressed") else sampled[np.isfinite(sampled)]
        bounds = src.bounds
        quality: dict[str, Any] = {
            "ok": True,
            "driver": src.driver,
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": str(src.dtypes[0]) if src.dtypes else None,
            "crs": src.crs.to_string() if src.crs else None,
            "bounds": {
                "left": bounds.left,
                "bottom": bounds.bottom,
                "right": bounds.right,
                "top": bounds.top,
            },
            "transform": list(src.transform)[:6],
            "nodata": _finite_float(src.nodata),
            "valid_sample_count": int(valid.size),
            "valid_sample_percent": float(valid.size / sampled.size) if sampled.size else 0.0,
        }
        if valid.size:
            quality.update(
                {
                    "sample_min": float(np.nanmin(valid)),
                    "sample_max": float(np.nanmax(valid)),
                    "sample_mean": float(np.nanmean(valid)),
                    "sample_p02": float(np.nanpercentile(valid, 2)),
                    "sample_p98": float(np.nanpercentile(valid, 98)),
                }
            )
        return quality


def _build_preview_png(source: Path, target: Path) -> str | None:
    try:
        import numpy as np
        import rasterio
        from PIL import Image
    except Exception:
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source) as src:
        if src.height > 1600 or src.width > 1600:
            scale = min(1600 / src.width, 1600 / src.height)
            out_width = max(1, int(src.width * scale))
            out_height = max(1, int(src.height * scale))
            band = src.read(1, out_shape=(out_height, out_width), masked=True)
        else:
            band = src.read(1, masked=True)
        data = band.filled(float("nan")).astype("float32")

    valid = data[np.isfinite(data)]
    if valid.size:
        p2, p98 = np.nanpercentile(valid, [2, 98])
        normalized = np.clip((data - p2) / max(p98 - p2, 1e-6), 0, 1)
        normalized = np.where(np.isfinite(normalized), normalized, 0)
        gray = (normalized * 255).astype("uint8")
    else:
        gray = np.zeros(data.shape, dtype="uint8")
    alpha = np.where(np.isfinite(data), 255, 0).astype("uint8")
    rgba = np.stack([gray, gray, gray, alpha], axis=-1)
    Image.fromarray(rgba, "RGBA").save(target)
    return str(target)


def _build_preview_from_quicklook(source: Path | None, target: Path) -> str | None:
    if source is None or not source.is_file():
        return None
    try:
        from PIL import Image
    except Exception:
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as img:
            preview = img.copy()
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            preview.thumbnail((1600, 1600), resampling)
            if preview.mode in {"1", "I", "I;16", "F"}:
                preview = preview.convert("L")
            elif preview.mode not in {"L", "LA", "RGB", "RGBA"}:
                preview = preview.convert("RGB")
            preview = image_service.make_edge_dark_transparent(preview)
            preview.save(target, "PNG")
        return str(target)
    except Exception:
        return None


def _asset_quicklook_path(asset: dict[str, Any]) -> Path | None:
    text = _path_text(asset.get("quicklook"))
    if not text:
        return None
    return Path(text)


def _points_look_like_lonlat(points: list[tuple[float, float]]) -> bool:
    if not points:
        return False
    for lon, lat in points:
        if not (math.isfinite(float(lon)) and math.isfinite(float(lat))):
            return False
        if not (-180.0 <= float(lon) <= 180.0 and -90.0 <= float(lat) <= 90.0):
            return False
    return True


def _crs_is_geographic_lonlat(crs: Any) -> bool:
    if not crs:
        return False
    try:
        if crs.to_epsg() == 4326:
            return True
    except Exception:
        pass
    try:
        if bool(crs.is_geographic):
            return True
    except Exception:
        pass
    try:
        wkt = str(crs.to_wkt() or "").upper()
        if "GEOGCS" in wkt and ("WGS 84" in wkt or "WORLD GEODETIC" in wkt):
            return True
    except Exception:
        pass
    return False


def _polygon_from_raster(path: Path) -> list[tuple[float, float]] | None:
    polygon = extract_geotiff_bounds(str(path))
    if polygon and len(polygon) >= 4:
        return polygon
    try:
        import rasterio
        from rasterio.warp import transform

        with rasterio.open(path) as src:
            if not src.crs:
                return None
            corners_xy = [
                src.transform * (0, 0),
                src.transform * (src.width, 0),
                src.transform * (src.width, src.height),
                src.transform * (0, src.height),
            ]
            xs = [point[0] for point in corners_xy]
            ys = [point[1] for point in corners_xy]
            raw_points = [(float(x), float(y)) for x, y in zip(xs, ys)]
            if _crs_is_geographic_lonlat(src.crs):
                points = raw_points
            else:
                try:
                    lons, lats = transform(src.crs, "EPSG:4326", xs, ys)
                    points = [(float(lon), float(lat)) for lon, lat in zip(lons, lats)]
                except Exception:
                    if not _points_look_like_lonlat(raw_points):
                        raise
                    points = raw_points
            if not _points_look_like_lonlat(points):
                return None
            points.append(points[0])
            return points
    except Exception:
        return None
    return None


def _polygon_from_native_asset(asset: dict[str, Any] | None) -> list[tuple[float, float]] | None:
    if not asset:
        return None
    for key in ("path", "quicklook"):
        text = _path_text(asset.get(key))
        if not text:
            continue
        path = Path(text)
        if not path.is_file():
            continue
        polygon = _polygon_from_raster(path)
        if polygon and len(polygon) >= 4:
            return polygon
    return None


def _scene_center_from_polygon(polygon: list[tuple[float, float]] | None) -> tuple[float | None, float | None]:
    if not polygon:
        return None, None
    try:
        shp = Polygon(polygon)
        if not shp.is_valid:
            shp = shp.buffer(0)
        if shp.is_valid and not shp.is_empty:
            return float(shp.centroid.x), float(shp.centroid.y)
    except Exception:
        return None, None
    return None, None


def _bounds_from_polygon(polygon: list[tuple[float, float]] | None) -> tuple[float | None, float | None, float | None, float | None]:
    if not polygon:
        return None, None, None, None
    lons = [float(point[0]) for point in polygon]
    lats = [float(point[1]) for point in polygon]
    return min(lons), min(lats), max(lons), max(lats)


def _geom_from_polygon(polygon: list[tuple[float, float]] | None) -> Any | None:
    if not polygon:
        return None
    try:
        shp = Polygon(polygon)
        if not shp.is_valid:
            shp = shp.buffer(0)
        if shp.is_valid and not shp.is_empty:
            return from_shape(shp, srid=4326)
    except Exception:
        return None
    return None


def _select_default_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_pol = {str(asset.get("polarization") or "").upper(): asset for asset in assets}
    for pol in POLARIZATION_PRIORITY:
        if pol in by_pol:
            return by_pol[pol]
    return assets[0] if assets else None


def _is_quicklook_raster_path(path: Any) -> bool:
    text = _path_text(path).lower()
    return text.endswith("_ql.tif") or text.endswith("_ql.tiff")


def _select_default_native_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        asset
        for asset in assets
        if _path_text(asset.get("path")) and not _is_quicklook_raster_path(asset.get("path"))
    ]
    complete = [asset for asset in candidates if asset.get("complete") is not False]
    return _select_default_asset(complete or candidates)


def _quicklook_assets_from_scene_manifest(scene_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    output_assets: list[dict[str, Any]] = []
    for asset in scene_manifest.get("assets") or []:
        native_path = _path_text(asset.get("path"))
        if not native_path or _is_quicklook_raster_path(native_path) or asset.get("complete") is not True:
            continue
        output_assets.append(
            {
                "polarization": str(asset.get("polarization") or "UNKNOWN").upper(),
                "role": "native_geo",
                "path": native_path,
                "hdr": _path_text(asset.get("hdr")) or None,
                "sml": _path_text(asset.get("sml")) or None,
                "quicklook": _path_text(asset.get("quicklook")) or None,
                "complete": bool(asset.get("complete")),
                "status": "registered_native_geo",
            }
        )
    return output_assets


def _quicklook_manifest_for_scene(scene_manifest: dict[str, Any], storage_root: str | Path | None) -> dict[str, Any]:
    root = Path(storage_root or settings.GF3_STORAGE_DIRS).resolve()
    out_dir = _standard_scene_dir(scene_manifest, root)
    manifest_path = out_dir / "gf3_native_preview_manifest.json"
    assets = _quicklook_assets_from_scene_manifest(scene_manifest)
    manifest = {
        "schema": "gf3_sarscape_native_preview.v1",
        "generated_at": _utc_now(),
        "scene_name": scene_manifest.get("scene_name"),
        "batch_name": scene_manifest.get("batch_name"),
        "native_manifest": scene_manifest.get("manifest_path") or str(Path(scene_manifest.get("native_dir") or "") / NATIVE_MANIFEST_NAME),
        "native_dir": scene_manifest.get("native_dir"),
        "standard_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "status": "NATIVE_READY" if assets else "FAILED",
        "assets": assets,
        "summary": {
            "native_assets": len(assets),
            "quicklook_assets": 0,
            "full_raster_materialized": True,
            "webp_source": "native_geo",
        },
        "errors": [] if assets else [{"error": "no native _geo assets found"}],
    }
    _write_json(manifest_path, manifest)
    return manifest


def _read_geotiff_quicklook_webp(source_path: str, target_path: str, *, max_size: int | None = None) -> bool:
    """Build a local WebP from a registered GF3 raster, including ENVI ``*_geo``."""
    try:
        import numpy as np
        import rasterio
        from PIL import Image
        from rasterio.enums import Resampling
    except Exception:
        return False

    try:
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        limit = int(max_size or settings.RADAR_THUMBNAIL_MAX_SIZE or 1600)
        with rasterio.open(source_path) as src:
            scale = min(limit / max(src.width, 1), limit / max(src.height, 1), 1.0)
            out_width = max(1, int(src.width * scale))
            out_height = max(1, int(src.height * scale))
            band = src.read(
                1,
                out_shape=(out_height, out_width),
                masked=True,
                resampling=Resampling.bilinear,
            )
            data = band.filled(0)
            if data.dtype != np.uint8:
                valid = band.compressed() if hasattr(band, "compressed") else data[np.isfinite(data)]
                if valid.size:
                    p2, p98 = np.nanpercentile(valid.astype("float32"), [2, 98])
                    scaled = np.clip((data.astype("float32") - p2) / max(float(p98 - p2), 1e-6), 0, 1)
                    data = (scaled * 255).astype("uint8")
                else:
                    data = np.zeros(data.shape, dtype="uint8")
            else:
                data = data.astype("uint8", copy=False)

            mask = getattr(band, "mask", None)
            if mask is None or np.ndim(mask) == 0:
                alpha = np.full(data.shape, 255, dtype="uint8")
            else:
                alpha = np.where(mask, 0, 255).astype("uint8")
            rgba = np.stack([data, data, data, alpha], axis=-1)

        image = Image.fromarray(rgba, "RGBA")
        image = image_service.make_edge_dark_transparent(image)
        image_service.save_image_as_webp(image, target_path, quality=82)
        return target.exists() and target.stat().st_size > 0
    except Exception:
        return False


def _native_preview_source_from_metadata(metadata: dict[str, Any]) -> str:
    candidates: list[Any] = [metadata.get("default_native_path")]
    for key in ("native_assets", "quicklook_assets"):
        for asset in metadata.get(key) or []:
            if isinstance(asset, dict):
                candidates.append(asset.get("path"))
    for candidate in candidates:
        text = _path_text(candidate)
        if text and not _is_quicklook_raster_path(text):
            return text
    return ""


def _path_is_under_any(path: str, roots: list[str]) -> bool:
    target = _path_key(path)
    if not target:
        return False
    for root in roots:
        root_key = _path_key(root)
        if root_key and (target == root_key or target.startswith(root_key + os.sep)):
            return True
    return False


def _configured_gf3_native_roots(native_dirs: list[str] | tuple[str, ...] | None = None) -> list[str]:
    roots = [str(item) for item in (native_dirs or []) if str(item or "").strip()]
    if not roots:
        roots = split_env_paths(settings.GF3_SARSCAPE_NATIVE_DIRS)
    return [os.path.normpath(root) for root in roots if str(root or "").strip()]


def _scene_native_fingerprint(scene_manifest: dict[str, Any]) -> str:
    return str(
        scene_manifest.get("native_fingerprint")
        or (scene_manifest.get("metadata") or {}).get("native_fingerprint")
        or ""
    ).strip()


def _record_has_bounds(record: RadarDataORM) -> bool:
    return (
        record.coverage_polygon is not None
        and record.min_lon is not None
        and record.min_lat is not None
        and record.max_lon is not None
        and record.max_lat is not None
    )


async def _find_existing_quicklook_radar(
    db: AsyncSession,
    *,
    scene_manifest: dict[str, Any],
) -> RadarDataORM | None:
    native_dir = str(scene_manifest.get("native_dir") or "")
    scene_name = scene_manifest.get("scene_name") or Path(native_dir).name
    unique_id = f"gf3_sarscape_native_preview:{scene_name}"
    result = await db.execute(
        select(RadarDataORM).where(
            or_(
                RadarDataORM.unique_id == unique_id,
                RadarDataORM.file_path == native_dir,
            )
        )
    )
    return result.scalars().first()


async def _can_skip_unchanged_quicklook_radar(
    db: AsyncSession,
    *,
    scene_manifest: dict[str, Any],
) -> tuple[bool, int | None, str]:
    native_fingerprint = _scene_native_fingerprint(scene_manifest)
    if not native_fingerprint:
        return False, None, "missing_native_fingerprint"

    record = await _find_existing_quicklook_radar(db, scene_manifest=scene_manifest)
    if record is None:
        return False, None, "missing_record"

    metadata = record.metadata_json or {}
    if str(metadata.get("native_fingerprint") or "") != native_fingerprint:
        return False, int(record.id) if record.id is not None else None, "native_fingerprint_changed"
    if str(metadata.get("registration_mode") or "") != "native_preview":
        return False, int(record.id) if record.id is not None else None, "registration_mode_changed"
    if not _record_has_bounds(record):
        return False, int(record.id) if record.id is not None else None, "missing_bounds"

    return True, int(record.id) if record.id is not None else None, "unchanged"


async def generate_gf3_quicklook_webp_cache(
    db: AsyncSession,
    *,
    force: bool = False,
    max_records: int | None = None,
    native_dirs: list[str] | tuple[str, ...] | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Generate local WebP previews from registered GF3 SARscape native ``*_geo`` rasters."""
    stmt = (
        select(RadarDataORM)
        .where(RadarDataORM.source_format == SOURCE_ASSET_NATIVE_PREVIEW_FORMAT)
        .order_by(RadarDataORM.imaging_date.desc().nullslast(), RadarDataORM.id.asc())
    )

    result = await db.execute(stmt)
    configured_roots = _configured_gf3_native_roots(native_dirs)
    records = [
        record
        for record in result.scalars().all()
        if _path_is_under_any(_native_preview_source_from_metadata(record.metadata_json or {}), configured_roots)
    ]
    if max_records and max_records > 0:
        records = records[: int(max_records)]
    total = len(records)
    generated = 0
    skipped = 0
    failed = 0
    record_results: list[dict[str, Any]] = []

    for idx, record in enumerate(records):
        metadata = record.metadata_json or {}
        source_path = _native_preview_source_from_metadata(metadata)
        cache_path = DataService.get_radar_raw_cache_path(record.unique_id or record.file_path, record.file_path)
        if progress_callback:
            pct = 5 + int((idx / max(total, 1)) * 90)
            progress_callback(pct, f"Generate GF3 native _geo WebP {idx + 1}/{total}: {record.product_unique_id or record.id}")

        if (
            not force
            and (record.preview_cache_status or "NONE") == "READY"
            and record.preview_cache_path
            and os.path.exists(record.preview_cache_path)
        ):
            skipped += 1
            record_results.append({"id": record.id, "status": "SKIPPED", "cache_path": record.preview_cache_path})
            continue

        if not source_path:
            failed += 1
            record.preview_cache_status = "FAILED"
            record.preview_cache_path = None
            record.preview_cache_error = "default_native_path_missing"
            record.preview_cache_version = QUICKLOOK_PREVIEW_CACHE_VERSION
            record.preview_cache_updated_at = _db_now()
            record_results.append({"id": record.id, "status": "FAILED", "error": record.preview_cache_error})
            await db.commit()
            continue

        ok = await asyncio.to_thread(_read_geotiff_quicklook_webp, source_path, cache_path)
        record.preview_cache_version = QUICKLOOK_PREVIEW_CACHE_VERSION
        record.preview_cache_updated_at = _db_now()
        if ok and os.path.exists(cache_path):
            generated += 1
            record.preview_cache_status = "READY"
            record.preview_cache_path = cache_path
            record.preview_cache_error = None
            record_results.append({"id": record.id, "status": "READY", "cache_path": cache_path})
        else:
            failed += 1
            record.preview_cache_status = "FAILED"
            record.preview_cache_path = None
            record.preview_cache_error = "native_webp_build_failed"
            record_results.append({"id": record.id, "status": "FAILED", "error": record.preview_cache_error})
        await db.commit()

    return {
        "ok": failed == 0,
        "mode": "gf3_native_webp",
        "total": total,
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
        "records": record_results,
    }


async def _upsert_quicklook_source_product_asset(
    db: AsyncSession,
    scene_manifest: dict[str, Any],
    quicklook_manifest: dict[str, Any],
) -> int | None:
    native_dir_text = _path_text(scene_manifest.get("native_dir"))
    if not native_dir_text:
        return None
    metadata = scene_manifest.get("metadata") or {}
    imaging_date = str(metadata.get("imaging_date") or "").strip() or None
    acquisition_start = _date_to_naive_utc(imaging_date)
    scene_name = scene_manifest.get("scene_name") or Path(native_dir_text).name
    now = _db_now()
    root = await _find_managed_root_for_path(db, native_dir_text)
    asset_metadata = _json_safe(
        {
            "source": "GF3 SARscape native _geo",
            "native_dir": native_dir_text,
            "native_manifest": scene_manifest.get("manifest_path"),
            "native_fingerprint": _scene_native_fingerprint(scene_manifest),
            "native_preview_manifest": quicklook_manifest.get("manifest_path"),
            "native_assets": quicklook_manifest.get("assets") or [],
            "full_raster_materialized": True,
            "analysis_engine": "gf3_sarscape",
        }
    )
    data = {
        "asset_uid": _source_asset_uid(f"gf3_native_preview:{native_dir_text}"),
        "logical_product_uid": scene_name,
        "satellite_family": "GF3",
        "satellite": "GF3",
        "source_format": SOURCE_ASSET_NATIVE_PREVIEW_FORMAT,
        "product_type": metadata.get("product_type") or "SARSCAPE_NATIVE",
        "product_level": "L2_NATIVE",
        "imaging_mode": metadata.get("imaging_mode"),
        "polarization": metadata.get("polarization"),
        "absolute_orbit": metadata.get("absolute_orbit") or metadata.get("orbit_circle"),
        "relative_orbit": metadata.get("relative_orbit"),
        "orbit_direction": metadata.get("orbit_direction"),
        "acquisition_start_time_utc": acquisition_start,
        "acquisition_stop_time_utc": None,
        "imaging_date": imaging_date,
        "root_ref_id": root.id if root else None,
        "root_path": root.path if root else str(Path(native_dir_text).parent),
        "file_path": native_dir_text,
        "archive_path": scene_manifest.get("source_archive"),
        "path_kind": _path_kind(native_dir_text),
        "file_name": Path(native_dir_text).name,
        "file_stem": Path(native_dir_text).name,
        "file_ext": "",
        "size_bytes": None,
        "mtime_epoch": None,
        "checksum_status": "NOT_COMPUTED",
        "parser_name": "gf3_sarscape_native_preview_manifest",
        "parser_version": CONVERTER_VERSION,
        "parse_status": "NATIVE_READY" if quicklook_manifest.get("assets") else "FAILED",
        "parse_error": "; ".join(str(item.get("error") or item) for item in (quicklook_manifest.get("errors") or [])) or None,
        "parsed_at": now,
        "metadata_json": asset_metadata,
        "is_active": True,
        "missing_since": None,
        "updated_at": now,
    }
    result = await db.execute(
        select(SourceProductAssetORM).where(
            or_(
                SourceProductAssetORM.asset_uid == data["asset_uid"],
                SourceProductAssetORM.file_path == native_dir_text,
            )
        )
    )
    asset = result.scalars().first()
    if asset is None:
        asset = SourceProductAssetORM(**data)
        db.add(asset)
    else:
        for key, value in data.items():
            setattr(asset, key, value)
    await db.flush()
    return int(asset.id) if asset.id is not None else None


async def _upsert_quicklook_radar_data(
    db: AsyncSession,
    scene_manifest: dict[str, Any],
    quicklook_manifest: dict[str, Any],
    source_product_ref_id: int | None = None,
) -> int | None:
    assets = quicklook_manifest.get("assets") or []
    default_asset = _select_default_native_asset(assets)
    if not default_asset:
        return None
    metadata = scene_manifest.get("metadata") or {}
    imaging_date = str(metadata.get("imaging_date") or "").strip() or None
    acquisition_start = _date_to_naive_utc(imaging_date)
    scene_name = scene_manifest.get("scene_name") or Path(str(scene_manifest.get("native_dir") or "")).name
    native_dir = str(scene_manifest.get("native_dir") or "")
    unique_id = f"gf3_sarscape_native_preview:{scene_name}"
    default_native_path = str(default_asset.get("path") or "")
    default_quicklook_path = str(default_asset.get("quicklook") or "")
    polygon = _polygon_from_native_asset(default_asset)
    min_lon = min_lat = max_lon = max_lat = None
    geom = None
    if polygon:
        min_lon, min_lat, max_lon, max_lat = _bounds_from_polygon(polygon)
        center_lon, center_lat = _scene_center_from_polygon(polygon)
        geom = _geom_from_polygon(polygon)
    else:
        center_lon = metadata.get("scene_center_lon")
        center_lat = metadata.get("scene_center_lat")
    preview_cache_path = None
    preview_cache_kind = "deferred"
    preview_cache_error = "native_webp_not_generated"
    radar_metadata = _json_safe(
        {
            **metadata,
            "native_dir": native_dir,
            "native_manifest": scene_manifest.get("manifest_path"),
            "native_fingerprint": _scene_native_fingerprint(scene_manifest),
            "native_preview_manifest": quicklook_manifest.get("manifest_path"),
            "native_assets": assets,
            "quicklook_assets": [],
            "default_native_path": default_native_path,
            "default_quicklook_path": default_quicklook_path,
            "coverage_source": "native_geo_or_quicklook" if polygon else "scene_name",
            "preview_cache_kind": preview_cache_kind,
            "analysis_engine": "gf3_sarscape",
            "registration_mode": "native_preview",
            "full_raster_materialized": True,
        }
    )
    data_to_upsert = {
        "unique_id": unique_id,
        "satellite": "GF3",
        "satellite_family": "GF3",
        "imaging_date": imaging_date,
        "imaging_mode": metadata.get("imaging_mode"),
        "polarization": ",".join(
            pol
            for pol in POLARIZATION_PRIORITY
            if any(str(asset.get("polarization") or "").upper() == pol for asset in assets)
        )
        or metadata.get("polarization"),
        "scene_center_lon": center_lon,
        "scene_center_lat": center_lat,
        "acquisition_time_utc": acquisition_start.isoformat() if acquisition_start else None,
        "product_level": "L2_NATIVE",
        "product_unique_id": metadata.get("product_unique_id") or scene_name,
        "source_product_token": scene_name,
        "acquisition_start_time_utc": acquisition_start,
        "acquisition_stop_time_utc": None,
        "absolute_orbit": metadata.get("absolute_orbit") or metadata.get("orbit_circle"),
        "relative_orbit": metadata.get("relative_orbit"),
        "source_format": SOURCE_ASSET_NATIVE_PREVIEW_FORMAT,
        "source_product_ref_id": source_product_ref_id,
        "image_data_format": "ENVI_NATIVE",
        "geocoded_flag": True,
        "metadata_json": radar_metadata,
        "file_path": native_dir,
        "has_orbit_data": False,
        "orbit_file_path": None,
        "is_envi_processed": True,
        "coverage_polygon": polygon,
        "geom": geom,
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
        "preview_cache_status": "NONE",
        "preview_cache_version": QUICKLOOK_PREVIEW_CACHE_VERSION,
        "preview_cache_path": preview_cache_path,
        "preview_cache_updated_at": _db_now(),
        "preview_cache_error": preview_cache_error,
    }
    result = await db.execute(
        select(RadarDataORM).where(
            or_(
                RadarDataORM.unique_id == unique_id,
                RadarDataORM.file_path == native_dir,
            )
        )
    )
    radar = result.scalars().first()
    if (
        radar is not None
        and (radar.preview_cache_status or "").upper() == "READY"
        and radar.preview_cache_path
        and os.path.exists(radar.preview_cache_path)
        and str((radar.metadata_json or {}).get("default_native_path") or "") == default_native_path
    ):
        data_to_upsert["preview_cache_status"] = radar.preview_cache_status
        data_to_upsert["preview_cache_version"] = radar.preview_cache_version or QUICKLOOK_PREVIEW_CACHE_VERSION
        data_to_upsert["preview_cache_path"] = radar.preview_cache_path
        data_to_upsert["preview_cache_updated_at"] = radar.preview_cache_updated_at
        data_to_upsert["preview_cache_error"] = radar.preview_cache_error
    if radar is None:
        radar = RadarDataORM(**data_to_upsert)
        db.add(radar)
    else:
        for key, value in data_to_upsert.items():
            setattr(radar, key, value)
    await db.flush()
    return int(radar.id) if radar.id is not None else None


def _metadata_for_radar(scene_manifest: dict[str, Any], standard_manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(scene_manifest.get("metadata") or {})
    metadata.update(
        {
            "native_dir": scene_manifest.get("native_dir"),
            "native_manifest": scene_manifest.get("manifest_path"),
            "standard_manifest": standard_manifest.get("manifest_path"),
            "standard_dir": standard_manifest.get("standard_dir"),
            "standard_assets": standard_manifest.get("assets") or [],
            "analysis_engine": "gf3_sarscape",
        }
    )
    return metadata


async def _upsert_source_product_asset(
    db: AsyncSession,
    scene_manifest: dict[str, Any],
    standard_manifest: dict[str, Any],
) -> int | None:
    standard_dir_text = _path_text(standard_manifest.get("standard_dir"))
    if not standard_dir_text:
        return None
    standard_dir = Path(standard_dir_text)
    metadata = scene_manifest.get("metadata") or {}
    imaging_date = str(metadata.get("imaging_date") or "").strip() or None
    acquisition_start = _date_to_naive_utc(imaging_date)
    scene_name = scene_manifest.get("scene_name") or standard_dir.name
    now = _db_now()
    root = await _find_managed_root_for_path(db, standard_dir_text)
    stats = await asyncio.to_thread(_tree_stats, standard_dir)
    asset_metadata = _json_safe(
        {
            "source": "GF3 SARscape standardized L2",
            "native_dir": scene_manifest.get("native_dir"),
            "native_manifest": scene_manifest.get("manifest_path"),
            "standard_manifest": standard_manifest.get("manifest_path"),
            "standard_dir": standard_dir_text,
            "standard_status": standard_manifest.get("status"),
            "standard_assets": standard_manifest.get("assets") or [],
            "summary": standard_manifest.get("summary") or {},
            "errors": standard_manifest.get("errors") or [],
            "analysis_engine": "gf3_sarscape",
        }
    )
    data = {
        "asset_uid": _source_asset_uid(standard_dir_text),
        "logical_product_uid": scene_name,
        "satellite_family": "GF3",
        "satellite": "GF3",
        "source_format": SOURCE_ASSET_FORMAT,
        "product_type": metadata.get("product_type") or "SARSCAPE_L2",
        "product_level": "L2",
        "imaging_mode": metadata.get("imaging_mode"),
        "polarization": metadata.get("polarization"),
        "absolute_orbit": metadata.get("absolute_orbit") or metadata.get("orbit_circle"),
        "relative_orbit": metadata.get("relative_orbit"),
        "orbit_direction": metadata.get("orbit_direction"),
        "acquisition_start_time_utc": acquisition_start,
        "acquisition_stop_time_utc": None,
        "imaging_date": imaging_date,
        "root_ref_id": root.id if root else None,
        "root_path": root.path if root else str(standard_dir.parent),
        "file_path": standard_dir_text,
        "archive_path": scene_manifest.get("native_dir"),
        "path_kind": _path_kind(standard_dir_text),
        "file_name": standard_dir.name,
        "file_stem": standard_dir.name,
        "file_ext": "",
        "size_bytes": stats.get("size_bytes"),
        "mtime_epoch": stats.get("mtime_epoch"),
        "checksum_status": "NOT_COMPUTED",
        "parser_name": "gf3_sarscape_standard_manifest",
        "parser_version": CONVERTER_VERSION,
        "parse_status": "OK" if standard_manifest.get("status") == "DONE" else str(standard_manifest.get("status") or "PARTIAL"),
        "parse_error": "; ".join(str(item.get("error") or item) for item in (standard_manifest.get("errors") or [])) or None,
        "parsed_at": now,
        "metadata_json": asset_metadata,
        "is_active": True,
        "missing_since": None,
        "updated_at": now,
    }

    result = await db.execute(
        select(SourceProductAssetORM).where(
            or_(
                SourceProductAssetORM.asset_uid == data["asset_uid"],
                SourceProductAssetORM.file_path == standard_dir_text,
            )
        )
    )
    asset = result.scalars().first()
    if asset is None:
        asset = SourceProductAssetORM(**data)
        db.add(asset)
    else:
        for key, value in data.items():
            setattr(asset, key, value)
    await db.flush()
    return int(asset.id) if asset.id is not None else None


async def _upsert_radar_data(
    db: AsyncSession,
    scene_manifest: dict[str, Any],
    standard_manifest: dict[str, Any],
    source_product_ref_id: int | None = None,
) -> int | None:
    assets = standard_manifest.get("assets") or []
    default_asset = _select_default_asset(assets)
    if not default_asset:
        return None

    polygon = _polygon_from_raster(Path(_path_text(default_asset.get("path"))))
    min_lon, min_lat, max_lon, max_lat = _bounds_from_polygon(polygon)
    center_lon, center_lat = _scene_center_from_polygon(polygon)
    geom = _geom_from_polygon(polygon)
    metadata = scene_manifest.get("metadata") or {}
    imaging_date = str(metadata.get("imaging_date") or "").strip() or None
    acquisition_start = _date_to_naive_utc(imaging_date)
    radar_metadata = _metadata_for_radar(scene_manifest, standard_manifest)
    scene_name = scene_manifest.get("scene_name") or Path(str(scene_manifest.get("native_dir") or "")).name
    unique_id = f"gf3_sarscape:{scene_name}"
    file_path = str(standard_manifest.get("standard_dir") or "")

    data_to_upsert = {
        "unique_id": unique_id,
        "satellite": "GF3",
        "satellite_family": "GF3",
        "imaging_date": imaging_date,
        "imaging_mode": metadata.get("imaging_mode"),
        "polarization": ",".join(
            pol
            for pol in POLARIZATION_PRIORITY
            if any(str(asset.get("polarization") or "").upper() == pol for asset in assets)
        )
        or metadata.get("polarization"),
        "scene_center_lon": metadata.get("scene_center_lon") if metadata.get("scene_center_lon") is not None else center_lon,
        "scene_center_lat": metadata.get("scene_center_lat") if metadata.get("scene_center_lat") is not None else center_lat,
        "acquisition_time_utc": acquisition_start.isoformat() if acquisition_start else None,
        "product_level": "L2",
        "product_unique_id": metadata.get("product_unique_id") or scene_name,
        "source_product_token": scene_name,
        "acquisition_start_time_utc": acquisition_start,
        "acquisition_stop_time_utc": None,
        "absolute_orbit": metadata.get("absolute_orbit") or metadata.get("orbit_circle"),
        "relative_orbit": metadata.get("relative_orbit"),
        "source_format": SOURCE_ASSET_FORMAT,
        "source_product_ref_id": source_product_ref_id,
        "image_data_format": "GEOTIFF",
        "geocoded_flag": True,
        "metadata_json": radar_metadata,
        "file_path": file_path,
        "has_orbit_data": False,
        "orbit_file_path": None,
        "is_envi_processed": True,
        "coverage_polygon": polygon,
        "geom": geom,
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
    }

    result = await db.execute(
        select(RadarDataORM).where(
            or_(
                RadarDataORM.unique_id == unique_id,
                RadarDataORM.file_path == file_path,
            )
        )
    )
    radar = result.scalars().first()
    if radar is None:
        radar = RadarDataORM(**data_to_upsert)
        db.add(radar)
    else:
        for key, value in data_to_upsert.items():
            setattr(radar, key, value)
    await db.flush()
    return int(radar.id) if radar.id is not None else None


async def _get_or_create_scene(db: AsyncSession, radar_id: int) -> SARSceneGeoORM:
    result = await db.execute(select(SARSceneGeoORM).where(SARSceneGeoORM.radar_data_id == radar_id))
    scene = result.scalar_one_or_none()
    if scene:
        return scene
    scene = SARSceneGeoORM(radar_data_id=radar_id, status="PENDING")
    db.add(scene)
    await db.flush()
    return scene


async def _register_analysis_ready(
    db: AsyncSession,
    radar_id: int,
    scene_manifest: dict[str, Any],
    standard_manifest: dict[str, Any],
) -> dict[str, Any] | None:
    radar = await db.get(RadarDataORM, radar_id)
    if not radar:
        return None
    assets = standard_manifest.get("assets") or []
    default_asset = _select_default_asset(assets)
    if not default_asset:
        return None
    scene = await _get_or_create_scene(db, radar_id)
    return await register_analysis_ready_tif(
        db=db,
        scene=scene,
        radar=radar,
        source_tif_path=str(default_asset.get("path") or ""),
        engine="gf3_sarscape",
        profile=CONVERTER_NAME,
        backscatter_unit="unknown",
        polarization=str(default_asset.get("polarization") or "").upper() or None,
        preview_source_path=str(default_asset.get("preview") or "") or None,
        metadata={
            "source": "GF3 SARscape native _geo",
            "native_dir": scene_manifest.get("native_dir"),
            "native_manifest": scene_manifest.get("manifest_path"),
            "standard_manifest": standard_manifest.get("manifest_path"),
            "available_polarization": [asset.get("polarization") for asset in assets],
            "standard_assets": assets,
        },
    )


def standardize_scene_manifest(
    scene_manifest: dict[str, Any],
    *,
    storage_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Convert one native scene manifest to GeoTIFF assets."""
    root = Path(storage_root or settings.GF3_STORAGE_DIRS).resolve()
    out_dir = _standard_scene_dir(scene_manifest, root)
    manifest_path = out_dir / STANDARD_MANIFEST_NAME
    existing_manifest = _read_json(manifest_path)

    converted = 0
    skipped = 0
    failed = 0
    output_assets: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for asset in scene_manifest.get("assets") or []:
        if not asset.get("complete"):
            continue
        pol = str(asset.get("polarization") or "UNKNOWN").upper()
        target_tif = _target_tif_path(scene_manifest, asset, root)
        existing_asset = _existing_manifest_asset(existing_manifest, pol)

        try:
            if force or _source_changed(asset, target_tif, existing_asset):
                convert_info = _convert_native_asset_to_tif(asset, target_tif)
                converted += 1
                status = "converted"
            else:
                convert_info = {
                    "path": str(target_tif),
                    "source_native": str(Path(_path_text(asset.get("path")))),
                    "source_fingerprint": _file_fingerprint(Path(_path_text(asset.get("path")))),
                    "converter_name": CONVERTER_NAME,
                    "converter_version": CONVERTER_VERSION,
                }
                skipped += 1
                status = "skipped"

            quality = _raster_quality(target_tif)
            quality_file = _quality_path(scene_manifest, asset, root)
            _write_json(quality_file, quality)
            preview_target = _preview_path(scene_manifest, asset, root)
            quicklook_path = _asset_quicklook_path(asset)
            preview = _build_preview_from_quicklook(quicklook_path, preview_target)
            preview_source = str(quicklook_path) if preview and quicklook_path else str(target_tif)
            if not preview:
                preview = _build_preview_png(target_tif, preview_target)
            output_assets.append(
                {
                    "polarization": pol,
                    "role": "analysis_tif",
                    "path": str(target_tif),
                    "source_native": convert_info["source_native"],
                    "source_fingerprint": convert_info["source_fingerprint"],
                    "converter_name": CONVERTER_NAME,
                    "converter_version": CONVERTER_VERSION,
                    "quality": str(quality_file),
                    "preview": preview,
                    "preview_source": preview_source,
                    "status": status,
                }
            )
        except Exception as exc:
            failed += 1
            errors.append({"polarization": pol, "source_native": str(asset.get("path") or ""), "error": str(exc)})

    status = "DONE" if output_assets and failed == 0 else ("PARTIAL" if output_assets else "FAILED")
    standard_manifest = {
        "schema": STANDARD_MANIFEST_SCHEMA,
        "generated_at": _utc_now(),
        "scene_name": scene_manifest.get("scene_name"),
        "batch_name": scene_manifest.get("batch_name"),
        "native_manifest": scene_manifest.get("manifest_path") or str(Path(scene_manifest.get("native_dir") or "") / NATIVE_MANIFEST_NAME),
        "native_dir": scene_manifest.get("native_dir"),
        "standard_dir": str(out_dir),
        "manifest_path": str(manifest_path),
        "status": status,
        "converter": {"name": CONVERTER_NAME, "version": CONVERTER_VERSION},
        "assets": output_assets,
        "summary": {
            "converted": converted,
            "skipped": skipped,
            "failed": failed,
        },
        "errors": errors,
    }
    _write_json(manifest_path, standard_manifest)
    return standard_manifest


async def standardize_gf3_sarscape_native_roots(
    db: AsyncSession,
    *,
    native_dirs: list[str] | None = None,
    storage_root: str | None = None,
    force: bool = False,
    register: bool = True,
    quicklook_only: bool = False,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    """Scan native roots, convert complete assets, and register standard scenes."""
    requested_native_dirs = [str(item) for item in (native_dirs or []) if str(item or "").strip()]
    if quicklook_only:
        scan_roots = _discover_gf3_sarscape_scan_roots(native_dirs)
        inventory = await asyncio.to_thread(
            scan_gf3_sarscape_native_roots,
            scan_roots,
            write_manifest=True,
        )
    else:
        scan_roots = _discover_gf3_sarscape_scan_roots(native_dirs)
        inventory = await asyncio.to_thread(
            scan_gf3_sarscape_native_roots,
            scan_roots,
            write_manifest=True,
        )
    scenes = inventory.get("scenes") or []
    ready_scenes = [scene for scene in scenes if scene.get("status") in {"NATIVE_READY", "PARTIAL"}]

    if quicklook_only:
        registered = 0
        quicklook_assets = 0
        failed_scenes = 0
        skipped_unchanged = 0
        scene_results: list[dict[str, Any]] = []
        total = len(ready_scenes)
        for idx, scene_manifest in enumerate(ready_scenes):
            if progress_callback:
                pct = 10 + int((idx / max(total, 1)) * 80)
                progress_callback(pct, f"Register GF3 native _geo {idx + 1}/{total}: {scene_manifest.get('scene_name')}")

            quicklook_manifest = await asyncio.to_thread(
                _quicklook_manifest_for_scene,
                scene_manifest,
                storage_root,
            )
            assets = quicklook_manifest.get("assets") or []
            quicklook_assets += len(assets)
            source_asset_id = None
            radar_id = None
            skipped_reason = None
            if register and assets:
                can_skip, existing_radar_id, skip_reason = await _can_skip_unchanged_quicklook_radar(
                    db,
                    scene_manifest=scene_manifest,
                )
                if can_skip:
                    skipped_unchanged += 1
                    radar_id = existing_radar_id
                    skipped_reason = skip_reason
                    if progress_callback:
                        pct = 10 + int(((idx + 1) / max(total, 1)) * 80)
                        progress_callback(pct, f"Skip unchanged GF3 native _geo {idx + 1}/{total}: {scene_manifest.get('scene_name')}")
                else:
                    skipped_reason = skip_reason
                    source_asset_id = await _upsert_quicklook_source_product_asset(db, scene_manifest, quicklook_manifest)
                    radar_id = await _upsert_quicklook_radar_data(
                        db,
                        scene_manifest,
                        quicklook_manifest,
                        source_product_ref_id=source_asset_id,
                    )
                    if radar_id:
                        registered += 1
                    await db.commit()
            if not assets:
                failed_scenes += 1
            scene_results.append(
                {
                    "scene_name": scene_manifest.get("scene_name"),
                    "native_status": scene_manifest.get("status"),
                    "standard_status": quicklook_manifest.get("status"),
                    "native_preview_manifest": quicklook_manifest.get("manifest_path"),
                    "source_asset_id": source_asset_id,
                    "radar_id": radar_id,
                    "skipped_unchanged": bool(skipped_reason == "unchanged"),
                    "skip_reason": skipped_reason,
                    "native_fingerprint": _scene_native_fingerprint(scene_manifest),
                    "summary": quicklook_manifest.get("summary") or {},
                    "errors": quicklook_manifest.get("errors") or [],
                }
            )

        return {
            "ok": failed_scenes == 0,
            "mode": "native_preview",
            "inventory": {
                key: value
                for key, value in inventory.items()
                if key != "scenes"
            },
            "requested_native_dirs": requested_native_dirs,
            "scan_roots": scan_roots,
            "scene_count": len(scenes),
            "ready_scene_count": len(ready_scenes),
            "converted_scenes": 0,
            "partial_scenes": 0,
            "failed_scenes": failed_scenes,
            "converted_assets": 0,
            "skipped_assets": skipped_unchanged,
            "skipped_unchanged": skipped_unchanged,
            "failed_assets": failed_scenes,
            "quicklook_assets": quicklook_assets,
            "native_assets": quicklook_assets,
            "registered": registered,
            "analysis_ready": 0,
            "scenes": scene_results,
        }

    converted_scenes = 0
    partial_scenes = 0
    failed_scenes = 0
    skipped_assets = 0
    converted_assets = 0
    failed_assets = 0
    registered = 0
    analysis_ready = 0
    scene_results: list[dict[str, Any]] = []

    total = len(ready_scenes)
    for idx, scene_manifest in enumerate(ready_scenes):
        if progress_callback:
            pct = 10 + int((idx / max(total, 1)) * 80)
            progress_callback(pct, f"标准化 GF3 SARscape 原生结果 {idx + 1}/{total}: {scene_manifest.get('scene_name')}")

        standard_manifest = await asyncio.to_thread(
            standardize_scene_manifest,
            scene_manifest,
            storage_root=storage_root,
            force=force,
        )
        summary = standard_manifest.get("summary") or {}
        converted_assets += int(summary.get("converted") or 0)
        skipped_assets += int(summary.get("skipped") or 0)
        failed_assets += int(summary.get("failed") or 0)
        status = standard_manifest.get("status")
        if status == "DONE":
            converted_scenes += 1
        elif status == "PARTIAL":
            partial_scenes += 1
        else:
            failed_scenes += 1

        radar_id = None
        source_asset_id = None
        analysis_manifest_path = None
        if register and status in {"DONE", "PARTIAL"}:
            source_asset_id = await _upsert_source_product_asset(db, scene_manifest, standard_manifest)
            radar_id = await _upsert_radar_data(
                db,
                scene_manifest,
                standard_manifest,
                source_product_ref_id=source_asset_id,
            )
            if radar_id:
                registered += 1
                analysis_manifest = await _register_analysis_ready(db, radar_id, scene_manifest, standard_manifest)
                if analysis_manifest:
                    analysis_ready += 1
                    analysis_manifest_path = analysis_manifest.get("analysis_dir")
            await db.commit()

        scene_results.append(
            {
                "scene_name": scene_manifest.get("scene_name"),
                "native_status": scene_manifest.get("status"),
                "standard_status": status,
                "standard_manifest": standard_manifest.get("manifest_path"),
                "source_asset_id": source_asset_id,
                "radar_id": radar_id,
                "analysis_manifest_path": analysis_manifest_path,
                "summary": summary,
                "errors": standard_manifest.get("errors") or [],
            }
        )

    return {
        "ok": failed_scenes == 0 and failed_assets == 0,
        "inventory": {
            key: value
            for key, value in inventory.items()
            if key != "scenes"
        },
        "requested_native_dirs": requested_native_dirs,
        "scan_roots": scan_roots,
        "scene_count": len(scenes),
        "ready_scene_count": len(ready_scenes),
        "converted_scenes": converted_scenes,
        "partial_scenes": partial_scenes,
        "failed_scenes": failed_scenes,
        "converted_assets": converted_assets,
        "skipped_assets": skipped_assets,
        "failed_assets": failed_assets,
        "registered": registered,
        "analysis_ready": analysis_ready,
        "scenes": scene_results,
    }
