"""Analysis-ready SAR GeoTIFF registration for flood/water algorithms.

This service owns the common contract between satellite-specific preprocessing
and downstream flood/water algorithms: one geocoded, single-band GeoTIFF plus
sidecar metadata under SAR_ANALYSIS_READY_ROOT.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import RadarDataORM, SARSceneGeoORM
from ..utils import normalize_satellite_family
from .image_service import image_service

_SAFE_TEXT_RE = re.compile(r"[^0-9A-Za-z._-]+")
_POLARIZATION_PRIORITY = ("HH", "VV", "HV", "VH")


def _safe_slug(value: Any, *, default: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    text = _SAFE_TEXT_RE.sub("_", text).strip("._-")
    return text or default


def _scene_family(radar: RadarDataORM | None) -> str:
    family = normalize_satellite_family(
        getattr(radar, "satellite_family", None) or getattr(radar, "satellite", None)
    )
    return _safe_slug(family or "SAR").upper()


def _scene_date(radar: RadarDataORM | None) -> str:
    text = str(getattr(radar, "imaging_date", None) or "").strip()
    match = re.search(r"(20\d{6})", re.sub(r"\D", "", text))
    if match:
        return match.group(1)
    return "unknown_date"


def _scene_token(
    *,
    radar: RadarDataORM | None,
    scene: SARSceneGeoORM,
    polarization: str | None = None,
) -> str:
    unique = getattr(radar, "unique_id", None) or f"radar_{getattr(radar, 'id', scene.radar_data_id)}"
    parts = [_scene_date(radar), _safe_slug(unique), f"scene_{scene.id}"]
    if polarization:
        parts.append(_safe_slug(polarization).upper())
    return "_".join(parts)


def scene_analysis_dir(
    *,
    radar: RadarDataORM | None,
    scene: SARSceneGeoORM,
    engine: str,
    profile: str,
    polarization: str | None = None,
) -> Path:
    return (
        Path(settings.SAR_ANALYSIS_READY_ROOT)
        / _scene_family(radar)
        / _safe_slug(engine)
        / _safe_slug(profile)
        / _scene_date(radar)
        / _scene_token(radar=radar, scene=scene, polarization=polarization)
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(_json_safe(payload), stream, ensure_ascii=False, indent=2, default=str, allow_nan=False)


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


def _link_or_copy(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        return "same_path"
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        shutil.copy2(source, target)
        return "copy"


def _choose_gf3_l2_tif(l2_dir: str, polarization: str | None = None) -> Path:
    root = Path(os.path.normpath(str(l2_dir or "").strip()))
    if root.is_file():
        return root
    if not root.is_dir():
        raise FileNotFoundError(f"GF3 L2 directory does not exist: {l2_dir}")

    candidates = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".tif", ".tiff"}
        and "L2" in path.name.upper()
    )
    if not candidates:
        raise FileNotFoundError(f"No GF3 L2 GeoTIFF found in: {l2_dir}")

    requested = str(polarization or "").strip().upper()
    if requested:
        for path in candidates:
            if requested in path.name.upper():
                return path

    for pol in _POLARIZATION_PRIORITY:
        for path in candidates:
            if pol in path.name.upper():
                return path
    return candidates[0]


def _infer_polarization_from_path(path: Path) -> str | None:
    upper_name = path.name.upper()
    for pol in _POLARIZATION_PRIORITY:
        if pol in upper_name:
            return pol
    return None


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
        transform = src.transform
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
            "transform": list(transform)[:6],
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


def _pixel_size_m_from_quality(quality: dict[str, Any]) -> float | None:
    try:
        transform = quality.get("transform") or []
        xres = abs(float(transform[0]))
        yres = abs(float(transform[4]))
        crs = str(quality.get("crs") or "").upper()
        if not xres or not yres:
            return None
        if crs and "4326" not in crs:
            return round((xres + yres) / 2.0, 3)
        bounds = quality.get("bounds") or {}
        lat = (float(bounds.get("bottom", 0.0)) + float(bounds.get("top", 0.0))) / 2.0
        meters_per_degree_lon = 111320.0 * max(0.01, math.cos(math.radians(lat)))
        x_m = xres * meters_per_degree_lon
        y_m = yres * 110540.0
        return round((x_m + y_m) / 2.0, 3)
    except Exception:
        return None


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
        data = band.filled(np.nan).astype("float32")

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


def _build_preview_from_existing(source: Path | None, target: Path) -> str | None:
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


async def _get_or_create_scene(db: AsyncSession, radar_id: int) -> SARSceneGeoORM:
    result = await db.execute(select(SARSceneGeoORM).where(SARSceneGeoORM.radar_data_id == radar_id))
    scene = result.scalar_one_or_none()
    if scene:
        return scene
    scene = SARSceneGeoORM(radar_data_id=radar_id, status="PENDING")
    db.add(scene)
    await db.flush()
    return scene


async def register_analysis_ready_tif(
    *,
    db: AsyncSession,
    scene: SARSceneGeoORM,
    radar: RadarDataORM | None,
    source_tif_path: str,
    engine: str,
    profile: str,
    backscatter_unit: str,
    polarization: str | None = None,
    metadata: dict[str, Any] | None = None,
    preview_source_path: str | None = None,
    copy_mode: str = "link_or_copy",
) -> dict[str, Any]:
    source = Path(os.path.normpath(str(source_tif_path or "").strip()))
    if not source.is_file():
        raise FileNotFoundError(f"Analysis-ready source GeoTIFF does not exist: {source}")

    out_dir = scene_analysis_dir(
        radar=radar,
        scene=scene,
        engine=engine,
        profile=profile,
        polarization=polarization,
    )
    target_tif = out_dir / "analysis_ready.tif"
    transfer = "none"
    if copy_mode == "reference":
        target_tif = source
    else:
        transfer = _link_or_copy(source, target_tif)

    quality = _raster_quality(target_tif)
    preview_source = Path(os.path.normpath(preview_source_path)) if preview_source_path else None
    preview_path = _build_preview_from_existing(preview_source, out_dir / "preview.png")
    if not preview_path:
        preview_path = _build_preview_png(target_tif, out_dir / "preview.png")
    manifest = {
        "scene_id": scene.id,
        "radar_data_id": scene.radar_data_id,
        "source_tif_path": str(source),
        "analysis_tif_path": str(target_tif),
        "analysis_dir": str(out_dir),
        "analysis_preview_path": preview_path,
        "engine": engine,
        "profile": profile,
        "backscatter_unit": backscatter_unit,
        "polarization": polarization,
        "transfer": transfer,
        "preview_source_path": str(preview_source) if preview_path and preview_source else None,
        "metadata": metadata or {},
        "quality": quality,
    }
    _write_json(out_dir / "manifest.json", manifest)
    _write_json(out_dir / "quality.json", quality)

    scene.geo_path = str(target_tif)
    scene.analysis_tif_path = str(target_tif)
    scene.analysis_dir = str(out_dir)
    scene.analysis_preview_path = preview_path
    scene.analysis_engine = engine
    scene.analysis_profile = profile
    scene.analysis_backscatter_unit = backscatter_unit
    scene.analysis_nodata_value = _finite_float(quality.get("nodata")) or float(settings.SAR_ANALYSIS_NODATA_VALUE)
    scene.analysis_metadata_json = _json_safe({**(metadata or {}), "manifest_path": str(out_dir / "manifest.json")})
    scene.analysis_quality_json = _json_safe(quality)
    scene.pixel_size_m = _pixel_size_m_from_quality(quality) or scene.pixel_size_m
    scene.status = "DONE"
    scene.error_msg = None

    return manifest


async def standardize_gf3_l2_for_radar(
    *,
    db: AsyncSession,
    radar_id: int,
    l2_path: str | None = None,
    polarization: str | None = None,
) -> dict[str, Any]:
    radar = await db.get(RadarDataORM, int(radar_id))
    if not radar:
        raise ValueError(f"RadarDataORM id={radar_id} does not exist")

    scene = await _get_or_create_scene(db, int(radar_id))
    source_root = l2_path or radar.file_path
    selected_tif = _choose_gf3_l2_tif(source_root, polarization=polarization or radar.polarization)
    selected_pol = polarization or _infer_polarization_from_path(selected_tif)

    manifest = await register_analysis_ready_tif(
        db=db,
        scene=scene,
        radar=radar,
        source_tif_path=str(selected_tif),
        engine="gf3_gdal",
        profile="gf3_l1a_l2_rpc",
        backscatter_unit="sigma0_db",
        polarization=selected_pol,
        metadata={
            "source": "GF3 L2",
            "source_l2_path": str(selected_tif),
            "source_l2_dir": str(Path(source_root).resolve()) if source_root else None,
            "available_polarization": radar.polarization,
        },
    )
    await db.commit()
    return manifest
