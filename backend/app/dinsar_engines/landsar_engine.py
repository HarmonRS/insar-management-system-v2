"""LandSAR D-InSAR engine integration.

This engine wraps LandSAR's console workflow:
    InSAR_Console.exe <Output_Data/200014.txt>

It intentionally does not import the bundled PyQt GUI helper.  Only the
documented proID 200014 parameter-file format is reproduced here so the backend
can run in the service process without desktop dependencies.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import xml.etree.ElementTree as ET

from ..config import get_env_text, read_bool_env, settings
from ..services.dinsar_naming import (
    PAIR_META_FILENAME,
    build_fallback_pair_key,
    write_pair_metadata,
    write_run_metadata,
)
from ..services.dinsar_result_layout_service import normalize_isce2_run_layout
from ..services.isce2_result_validator import validate_isce2_result_files
from ..utils import normalize_satellite_family
from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult


DINSAR_PROID = "200014"
IMPORT_PROID = "100016"
RERUN_MODE_UNFINISHED_ONLY = "unfinished_only"
SUPPORTED_PROFILES = {"lt1_dinsar", "standard"}

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATE_RE = re.compile(r"(?:^|[_-])((?:19|20)\d{6})(?:[_-]|$)")
_CENTER_RE = re.compile(r"(?:^|_)E([+-]?\d+(?:\.\d+)?)_N([+-]?\d+(?:\.\d+)?)(?:_|$)", re.IGNORECASE)
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")
_SUCCESS_RE = re.compile(r"(success|成功)", re.IGNORECASE)
_DEFAULT_DEM_CROP_MARGIN_DEGREES = 0.35

_DEFAULT_PARAM_VALUES: Dict[str, Any] = {
    "dem_file_type": 0,
    "dem_product_type": 0,
    "gcp_file": "",
    "gacos_file": "",
    "do_registration": 1,
    "reg_method": 0,
    "reg_grid_points": 64,
    "reg_window": 128,
    "reg_snr": 7,
    "do_resample": 1,
    "crop_invalid": 1,
    "crop_gcps": 0,
    "do_interferogram": 1,
    "az_looks": 3,
    "rg_looks": 3,
    "gen_intensity": 1,
    "gcp_geometric": 0,
    "gcp_multilook": 0,
    "gen_8bit": 0,
    "do_deflatten": 1,
    "deflat_method": 0,
    "deflat_window": 128,
    "deflat_oversample": 3,
    "do_filter": 1,
    "filter_alpha": 0.6,
    "filter_iterations": 1,
    "do_coherence_mask": 1,
    "coh_mask_threshold": 0.3,
    "do_unwrap": 1,
    "unwrap_method": 0,
    "unwrap_az_blocks": 1,
    "unwrap_rg_blocks": 1,
    "unwrap_coh_threshold": 0.3,
    "do_gcp_extract": 1,
    "gcp_grid_points": 25,
    "gcp_flat_area": 10,
    "gcp_height_diff": 10,
    "gcp_coh_diff": 0.1,
    "do_baseline_refine": 1,
    "baseline_method": 0,
    "phase_correction": 0,
    "baseline_coh_threshold": 0.9,
    "optimize_gcps": 0,
    "gcp_error_threshold": 10,
    "do_diff_fitting": 1,
    "diff_coh_threshold": 0.91,
    "diff_az_samples": 64,
    "diff_rg_samples": 64,
    "do_los_displacement": 1,
    "displacement_format": 0,
    "do_vertical_displacement": 0,
    "do_atmosphere": 0,
    "do_displacement_correction": 0,
    "do_geocoding": 1,
    "geo_wrapped": 1,
    "geo_unwrapped": 1,
    "geo_coherence": 1,
    "geo_los": 1,
    "geo_vertical": 1,
}

_INT_PARAM_KEYS = {
    "dem_file_type",
    "dem_product_type",
    "do_registration",
    "reg_method",
    "reg_grid_points",
    "reg_window",
    "reg_snr",
    "do_resample",
    "crop_invalid",
    "crop_gcps",
    "do_interferogram",
    "az_looks",
    "rg_looks",
    "gen_intensity",
    "gcp_geometric",
    "gcp_multilook",
    "gen_8bit",
    "do_deflatten",
    "deflat_method",
    "deflat_window",
    "deflat_oversample",
    "do_filter",
    "filter_iterations",
    "do_coherence_mask",
    "do_unwrap",
    "unwrap_method",
    "unwrap_az_blocks",
    "unwrap_rg_blocks",
    "do_gcp_extract",
    "gcp_grid_points",
    "gcp_flat_area",
    "gcp_height_diff",
    "do_baseline_refine",
    "baseline_method",
    "phase_correction",
    "optimize_gcps",
    "gcp_error_threshold",
    "do_diff_fitting",
    "diff_az_samples",
    "diff_rg_samples",
    "do_los_displacement",
    "displacement_format",
    "do_vertical_displacement",
    "do_atmosphere",
    "do_displacement_correction",
    "do_geocoding",
    "geo_wrapped",
    "geo_unwrapped",
    "geo_coherence",
    "geo_los",
    "geo_vertical",
}

_FLOAT_PARAM_KEYS = {
    "filter_alpha",
    "coh_mask_threshold",
    "unwrap_coh_threshold",
    "gcp_coh_diff",
    "baseline_coh_threshold",
    "diff_coh_threshold",
}

_PATH_PARAM_KEYS = {"dem_path", "gcp_file", "gacos_file"}
_LANDSAR_REQUIRED_DLLS = [
    "Qt5Core.dll",
    "Qt5Network.dll",
    "Qt5Widgets.dll",
    "libgcc_s_seh-1.dll",
    "libwinpthread-1.dll",
    "libstdc++-6.dll",
    "SAR_ImagePrinterModel.dll",
    "SAR_InSAR_DInSARModel.dll",
    "SAR_InSAR_GeneralModel.dll",
    "SAR_InSAR_GeoInterferometricModel.dll",
    "SAR_InSAR_GeometricModel.dll",
    "SAR_InSAR_InSARModel.dll",
    "SAR_InSAR_IOModel.dll",
    "SAR_InSAR_Model.dll",
    "SAR_InSAR_MTInSARModel.dll",
    "SAR_InSAR_Sequential.dll",
    "SAR_SwapIO.dll",
]
_SYSTEM_EXTRA_KEYS = {
    "__managed_run_dir",
    "__managed_native_output_dir",
    "__managed_work_dir",
    "__managed_export_dir",
    "__managed_orbit_output_dir",
    "__managed_run_key",
    "__source_root_override",
    "__source_task_dir_override",
    "__rerun_mode",
    "__validated_task_count",
    "__validated_mode",
    "__discovered_task_count",
    "__skipped_completed_count",
}


def _read_env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        value = get_env_text(name, default)
    return str(value or default).strip().strip('"').strip("'")


def _norm_path(path: Any) -> str:
    text = str(path or "").strip().strip('"').strip("'")
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text))


def _utc_text(value: Optional[datetime] = None) -> str:
    return (value or datetime.utcnow()).isoformat(timespec="seconds") + "Z"


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return bool(value)


def _read_int_env(name: str, default: int) -> int:
    try:
        return int(_read_env(name, str(default)) or default)
    except (TypeError, ValueError):
        return int(default)


def _tcp_connect(host: str, port: int, timeout: float = 1.0) -> bool:
    if not host or int(port or 0) <= 0:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _decode_line(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return raw.decode(encoding, errors="strict").rstrip("\r\n")
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def _extract_date(name: str) -> str:
    match = _DATE_RE.search(name or "")
    if match:
        return match.group(1)
    fallback = re.search(r"((?:19|20)\d{6})", name or "")
    return fallback.group(1) if fallback else ""


def _safe_path_name(value: Any, fallback: str = "item") -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip()).strip("._-")
    return text or fallback


def _extract_center_from_name(name: str) -> Optional[tuple[float, float]]:
    match = _CENTER_RE.search(name or "")
    if not match:
        return None
    try:
        return float(match.group(1)), float(match.group(2))
    except (TypeError, ValueError):
        return None


def _extract_center_from_path(path: str) -> Optional[tuple[float, float]]:
    source = _norm_path(path)
    if not source:
        return None
    candidates = [os.path.basename(source), os.path.basename(os.path.dirname(source))]
    for candidate in candidates:
        center = _extract_center_from_name(candidate)
        if center:
            return center
    return None


def _collect_tail(text: str, max_chars: int = 4000) -> str:
    content = str(text or "")
    if len(content) <= max_chars:
        return content
    return content[-max_chars:]


def _summarize_landsar_failure(stdout_text: str, stage: str, return_code: int) -> str:
    content = str(stdout_text or "")
    lowered = content.lower()
    prefix = f"{stage} returned {return_code}"
    if "connect server failed" in lowered or "load_server_memory_2_dongle failed" in lowered:
        return f"{prefix}: LandSAR network license server is unreachable or rejected the request."
    license_failure_markers = [
        "hasp_login failed",
        "dongle_read failed",
        "read_memory(from server) failed",
        "buff_parsing failed",
    ]
    if any(marker in lowered for marker in license_failure_markers):
        match = re.search(r"HASP_STATUS\s*==\s*([0-9]+)", content, re.IGNORECASE)
        status_text = f" (HASP_STATUS == {match.group(1)})" if match else ""
        return f"{prefix}: LandSAR license dongle login failed{status_text}."
    if "cann't find 'config.csv'" in lowered or "can't find 'config.csv'" in lowered:
        return f"{prefix}: LandSAR config.csv is missing. Run versionControl.exe once from LANDSAR_HOME."
    if "cannot operate regis module" in lowered or "invalid parameters" in lowered:
        return (
            f"{prefix}: registration module failed. "
            "LandSAR could not obtain a valid coregistration solution for this pair."
        )
    if "invalid data type" in lowered or "access window out of range" in lowered or "subterrain phase failed" in lowered:
        return f"{prefix}: DEM/sub-terrain processing failed. Check DEM coverage, data type, and LandSAR-readable format."
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    error_lines = [
        line
        for line in lines
        if (
            re.search(r"\berror\b", line, re.IGNORECASE)
            or re.search(r"\bfailed\b", line, re.IGNORECASE)
            or "CurProModule:" in line
        )
        and "dongle.info:" not in line
        and line.lower() != "console failed."
    ]
    if error_lines:
        return f"{prefix}: {' | '.join(error_lines[-5:])}"
    if lines:
        return f"{prefix}: {' | '.join(lines[-4:])}"
    return f"{prefix}."


def _find_first_existing(*paths: Path) -> str:
    for path in paths:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return ""


def _split_path_env(value: str) -> List[str]:
    return [part.strip() for part in str(value or "").split(os.pathsep) if part.strip()]


def _path_search_dirs(*leading_dirs: str) -> List[str]:
    seen = set()
    configured_dirs = [
        *_split_path_env(_read_env("LANDSAR_RUNTIME_PATHS", "")),
        *_split_path_env(_read_env("LANDSAR_DLL_DIRS", "")),
    ]
    result: List[str] = []
    for directory in list(leading_dirs) + configured_dirs + [
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64"),
        os.environ.get("SystemRoot", r"C:\Windows"),
        *os.environ.get("PATH", "").split(os.pathsep),
    ]:
        normalized = _norm_path(directory)
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        result.append(normalized)
    return result


def _find_dll(name: str, search_dirs: List[str]) -> str:
    for directory in search_dirs:
        candidate = os.path.join(directory, name)
        try:
            if os.path.isfile(candidate):
                return _norm_path(candidate)
        except OSError:
            continue
    return ""


def _check_required_dlls(console_path: str, home: str) -> Dict[str, Any]:
    console_dir = os.path.dirname(_norm_path(console_path))
    search_dirs = _path_search_dirs(console_dir, home)
    missing: List[str] = []
    found: Dict[str, str] = {}
    for name in _LANDSAR_REQUIRED_DLLS:
        resolved = _find_dll(name, search_dirs)
        if resolved:
            found[name] = resolved
        else:
            missing.append(name)
    return {
        "ok": not missing,
        "missing": missing,
        "found": found,
        "search_dirs": search_dirs,
    }


def _landsar_process_env(console_path: str, home: str) -> Dict[str, str]:
    env = dict(os.environ)
    leading_dirs = _path_search_dirs(os.path.dirname(_norm_path(console_path)), home)
    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(leading_dirs + ([existing_path] if existing_path else []))
    return env


def _copy_file(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def _has_lt1_source_data(directory: str) -> bool:
    source_dir = _norm_path(directory)
    if not os.path.isdir(source_dir):
        return False
    for entry in os.scandir(source_dir):
        if not entry.is_file():
            continue
        lower_name = entry.name.lower()
        if lower_name.startswith("lt1") and lower_name.endswith((".xml", ".tif", ".tiff")):
            return True
    return False


def _looks_like_raw_task_dir(task_dir: str) -> bool:
    normalized = _norm_path(task_dir)
    master_dir = os.path.join(normalized, "master")
    slave_dir = os.path.join(normalized, "slave")
    return (
        os.path.isdir(master_dir)
        and os.path.isdir(slave_dir)
        and _has_lt1_source_data(master_dir)
        and _has_lt1_source_data(slave_dir)
    )


def _find_lt1_scene_token(directory: str) -> str:
    source_dir = _norm_path(directory)
    if not os.path.isdir(source_dir):
        return ""
    for entry in os.scandir(source_dir):
        if entry.is_file():
            match = re.match(r"^(LT1[AB])_", entry.name, re.IGNORECASE)
            if match:
                return match.group(1).upper()
    return ""


def _infer_lt1_import_sat_mode(master_dir: str, slave_dir: str) -> str:
    master_sat = _find_lt1_scene_token(master_dir)
    slave_sat = _find_lt1_scene_token(slave_dir)
    if master_sat and slave_sat and master_sat != slave_sat:
        return "BIST"
    return "MONO"


def _read_dem_bounds(dem_path: str) -> Optional[tuple[float, float, float, float]]:
    path = _norm_path(dem_path)
    if not path or not os.path.isfile(path):
        return None
    try:
        import rasterio  # type: ignore

        with rasterio.open(path) as dataset:
            bounds = dataset.bounds
            return float(bounds.left), float(bounds.bottom), float(bounds.right), float(bounds.top)
    except Exception:
        return None


def _scene_centers_outside_dem(
    parsed_pair: Dict[str, Any],
    dem_path: str,
    *,
    margin_degrees: float = 0.25,
) -> List[str]:
    bounds = _read_dem_bounds(dem_path)
    if not bounds:
        return []
    left, bottom, right, top = bounds
    blockers: List[str] = []
    for role, key in (("master", "master_xml"), ("slave", "slave_xml")):
        source_path = str(parsed_pair.get(key) or "")
        center = _extract_center_from_path(source_path)
        if not center:
            continue
        lon, lat = center
        if lon < left + margin_degrees or lon > right - margin_degrees or lat < bottom + margin_degrees or lat > top - margin_degrees:
            blockers.append(
                f"{role} center E{lon:.3f}/N{lat:.3f} is outside or too close to DEM bounds "
                f"E{left:.3f}-{right:.3f}, N{bottom:.3f}-{top:.3f}"
            )
    return blockers


def _xml_local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _xml_float_text(value: Any) -> Optional[float]:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _xml_child_float_by_local_names(element: ET.Element, names: Iterable[str]) -> Optional[float]:
    wanted = {str(name).lower() for name in names}
    for child in element.iter():
        if child is element:
            continue
        if _xml_local_name(child.tag) in wanted:
            value = _xml_float_text(child.text)
            if value is not None:
                return value
    return None


def _extract_scene_lonlat_points_from_xml(xml_path: str) -> List[tuple[float, float]]:
    source = _norm_path(xml_path)
    if not source or not os.path.isfile(source):
        return []
    try:
        root = ET.parse(source).getroot()
    except Exception:
        return []

    points: List[tuple[float, float]] = []
    for element in root.iter():
        if _xml_local_name(element.tag) != "scenecornercoord":
            continue
        lon = _xml_child_float_by_local_names(element, ("lon", "longitude"))
        lat = _xml_child_float_by_local_names(element, ("lat", "latitude"))
        if lon is not None and lat is not None:
            points.append((lon, lat))
    if points:
        return points

    for element in root.iter():
        if _xml_local_name(element.tag) != "scenecentercoord":
            continue
        lon = _xml_child_float_by_local_names(element, ("lon", "longitude"))
        lat = _xml_child_float_by_local_names(element, ("lat", "latitude"))
        if lon is not None and lat is not None:
            return [(lon, lat)]
    return []


def _bbox_from_points(points: Iterable[tuple[float, float]]) -> Optional[tuple[float, float, float, float]]:
    cleaned = [
        (float(lon), float(lat))
        for lon, lat in points
        if math.isfinite(float(lon)) and math.isfinite(float(lat))
    ]
    if not cleaned:
        return None
    lons = [item[0] for item in cleaned]
    lats = [item[1] for item in cleaned]
    return min(lons), min(lats), max(lons), max(lats)


def _expand_lonlat_bbox(
    bbox: tuple[float, float, float, float],
    margin_degrees: float,
) -> tuple[float, float, float, float]:
    margin = max(0.0, float(margin_degrees or 0.0))
    left, bottom, right, top = bbox
    return (
        max(-180.0, float(left) - margin),
        max(-90.0, float(bottom) - margin),
        min(180.0, float(right) + margin),
        min(90.0, float(top) + margin),
    )


def derive_landsar_dem_bbox_from_xml_paths(
    xml_paths: Iterable[str],
    *,
    fallback_paths: Iterable[str] = (),
) -> Optional[tuple[float, float, float, float]]:
    points: List[tuple[float, float]] = []
    for xml_path in xml_paths:
        points.extend(_extract_scene_lonlat_points_from_xml(str(xml_path or "")))
    bbox = _bbox_from_points(points)
    if bbox:
        return bbox

    fallback_points: List[tuple[float, float]] = []
    for path in fallback_paths:
        center = _extract_center_from_path(str(path or ""))
        if center:
            fallback_points.append(center)
    return _bbox_from_points(fallback_points)


def derive_landsar_pair_dem_bbox(parsed_pair: Dict[str, Any]) -> Optional[tuple[float, float, float, float]]:
    return derive_landsar_dem_bbox_from_xml_paths(
        [
            str(parsed_pair.get("master_xml") or ""),
            str(parsed_pair.get("slave_xml") or ""),
        ],
        fallback_paths=[
            str(parsed_pair.get("master_xml") or parsed_pair.get("master_tif") or ""),
            str(parsed_pair.get("slave_xml") or parsed_pair.get("slave_tif") or ""),
        ],
    )


def _align_raster_window(window: Any, width: int, height: int) -> Any:
    from rasterio.windows import Window

    col_off = max(0, int(math.floor(window.col_off)))
    row_off = max(0, int(math.floor(window.row_off)))
    col_stop = min(width, int(math.ceil(window.col_off + window.width)))
    row_stop = min(height, int(math.ceil(window.row_off + window.height)))
    if col_stop <= col_off or row_stop <= row_off:
        raise ValueError("DEM crop bbox does not overlap source DEM")
    return Window(col_off, row_off, col_stop - col_off, row_stop - row_off)


def _iter_raster_windows(width: int, height: int, block_size: int = 2048) -> Iterable[Any]:
    from rasterio.windows import Window

    step = max(256, int(block_size or 2048))
    for row in range(0, int(height), step):
        h = min(step, int(height) - row)
        for col in range(0, int(width), step):
            w = min(step, int(width) - col)
            yield Window(col, row, w, h)


def _format_bbox_key(bbox: tuple[float, float, float, float]) -> str:
    return ",".join(f"{value:.8f}" for value in bbox)


def prepare_landsar_dem_crop(
    source_dem_path: str,
    crop_root: str,
    bbox: tuple[float, float, float, float],
    *,
    label: str = "task",
    margin_degrees: float = _DEFAULT_DEM_CROP_MARGIN_DEGREES,
    block_size: int = 2048,
) -> Dict[str, Any]:
    source = _norm_path(source_dem_path)
    if not source or not os.path.isfile(source):
        raise FileNotFoundError(f"LandSAR DEM source file is missing: {source or '<empty>'}")
    if not bbox:
        raise ValueError("LandSAR DEM crop bbox is empty")

    expanded_bbox = _expand_lonlat_bbox(bbox, margin_degrees)
    crop_dir = Path(_norm_path(crop_root))
    crop_dir.mkdir(parents=True, exist_ok=True)
    safe_label = _safe_path_name(label, "task")
    key = hashlib.sha1(f"{source}|{_format_bbox_key(expanded_bbox)}".encode("utf-8", errors="ignore")).hexdigest()[:16]
    target = crop_dir / f"{safe_label}_{key}_dem.tif"
    manifest = target.with_suffix(target.suffix + ".json")

    if target.is_file() and manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        return {
            "source_dem_path": source,
            "dem_path": str(target),
            "bbox": list(bbox),
            "expanded_bbox": list(expanded_bbox),
            "reused": True,
            "manifest_path": str(manifest),
            **({"bounds": payload.get("bounds")} if payload.get("bounds") else {}),
        }

    try:
        import rasterio  # type: ignore
        from rasterio.transform import array_bounds
        from rasterio.windows import Window, from_bounds
    except Exception as exc:
        raise RuntimeError("rasterio is required to crop LandSAR DEMs") from exc

    temp_path: Optional[Path] = None
    with rasterio.open(source) as src:
        source_dtype = str(src.dtypes[0]).lower()
        if source_dtype != "int16":
            raise ValueError(f"LandSAR DEM source must be a prepared Int16 GeoTIFF, got dtype={src.dtypes[0]}: {source}")
        bounds = src.bounds
        left, bottom, right, top = expanded_bbox
        if left < bounds.left or right > bounds.right or bottom < bounds.bottom or top > bounds.top:
            raise ValueError(
                "LandSAR DEM crop bbox is outside source DEM bounds: "
                f"bbox=E{left:.6f}-{right:.6f},N{bottom:.6f}-{top:.6f}; "
                f"source=E{bounds.left:.6f}-{bounds.right:.6f},N{bounds.bottom:.6f}-{bounds.top:.6f}"
            )

        source_window = _align_raster_window(from_bounds(*expanded_bbox, transform=src.transform), src.width, src.height)
        source_window = Window(
            int(source_window.col_off),
            int(source_window.row_off),
            int(source_window.width),
            int(source_window.height),
        )
        transform = src.window_transform(source_window)
        crop_bounds = array_bounds(int(source_window.height), int(source_window.width), transform)

        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            height=int(source_window.height),
            width=int(source_window.width),
            count=1,
            dtype=src.dtypes[0],
            crs=src.crs,
            transform=transform,
            nodata=src.nodata,
            compress="NONE",
            BIGTIFF="YES",
            interleave="band",
        )
        profile.pop("photometric", None)
        profile.pop("predictor", None)
        if int(source_window.width) >= 512 and int(source_window.height) >= 512:
            profile.update(tiled=True, blockxsize=512, blockysize=512)
        else:
            profile.pop("blockxsize", None)
            profile.pop("blockysize", None)
            profile["tiled"] = False

        try:
            with tempfile.NamedTemporaryFile(
                prefix=f"{target.stem}.",
                suffix=".tmp.tif",
                dir=str(crop_dir),
                delete=False,
            ) as tmp:
                temp_path = Path(tmp.name)

            with rasterio.open(temp_path, "w", **profile) as dst:
                for rel_window in _iter_raster_windows(int(source_window.width), int(source_window.height), block_size):
                    src_window = Window(
                        source_window.col_off + rel_window.col_off,
                        source_window.row_off + rel_window.row_off,
                        rel_window.width,
                        rel_window.height,
                    )
                    dst.write(src.read(1, window=src_window, masked=False), 1, window=rel_window)
            os.replace(temp_path, target)
            temp_path = None
            payload = {
                "source_dem_path": source,
                "dem_path": str(target),
                "bbox": list(bbox),
                "expanded_bbox": list(expanded_bbox),
                "bounds": [float(value) for value in crop_bounds],
                "width": int(source_window.width),
                "height": int(source_window.height),
                "dtype": src.dtypes[0],
                "nodata": src.nodata,
                "margin_degrees": float(margin_degrees),
            }
            manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    return {
        "source_dem_path": source,
        "dem_path": str(target),
        "bbox": list(bbox),
        "expanded_bbox": list(expanded_bbox),
        "bounds": [float(value) for value in crop_bounds],
        "reused": False,
        "manifest_path": str(manifest),
    }


def parse_lt1_slc_pair(input_data_dir: str) -> Optional[Dict[str, Any]]:
    """Return the first chronological LT-1 SLC pair from Input_Data."""

    input_dir = _norm_path(input_data_dir)
    if not os.path.isdir(input_dir):
        return None

    by_date: Dict[str, Dict[str, str]] = {}
    for entry in os.scandir(input_dir):
        if not entry.is_file():
            continue
        lower_name = entry.name.lower()
        if not lower_name.endswith((".xml", ".tif", ".tiff")):
            continue
        if lower_name.endswith(".meta.xml") or lower_name.endswith("_check.xml"):
            continue
        if lower_name.endswith(".xml") and "_slc" not in lower_name:
            continue
        date_text = _extract_date(entry.name)
        if not date_text:
            continue
        payload = by_date.setdefault(date_text, {})
        if lower_name.endswith(".xml") and "xml" not in payload:
            payload["xml"] = entry.path
        elif lower_name.endswith((".tif", ".tiff")) and "tif" not in payload:
            payload["tif"] = entry.path

    scenes = [
        {"date": date_text, **paths}
        for date_text, paths in sorted(by_date.items())
        if paths.get("xml") and paths.get("tif")
    ]
    if len(scenes) < 2:
        return None

    master = scenes[0]
    slave = scenes[1]
    return {
        "master_date": master["date"],
        "slave_date": slave["date"],
        "master_xml": _norm_path(master["xml"]),
        "master_tif": _norm_path(master["tif"]),
        "slave_xml": _norm_path(slave["xml"]),
        "slave_tif": _norm_path(slave["tif"]),
    }


def _generate_import_param_file(
    filepath: str,
    *,
    master_dir: str,
    slave_dir: str,
    export_dir: str,
    import_method: str = "dir",
    sat_mode: str = "BIST",
    read_xml: bool = True,
    read_slc: bool = True,
    export_to_new: bool = True,
    master_xml: str = "",
    master_slc: str = "",
    master_rpb: str = "",
    slave_xml: str = "",
    slave_slc: str = "",
    slave_rpb: str = "",
) -> str:
    is_dir_import = import_method == "dir"
    lines = [
        "卫星数据导入LT-1",
        f"处理       {IMPORT_PROID}",
        f"设置数据导入形式_0文件夹导入_1数据导入  {'文件夹导入' if is_dir_import else '数据导入'}",
        f"读取成像参数文件_0否_1是 {'1' if read_xml else '0'}",
        f"读取SLC数据文件_0否_1是 {'1' if read_slc else '0'}",
        f"文件夹导入标识  {'TRUE' if is_dir_import else 'FALSE'}",
        "文件夹导入个数  2",
        f"文件夹1路径  <{master_dir}>",
        f"文件夹2路径  <{slave_dir}>",
        f"数据导入  {'FALSE' if is_dir_import else 'TRUE'}",
        f"输入卫星数据格式  {sat_mode}",
        f"输入主影像成像参数文件路径  <{master_xml}>",
        f"输入主影像SLC数据文件路径  <{master_slc}>",
        f"输入主影像RPB数据文件路径  <{master_rpb}>",
        f"输入辅影像成像参数文件路径  <{slave_xml}>",
        f"输入辅影像SLC数据文件路径  <{slave_slc}>",
        f"输入辅影像RPB数据文件路径  <{slave_rpb}>",
        f"设置数据导出目标路径_0原目录_1新目录  {'1' if export_to_new else '0'}",
        f"设置输出文件目录  <{export_dir}>",
    ]
    target = _norm_path(filepath)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return target


def _generate_dinsar_param_file(
    filepath: str,
    *,
    master_xml: str,
    master_tif: str,
    slave_xml: str,
    slave_tif: str,
    dem_path: str,
    output_dir: str,
    params: Dict[str, Any],
) -> str:
    def p(key: str, default: Any) -> Any:
        return params.get(key, default)

    lines = [
        "DInSARProcess流程化处理",
        f"ID\t                {DINSAR_PROID}",
        "",
        "输入输出数据设置",
        f"输入主影像XML文件路径\t    <{master_xml}>",
        f"输入主影像SLC数据路径\t    <{master_tif}>",
        f"输入辅影像XML文件路径\t    <{slave_xml}>",
        f"输入辅影像SLC数据路径\t    <{slave_tif}>",
        f"输入外部参考DEM文件类型_0文件_1目录\t    {p('dem_file_type', 0)}",
        f"输入外部参考DEM产品类型_0STRM_1TanDEM\t    {p('dem_product_type', 0)}",
        f"输入外部参考DEM路径或目录\t    <{dem_path}>",
        f"输入控制点文件              <{p('gcp_file', '')}>",
        f"输入外部GACOS大气相位数据            <{p('gacos_file', '')}>",
        f"输出差分干涉处理结果目录        <{output_dir}>",
        "",
        f"是否处理干涉对配准模块   {p('do_registration', 1)}",
        f"配准方法\t                 {p('reg_method', 0)}",
        f"规则格网点数\t                {p('reg_grid_points', 64)}",
        f"配准窗口\t               {p('reg_window', 128)}",
        f"信噪比\t                 {p('reg_snr', 7)}",
        "",
        f"是否处理辅影像重采样模块    {p('do_resample', 1)}",
        f"裁剪无效区域标识\t                 {p('crop_invalid', 1)}",
        f"裁剪主影像对应的GCPs文件\t                 {p('crop_gcps', 0)}",
        "",
        f"是否处理干涉条纹图模块    {p('do_interferogram', 1)}",
        f"方位向多视\t  {p('az_looks', 3)}",
        f"距离向多视\t  {p('rg_looks', 3)}",
        f"是否生成多视强度影像(0表示不生成_1表示生成)  {p('gen_intensity', 1)}",
        f"是否利用控制点进行几何纠正(0表示不纠正_1表示纠正)  {p('gcp_geometric', 0)}",
        f"是否对控制点数据进行多视(0表示不多视_1表示多视)  {p('gcp_multilook', 0)}",
        f"是否生成生成8bit灰度影像(0表示不生成_1表示生成)  {p('gen_8bit', 0)}",
        "",
        f"是否处理去除平地地形相位模块    {p('do_deflatten', 1)}",
        f"去除相位方法标识  {p('deflat_method', 0)}",
        f"精配准窗口大小  {p('deflat_window', 128)}",
        f"采样倍数  {p('deflat_oversample', 3)}",
        "",
        f"是否处理滤波干涉条纹图模块    {p('do_filter', 1)}",
        f"滤波因子  {p('filter_alpha', 0.6)}",
        f"滤波次数  {p('filter_iterations', 1)}",
        "",
        f"是否处理相干性掩膜模块    {p('do_coherence_mask', 1)}",
        f"相干性掩膜阈值  {p('coh_mask_threshold', 0.3)}",
        "",
        f"是否处理相位解缠模块    {p('do_unwrap', 1)}",
        f"解缠方法       {p('unwrap_method', 0)}",
        f"方位向分块     {p('unwrap_az_blocks', 1)}",
        f"距离向分块     {p('unwrap_rg_blocks', 1)}",
        f"相干性阈值     {p('unwrap_coh_threshold', 0.3)}",
        "",
        f"是否处理GCP提取模块    {p('do_gcp_extract', 1)}",
        f"规则格网点数     {p('gcp_grid_points', 25)}",
        f"在窗口内筛选相对平坦区域     {p('gcp_flat_area', 10)}",
        f"窗口内高差设置     {p('gcp_height_diff', 10)}",
        f"窗口内相干性差异设置     {p('gcp_coh_diff', 0.1)}",
        "",
        f"是否处理基线精估计模块    {p('do_baseline_refine', 1)}",
        f"基线精估计方法       {p('baseline_method', 0)}",
        f"相位校正标识       {p('phase_correction', 0)}",
        f"基线精估计相干性阈值     {p('baseline_coh_threshold', 0.9)}",
        f"优选控制点标识       {p('optimize_gcps', 0)}",
        f"优选控制点误差阈值       {p('gcp_error_threshold', 10)}",
        "",
        f"是否处理差分干涉相位拟合模块   {p('do_diff_fitting', 1)}",
        f"相干阈值 \t\t\t\t{p('diff_coh_threshold', 0.91)}",
        f"方位向采样点数             {p('diff_az_samples', 64)}",
        f"距离向采样点数\t           {p('diff_rg_samples', 64)}",
        "",
        f"是否处理相位转LOS向形变模块   {p('do_los_displacement', 1)}",
        f"形变图产品形式   {p('displacement_format', 0)}",
        "",
        f"是否处理LOS向形变转垂直向形变模块     {p('do_vertical_displacement', 0)}",
        "",
        f"是否处理大气相位改正模块    {p('do_atmosphere', 0)}",
        "",
        f"是否处理形变结果校正模块   {p('do_displacement_correction', 0)}",
        "",
        f"是否处理地理编码模块   {p('do_geocoding', 1)}",
        f"是否编码滤波后缠绕数据(差分干涉相位拟合)   {p('geo_wrapped', 1)}",
        f"是否编码滤波后解缠数据(差分干涉相位拟合)   {p('geo_unwrapped', 1)}",
        f"是否编码滤波后相干系数数据   {p('geo_coherence', 1)}",
        f"是否编码视线向形变场数据   {p('geo_los', 1)}",
        f"是否编码垂直向形变场数据   {p('geo_vertical', 1)}",
    ]

    target = _norm_path(filepath)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return target


class LandsarEngine(DinsarEngine):
    @property
    def engine_code(self) -> str:
        return "landsar"

    @property
    def engine_label(self) -> str:
        return "LandSAR"

    @property
    def default_timeout_seconds(self) -> int:
        return max(60, int(getattr(settings, "LANDSAR_DINSAR_TIMEOUT_SECONDS", 0) or _read_env("LANDSAR_DINSAR_TIMEOUT_SECONDS", "43200") or 43200))

    @property
    def _enabled(self) -> bool:
        return read_bool_env("LANDSAR_ENABLED", True)

    @property
    def _default_home(self) -> str:
        return str(_PROJECT_ROOT / "third_party" / "LandSAR" / "dist" / "LandSAR_Portable")

    @property
    def _home(self) -> str:
        return _norm_path(_read_env("LANDSAR_HOME", self._default_home))

    @property
    def _console_exe(self) -> str:
        explicit = _read_env("LANDSAR_CONSOLE_EXE", "")
        if explicit:
            return _norm_path(explicit)
        home = self._home
        candidates = [
            Path(home) / "InSAR_Console.exe",
            _PROJECT_ROOT / "third_party" / "LandSAR" / "dist" / "LandSAR_Portable" / "InSAR_Console.exe",
            _PROJECT_ROOT / "third_party" / "LandSAR" / "dist" / "InSAR_Console.exe",
        ]
        found = _find_first_existing(*candidates)
        return _norm_path(found or str(candidates[0]))

    @property
    def _config_csv(self) -> str:
        return _norm_path(os.path.join(self._home, "config", "config.csv"))

    @property
    def _version_control_exe(self) -> str:
        return _norm_path(os.path.join(self._home, "versionControl.exe"))

    @property
    def _license_mode(self) -> str:
        return _read_env("LANDSAR_LICENSE_MODE", str(getattr(settings, "LANDSAR_LICENSE_MODE", "netVersion") or "netVersion"))

    @property
    def _license_host(self) -> str:
        return _read_env("LANDSAR_LICENSE_HOST", str(getattr(settings, "LANDSAR_LICENSE_HOST", "127.0.0.1") or "127.0.0.1"))

    @property
    def _license_port(self) -> int:
        return _read_int_env("LANDSAR_LICENSE_PORT", int(getattr(settings, "LANDSAR_LICENSE_PORT", 6666) or 6666))

    @property
    def _config_auto_write(self) -> bool:
        return read_bool_env("LANDSAR_CONFIG_AUTO_WRITE", bool(getattr(settings, "LANDSAR_CONFIG_AUTO_WRITE", True)))

    @property
    def _auth_server_exe(self) -> str:
        return _norm_path(_read_env("LANDSAR_AUTH_SERVER_EXE", str(getattr(settings, "LANDSAR_AUTH_SERVER_EXE", "") or "")))

    @property
    def _auth_server_auto_start(self) -> bool:
        return read_bool_env("LANDSAR_AUTH_SERVER_AUTO_START", bool(getattr(settings, "LANDSAR_AUTH_SERVER_AUTO_START", True)))

    @property
    def _auth_server_host(self) -> str:
        return _read_env("LANDSAR_AUTH_SERVER_HOST", str(getattr(settings, "LANDSAR_AUTH_SERVER_HOST", "127.0.0.1") or "127.0.0.1"))

    @property
    def _auth_server_port(self) -> int:
        return _read_int_env("LANDSAR_AUTH_SERVER_PORT", int(getattr(settings, "LANDSAR_AUTH_SERVER_PORT", 6666) or 6666))

    @property
    def _expected_config_row(self) -> str:
        explicit = _read_env("LANDSAR_CONFIG_ROW", str(getattr(settings, "LANDSAR_CONFIG_ROW", "") or ""))
        if explicit:
            return explicit
        mode = self._license_mode or "netVersion"
        host = self._license_host or "127.0.0.1"
        port = self._license_port
        return f"{mode},zh,{host},{port}"

    def _expected_config_parts(self) -> List[str]:
        return [part.strip() for part in self._expected_config_row.split(",")]

    def _expected_config_mode(self) -> str:
        parts = self._expected_config_parts()
        return parts[0] if parts else self._license_mode

    def _expected_config_host(self) -> str:
        parts = self._expected_config_parts()
        if len(parts) >= 3 and parts[2]:
            return parts[2]
        return self._license_host or "127.0.0.1"

    def _expected_config_port(self) -> int:
        parts = self._expected_config_parts()
        if len(parts) >= 4:
            try:
                return int(parts[3])
            except (TypeError, ValueError):
                pass
        return int(self._license_port or 6666)

    @property
    def _default_dem_path(self) -> str:
        landsar_dem = _read_env("LANDSAR_DEM_PATH", "")
        if landsar_dem:
            return _norm_path(landsar_dem)
        pyint_dem = _read_env("PYINT_PREPARED_DEM_PATH", "")
        if pyint_dem:
            return _norm_path(pyint_dem)
        isce2_dem = _read_env("ISCE2_DEM_PATH", "")
        if isce2_dem:
            return _norm_path(isce2_dem)
        return _norm_path(getattr(settings, "IDL_DINSAR_DEM_BASE_FILE", "") or "")

    def _read_config_rows(self) -> List[str]:
        path = self._config_csv
        try:
            with open(path, "r", encoding="utf-8-sig", errors="ignore") as fp:
                return [line.strip() for line in fp.read().splitlines() if line.strip()]
        except OSError:
            return []

    def _config_matches_expected(self) -> bool:
        rows = self._read_config_rows()
        if len(rows) < 2:
            return False
        return rows[1].strip().lower() == self._expected_config_row.lower()

    def _ensure_config_csv(self) -> tuple[bool, str]:
        path = self._config_csv
        if self._config_matches_expected():
            return True, f"ok: {path}"
        if not self._config_auto_write:
            if os.path.isfile(path):
                return False, f"LandSAR config.csv is not set to expected license row: {path}"
            return False, f"LandSAR config.csv is missing: {path}"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fp:
                fp.write("version,language,Address,port\n")
                fp.write(self._expected_config_row + "\n")
            return True, f"wrote: {path}"
        except OSError as exc:
            return False, f"failed to write {path}: {exc}"

    def _is_network_license_mode(self) -> bool:
        return self._expected_config_mode().strip().lower() == "netversion"

    def _start_auth_server_if_needed(self) -> tuple[bool, str]:
        if not self._is_network_license_mode():
            return True, "not required for standalone license mode"

        client_host = self._expected_config_host()
        client_port = int(self._expected_config_port())
        bind_host = self._auth_server_host or client_host
        bind_port = int(self._auth_server_port or client_port)
        if _tcp_connect(client_host, client_port):
            return True, f"listening on {client_host}:{client_port}"

        exe = self._auth_server_exe
        if not self._auth_server_auto_start:
            return False, f"not listening on {client_host}:{client_port}; auto-start disabled"
        if not exe or not os.path.isfile(exe):
            return False, f"auth server executable missing: {exe or '<empty>'}"

        server_dir = os.path.dirname(exe)
        memory_bin = os.path.join(server_dir, "dongle_0xa0.bin")
        if not os.path.isfile(memory_bin):
            fallback_bin = str(_PROJECT_ROOT / "third_party" / "LandSAR" / "tools" / "dongle_0xa0.bin")
            if os.path.isfile(fallback_bin):
                try:
                    shutil.copy2(fallback_bin, memory_bin)
                except OSError as exc:
                    return False, f"failed to copy LandSAR authorization block to auth server dir: {exc}"
            else:
                return False, f"auth server memory image missing: {memory_bin}"

        command = [exe, "--host", bind_host, "--port", str(bind_port)]
        try:
            subprocess.Popen(
                command,
                cwd=server_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            return False, f"failed to start auth server: {exc}"

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if _tcp_connect(client_host, client_port):
                return True, f"started: {exe} on {bind_host}:{bind_port}; client={client_host}:{client_port}"
            time.sleep(0.25)
        return False, f"started auth server but client port is not reachable: {client_host}:{client_port}"

    def get_profiles(self) -> List[EngineProfile]:
        params_schema = {
            "dem_path": {
                "label": "DEM 文件",
                "type": "string",
                "default": self._default_dem_path,
                "section": "输入数据",
                "readonly": True,
                "readonly_label": "服务器固定",
                "include_in_payload": False,
                "description": "传给 LandSAR 200014 D-InSAR 模块的外部参考 DEM，建议使用已验证可用的 GeoTIFF。",
            },
            "az_looks": {
                "label": "方位向多视数",
                "type": "number",
                "default": _DEFAULT_PARAM_VALUES["az_looks"],
                "step": 1,
                "min": 1,
                "section": "核心参数",
            },
            "rg_looks": {
                "label": "距离向多视数",
                "type": "number",
                "default": _DEFAULT_PARAM_VALUES["rg_looks"],
                "step": 1,
                "min": 1,
                "section": "核心参数",
            },
            "coh_mask_threshold": {
                "label": "相干掩膜阈值",
                "type": "number",
                "default": _DEFAULT_PARAM_VALUES["coh_mask_threshold"],
                "step": 0.01,
                "min": 0,
                "max": 1,
                "section": "核心参数",
            },
            "unwrap_coh_threshold": {
                "label": "解缠相干阈值",
                "type": "number",
                "default": _DEFAULT_PARAM_VALUES["unwrap_coh_threshold"],
                "step": 0.01,
                "min": 0,
                "max": 1,
                "section": "核心参数",
            },
            "filter_alpha": {
                "label": "Goldstein 滤波因子",
                "type": "number",
                "default": _DEFAULT_PARAM_VALUES["filter_alpha"],
                "step": 0.05,
                "min": 0,
                "max": 1,
                "section": "核心参数",
            },
            "do_vertical_displacement": {
                "label": "生成垂直向形变",
                "type": "boolean",
                "default": False,
                "section": "输出与改正",
                "description": "可选模块，将 LOS 向形变换算为垂直向形变；LOS 形变仍会保留。",
            },
            "do_atmosphere": {
                "label": "GACOS 大气相位改正",
                "type": "boolean",
                "default": False,
                "section": "输出与改正",
                "readonly": True,
                "readonly_label": "暂未开放",
                "include_in_payload": False,
                "description": "暂未开放。该模块需要外部 GACOS 大气延迟文件，LandSAR 不会自动生成该文件。",
            },
            "do_geocoding": {
                "label": "地理编码输出",
                "type": "boolean",
                "default": True,
                "section": "输出与改正",
                "readonly": True,
                "readonly_label": "固定开启",
                "include_in_payload": False,
                "description": "系统入库和地图展示依赖地理编码 GeoTIFF，因此固定开启。",
            },
        }
        return [
            EngineProfile(
                code="lt1_dinsar",
                label="LT-1 LandSAR D-InSAR",
                description="Run LandSAR import 100016 when needed, then proID 200014 D-InSAR.",
                params_schema=params_schema,
            ),
        ]

    def normalize_extra(self, extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for key, value in dict(extra or {}).items():
            if key in _SYSTEM_EXTRA_KEYS:
                normalized[key] = value
                continue
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == "":
                continue
            normalized[key] = value

        for key in set(_DEFAULT_PARAM_VALUES) | {"dem_path"}:
            if key not in normalized:
                continue
            value = normalized[key]
            if key in _PATH_PARAM_KEYS:
                path = _norm_path(value)
                if key in {"gcp_file", "gacos_file"} and not path:
                    normalized.pop(key, None)
                    continue
                normalized[key] = path
                continue
            if key in _INT_PARAM_KEYS:
                try:
                    parsed = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be an integer.") from exc
                normalized[key] = parsed
                continue
            if key in _FLOAT_PARAM_KEYS:
                try:
                    parsed_f = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be a number.") from exc
                if key.endswith("threshold") and (parsed_f < 0 or parsed_f > 1):
                    raise ValueError(f"{key} must be between 0 and 1.")
                normalized[key] = parsed_f

        for bool_key in ("do_vertical_displacement", "do_atmosphere", "do_geocoding"):
            if bool_key in normalized:
                normalized[bool_key] = 1 if _coerce_bool(normalized[bool_key]) else 0

        return normalized

    def _iter_candidate_task_dirs(self, root_dir: str) -> Iterable[str]:
        root = _norm_path(root_dir)
        if os.path.isdir(os.path.join(root, "Input_Data")) or _looks_like_raw_task_dir(root):
            yield root
            return
        if not os.path.isdir(root):
            return
        for entry in sorted(os.scandir(root), key=lambda item: item.name.lower()):
            if entry.is_dir() and entry.name.lower().startswith("task_"):
                yield _norm_path(entry.path)

    def _has_completed_task_result(self, task_dir: str) -> bool:
        output_dir = os.path.join(_norm_path(task_dir), "Output_Data")
        return self._is_completed_output(output_dir)

    def _is_completed_output(self, output_dir: str) -> bool:
        if not output_dir or not os.path.isdir(output_dir):
            return False
        has_geo = bool(self._select_primary_file(output_dir))
        if not has_geo:
            return False

        log_candidates = [
            os.path.join(output_dir, f"{DINSAR_PROID}.log"),
            os.path.join(output_dir, f"{DINSAR_PROID}_console.log"),
        ]
        log_candidates.extend(str(path) for path in Path(output_dir).glob(f"*{DINSAR_PROID}*.log"))
        for log_path in log_candidates:
            if not os.path.isfile(log_path):
                continue
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                if "console success" in content.lower():
                    return True
                if _SUCCESS_RE.search(content) and ("DInSAR" in content or "差分" in content or "module" in content.lower()):
                    return True
            except OSError:
                continue
        return False

    def validate_root_dir(
        self,
        root_dir: str,
        num_to_process: int = 0,
        rerun_mode: str = "rerun_all",
    ) -> Dict[str, Any]:
        normalized_root = _norm_path(root_dir)
        if not normalized_root or not os.path.isdir(normalized_root):
            raise ValueError(f"LandSAR root_dir does not exist or is not a directory: {root_dir}")

        candidates = list(self._iter_candidate_task_dirs(normalized_root))
        invalid_candidates: List[Dict[str, Any]] = []
        valid_task_dirs: List[str] = []
        for task_dir in candidates:
            input_dir = os.path.join(task_dir, "Input_Data")
            raw_ready = _looks_like_raw_task_dir(task_dir)
            if not os.path.isdir(input_dir):
                if raw_ready:
                    valid_task_dirs.append(task_dir)
                    continue
                invalid_candidates.append(
                    {
                        "name": os.path.basename(task_dir),
                        "path": task_dir,
                        "reason": "missing Input_Data and no valid master/slave raw LT-1 data",
                    }
                )
                continue
            pair = parse_lt1_slc_pair(input_dir)
            if pair is None:
                if raw_ready:
                    valid_task_dirs.append(task_dir)
                    continue
                invalid_candidates.append(
                    {
                        "name": os.path.basename(task_dir),
                        "path": task_dir,
                        "reason": "less than two valid Input_Data SLC xml/tif pairs and no valid master/slave raw LT-1 data",
                    }
                )
                continue
            valid_task_dirs.append(task_dir)

        if not valid_task_dirs:
            detail = ""
            if invalid_candidates:
                formatted = ", ".join(f"{item['name']} {item['reason']}" for item in invalid_candidates[:5])
                detail = f" Invalid candidates: {formatted}."
            raise ValueError(
                "LandSAR root_dir must be either one Task_* directory containing Input_Data, "
                "one Task_* directory containing master/slave raw LT-1 folders, "
                "or a parent directory containing either layout."
                f"{detail}"
            )

        discovered_task_count = len(valid_task_dirs)
        selected_dirs: List[str] = []
        skipped_completed_count = 0
        if str(rerun_mode or "").strip().lower() == RERUN_MODE_UNFINISHED_ONLY:
            for task_dir in valid_task_dirs:
                if self._has_completed_task_result(task_dir):
                    skipped_completed_count += 1
                    continue
                selected_dirs.append(task_dir)
        else:
            selected_dirs = list(valid_task_dirs)

        limit = int(num_to_process or 0)
        if limit > 0:
            selected_dirs = selected_dirs[:limit]

        mode = (
            "single_task_dir"
            if os.path.isdir(os.path.join(normalized_root, "Input_Data")) or _looks_like_raw_task_dir(normalized_root)
            else "task_root_dir"
        )
        return {
            "root_dir": normalized_root,
            "mode": mode,
            "task_dirs": selected_dirs,
            "task_count": len(selected_dirs),
            "selected_task_count": len(selected_dirs),
            "discovered_task_count": discovered_task_count,
            "skipped_completed_count": skipped_completed_count,
            "invalid_candidates": invalid_candidates,
        }

    def check_available(self) -> EngineAvailability:
        console_path = self._console_exe
        home = self._home
        config_ok, config_detail = self._ensure_config_csv() if os.path.isdir(home) else (False, f"LANDSAR_HOME missing: {home}")
        auth_ok, auth_detail = self._start_auth_server_if_needed() if config_ok else (False, "skipped until config.csv is available")
        version_control_exe = self._version_control_exe
        enabled = self._enabled
        dll_check = _check_required_dlls(console_path, home) if os.path.isfile(console_path) else {
            "ok": False,
            "missing": list(_LANDSAR_REQUIRED_DLLS),
            "found": {},
            "search_dirs": _path_search_dirs(home),
        }
        checks = [
            {"name": "LANDSAR_ENABLED", "ok": enabled, "detail": str(enabled).lower()},
            {"name": "LANDSAR_HOME", "ok": os.path.isdir(home), "detail": home},
            {"name": "InSAR_Console.exe", "ok": os.path.isfile(console_path), "detail": console_path},
            {
                "name": "LandSAR runtime DLLs",
                "ok": bool(dll_check["ok"]),
                "detail": "ok" if dll_check["ok"] else f"missing: {', '.join(dll_check['missing'])}",
            },
            {
                "name": "LandSAR config.csv",
                "ok": config_ok,
                "detail": config_detail,
            },
            {
                "name": "LandSAR license mode",
                "ok": True,
                "detail": self._expected_config_row,
                "optional": True,
            },
            {
                "name": "LandSAR auth server",
                "ok": auth_ok,
                "detail": auth_detail,
                "optional": not self._is_network_license_mode(),
            },
            {
                "name": "versionControl.exe",
                "ok": os.path.isfile(version_control_exe),
                "detail": version_control_exe,
                "optional": True,
            },
            {
                "name": "LANDSAR_DEM_PATH",
                "ok": True,
                "detail": self._default_dem_path or "set per run",
                "optional": True,
            },
        ]
        available = enabled and os.path.isfile(console_path) and bool(dll_check["ok"]) and config_ok and auth_ok
        status = "ok" if available else "unavailable"
        if available and not os.path.isdir(home):
            status = "degraded"
        if available:
            message = "LandSAR console is available."
        elif os.path.isfile(console_path) and not dll_check["ok"]:
            message = f"LandSAR console dependencies are missing: {', '.join(dll_check['missing'])}"
        elif not config_ok:
            message = config_detail
        elif not auth_ok:
            message = auth_detail
        else:
            message = "LandSAR console is not configured."
        return EngineAvailability(
            engine_code=self.engine_code,
            status=status,
            available=available,
            checks=checks,
            message=message,
        )

    def run(self, request: RunRequest) -> RunResult:
        if not self._enabled:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error="LandSAR is disabled.",
            )
        if request.profile not in SUPPORTED_PROFILES:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error=f"Unknown LandSAR profile: {request.profile}",
            )

        extra = self.normalize_extra(request.extra)
        dem_source_path = _norm_path(extra.get("dem_path") or self._default_dem_path)
        if not dem_source_path or not os.path.isfile(dem_source_path):
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error=f"LandSAR DEM source file is missing: {dem_source_path or '<empty>'}",
            )
        if _coerce_bool(extra.get("do_atmosphere")):
            gacos_path = _norm_path(extra.get("gacos_file"))
            if not gacos_path or not os.path.isfile(gacos_path):
                return RunResult(
                    success=False,
                    engine_code=self.engine_code,
                    profile=request.profile,
                    job_id=request.job_id,
                    error="LandSAR GACOS atmospheric correction requires a valid external GACOS file.",
                )

        console_path = self._console_exe
        if not os.path.isfile(console_path):
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error=f"InSAR_Console.exe not found: {console_path}",
            )
        config_ok, config_detail = self._ensure_config_csv()
        if not config_ok:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error=f"LandSAR config.csv is not ready: {config_detail}",
            )
        auth_ok, auth_detail = self._start_auth_server_if_needed()
        if not auth_ok:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error=f"LandSAR network license server is not ready: {auth_detail}",
            )

        validation = self.validate_root_dir(
            request.root_dir,
            request.num_to_process,
            str(extra.get("__rerun_mode") or "rerun_all"),
        )
        task_dirs: List[str] = list(validation["task_dirs"])
        run_started_at = datetime.utcnow()
        run_started_at_text = _utc_text(run_started_at)
        run_key_override = str(extra.get("__managed_run_key") or "").strip()
        run_key = run_key_override or f"run_{run_started_at.strftime('%Y%m%dT%H%M%SZ')}_{self.engine_code}_{request.profile}"
        timeout = max(60, int(request.timeout_seconds or self.default_timeout_seconds))
        output_dirs: List[str] = []
        task_results: List[Dict[str, Any]] = []
        pairs_processed = 0
        pairs_failed = 0

        progress_callback = request.progress_callback

        def emit_progress(event_type: str, **payload: Any) -> None:
            if not callable(progress_callback):
                return
            try:
                progress_callback({"event": event_type, **payload})
            except Exception:
                return

        for pair_index, task_dir in enumerate(task_dirs, start=1):
            task_name = os.path.basename(task_dir)
            initial_input_data_dir = os.path.join(task_dir, "Input_Data")
            parsed_pair = parse_lt1_slc_pair(initial_input_data_dir)
            task_alias, pair_key, pair_meta = self._resolve_task_identity(task_dir, task_name, parsed_pair)

            managed_run_dir = _norm_path(extra.get("__managed_run_dir"))
            if managed_run_dir:
                run_dir = managed_run_dir
            else:
                pair_root = os.path.join(settings.DINSAR_PRODUCT_DIR, pair_key, "runs")
                run_dir = os.path.join(pair_root, run_key)
            native_output_dir = _norm_path(extra.get("__managed_native_output_dir")) or os.path.join(run_dir, "native")
            landsar_input_dir = os.path.join(native_output_dir, "landsar_input")
            landsar_output_dir = os.path.join(native_output_dir, "landsar_output")
            os.makedirs(landsar_output_dir, exist_ok=True)

            command = [console_path, os.path.join(landsar_output_dir, f"{DINSAR_PROID}.txt")]
            emit_progress(
                "pair_started",
                pair_index=pair_index,
                pair_total=len(task_dirs),
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
            )

            if not parsed_pair:
                import_result = self._ensure_imported_input_data(
                    task_dir=task_dir,
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    export_dir=landsar_input_dir,
                    console_path=console_path,
                    timeout=timeout,
                    pair_index=pair_index,
                    pair_total=len(task_dirs),
                    emit_progress=emit_progress,
                )
                if not import_result.get("success"):
                    pairs_failed += 1
                    error_text = str(import_result.get("error") or "LandSAR import failed.")
                    emit_progress("pair_finished", pair_index=pair_index, pair_total=len(task_dirs), success=False, error=error_text)
                    task_results.append(
                        self._build_task_result(
                            task_name=task_name,
                            task_alias=task_alias,
                            pair_key=pair_key,
                            run_key=run_key,
                            task_dir=task_dir,
                            run_dir=run_dir,
                            native_output_dir=native_output_dir,
                            landsar_output_dir=landsar_output_dir,
                            command=import_result.get("command") or command,
                            success=False,
                            returncode=int(import_result.get("returncode") or -2),
                            error=error_text,
                            stdout_tail=str(import_result.get("stdout_tail") or ""),
                            param_file=str(import_result.get("param_file") or ""),
                        )
                    )
                    continue
                parsed_pair = parse_lt1_slc_pair(str(import_result.get("input_data_dir") or landsar_input_dir))
                if not parsed_pair:
                    pairs_failed += 1
                    error_text = "LandSAR import completed but no valid Input_Data xml/tif pair was produced."
                    emit_progress("pair_finished", pair_index=pair_index, pair_total=len(task_dirs), success=False, error=error_text)
                    task_results.append(
                        self._build_task_result(
                            task_name=task_name,
                            task_alias=task_alias,
                            pair_key=pair_key,
                            run_key=run_key,
                            task_dir=task_dir,
                            run_dir=run_dir,
                            native_output_dir=native_output_dir,
                            landsar_output_dir=landsar_output_dir,
                            command=import_result.get("command") or command,
                            success=False,
                            returncode=-2,
                            error=error_text,
                            stdout_tail=str(import_result.get("stdout_tail") or ""),
                            param_file=str(import_result.get("param_file") or ""),
                        )
                    )
                    continue
                task_alias, pair_key, pair_meta = self._resolve_task_identity(task_dir, task_name, parsed_pair)

            dem_blockers = _scene_centers_outside_dem(parsed_pair, dem_source_path)
            if dem_blockers:
                pairs_failed += 1
                error_text = "LandSAR DEM coverage preflight failed: " + "; ".join(dem_blockers)
                emit_progress("pair_finished", pair_index=pair_index, pair_total=len(task_dirs), success=False, error=error_text)
                task_results.append(
                    self._build_task_result(
                        task_name=task_name,
                        task_alias=task_alias,
                        pair_key=pair_key,
                        run_key=run_key,
                        task_dir=task_dir,
                        run_dir=run_dir,
                        native_output_dir=native_output_dir,
                        landsar_output_dir=landsar_output_dir,
                        command=command,
                        success=False,
                        returncode=-2,
                        error=error_text,
                        stdout_tail="",
                        param_file="",
                        dem_source_path=dem_source_path,
                        dem_path=effective_dem_path,
                        dem_crop=dem_crop_info,
                    )
                )
                continue

            effective_dem_path = dem_source_path
            dem_crop_info: Dict[str, Any] = {}
            try:
                dem_bbox = derive_landsar_pair_dem_bbox(parsed_pair)
                if not dem_bbox:
                    raise ValueError("cannot derive DEM crop bbox from LT-1 XML corner coordinates")
                dem_crop_info = prepare_landsar_dem_crop(
                    dem_source_path,
                    os.path.join(native_output_dir, "dem_crop"),
                    dem_bbox,
                    label=task_alias,
                )
                effective_dem_path = str(dem_crop_info.get("dem_path") or dem_source_path)
                emit_progress(
                    "log",
                    pair_index=pair_index,
                    pair_total=len(task_dirs),
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    level="INFO",
                    source="dem",
                    message=(
                        f"LandSAR DEM crop ready: {effective_dem_path} "
                        f"from {dem_source_path}"
                    ),
                )
            except Exception as exc:
                pairs_failed += 1
                error_text = f"LandSAR DEM crop failed: {exc}"
                emit_progress("pair_finished", pair_index=pair_index, pair_total=len(task_dirs), success=False, error=error_text)
                task_results.append(
                    self._build_task_result(
                        task_name=task_name,
                        task_alias=task_alias,
                        pair_key=pair_key,
                        run_key=run_key,
                        task_dir=task_dir,
                        run_dir=run_dir,
                        native_output_dir=native_output_dir,
                        landsar_output_dir=landsar_output_dir,
                        command=command,
                        success=False,
                        returncode=-2,
                        error=error_text,
                        stdout_tail="",
                        param_file="",
                    )
                )
                continue

            param_values = {**_DEFAULT_PARAM_VALUES}
            param_values.update({key: value for key, value in extra.items() if key in _DEFAULT_PARAM_VALUES})
            param_file = _generate_dinsar_param_file(
                command[1],
                master_xml=parsed_pair["master_xml"],
                master_tif=parsed_pair["master_tif"],
                slave_xml=parsed_pair["slave_xml"],
                slave_tif=parsed_pair["slave_tif"],
                dem_path=effective_dem_path,
                output_dir=landsar_output_dir,
                params=param_values,
            )
            command = [console_path, param_file]

            emit_progress(
                "log",
                pair_index=pair_index,
                pair_total=len(task_dirs),
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                level="INFO",
                source="input",
                message=f"master={os.path.basename(parsed_pair['master_xml'])}, slave={os.path.basename(parsed_pair['slave_xml'])}",
            )
            emit_progress(
                "log",
                pair_index=pair_index,
                pair_total=len(task_dirs),
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                level="INFO",
                source="dem",
                message=effective_dem_path,
            )

            rc, stdout_text, timed_out = self._run_console(
                command,
                cwd=self._home if os.path.isdir(self._home) else os.path.dirname(console_path),
                log_path=os.path.join(landsar_output_dir, f"{DINSAR_PROID}_console.log"),
                timeout=timeout,
                emit_log=lambda level, message: emit_progress(
                    "log",
                    pair_index=pair_index,
                    pair_total=len(task_dirs),
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    level=level,
                    source="console",
                    message=message,
                ),
            )

            success_marker = self._is_completed_output(landsar_output_dir)
            primary_raw = self._select_primary_file(landsar_output_dir)
            coherence_raw = self._select_coherence_file(landsar_output_dir)
            success = rc == 0 and success_marker and bool(primary_raw)
            error_text = ""
            primary_file = ""
            source_files: List[str] = []
            validation_result: Dict[str, Any] = {}
            layout_result: Dict[str, Any] = {}
            if timed_out:
                error_text = f"LandSAR console timed out after {timeout}s."
                success = False
            elif rc != 0:
                error_text = _summarize_landsar_failure(stdout_text, "LandSAR console", rc)
            elif not success_marker:
                error_text = "LandSAR success marker or geocoded output is missing."
            elif not primary_raw:
                error_text = "LandSAR primary displacement GeoTIFF is missing."

            if success:
                try:
                    raw_sources = [primary_raw]
                    if coherence_raw:
                        raw_sources.append(coherence_raw)
                    validation_result = validate_isce2_result_files(primary_raw, raw_sources)
                    if not bool(validation_result.get("accepted")):
                        issues = validation_result.get("issues") or []
                        raise RuntimeError("; ".join(str(item) for item in issues[:3]) or "GeoTIFF validation failed.")

                    os.makedirs(run_dir, exist_ok=True)
                    standard_disp = os.path.join(run_dir, "assets", "disp", "disp.tif")
                    standard_coh = os.path.join(run_dir, "assets", "coh", "coh.tif")
                    _copy_file(primary_raw, standard_disp)
                    if coherence_raw:
                        _copy_file(coherence_raw, standard_coh)
                    source_files = [standard_disp]
                    if os.path.isfile(standard_coh):
                        source_files.append(standard_coh)
                    layout_result = normalize_isce2_run_layout(
                        run_dir,
                        primary_file=standard_disp,
                        source_files=source_files,
                        rewrite_metadata=False,
                    )
                    primary_file = layout_result["primary_file"]
                    source_files = list(layout_result["source_files"])

                    pair_meta_payload = self._build_pair_meta_payload(
                        pair_key=pair_key,
                        task_alias=task_alias,
                        pair_meta=pair_meta,
                        parsed_pair=parsed_pair,
                    )
                    write_pair_metadata(task_dir, pair_meta_payload)
                    self._write_run_metadata(
                        run_dir=run_dir,
                        native_output_dir=native_output_dir,
                        task_dir=task_dir,
                        task_name=task_name,
                        task_alias=task_alias,
                        pair_key=pair_key,
                        run_key=run_key,
                        request=request,
                        pair_meta=pair_meta_payload,
                        params=param_values,
                        dem_path=effective_dem_path,
                        dem_source_path=dem_source_path,
                        dem_crop=dem_crop_info,
                        landsar_output_dir=landsar_output_dir,
                        primary_file=primary_file,
                        source_files=source_files,
                        returncode=rc,
                        started_at=run_started_at_text,
                    )
                    output_dirs.append(run_dir)
                    pairs_processed += 1
                except Exception as exc:
                    success = False
                    error_text = f"LandSAR output packaging failed: {exc}"

            if not success:
                pairs_failed += 1

            emit_progress(
                "pair_finished",
                pair_index=pair_index,
                pair_total=len(task_dirs),
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                success=success,
                returncode=rc,
                error=error_text,
            )
            task_results.append(
                self._build_task_result(
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    run_key=run_key,
                    task_dir=task_dir,
                    run_dir=run_dir,
                    native_output_dir=native_output_dir,
                    landsar_output_dir=landsar_output_dir,
                    command=command,
                    success=success,
                    returncode=rc,
                    error=error_text,
                    stdout_tail=_collect_tail(stdout_text, 3000),
                    primary_file=primary_file,
                    source_files=source_files,
                    validation=validation_result,
                    layout=layout_result,
                    param_file=param_file,
                    raw_primary_file=primary_raw,
                    raw_coherence_file=coherence_raw,
                    dem_source_path=dem_source_path,
                    dem_path=effective_dem_path,
                    dem_crop=dem_crop_info,
                )
            )

        invalid_candidates = validation.get("invalid_candidates", [])
        pairs_failed += len(invalid_candidates)
        overall_success = pairs_processed > 0 or (pairs_processed == 0 and pairs_failed == 0)
        run_status = "COMPLETED" if pairs_failed == 0 else ("PARTIAL" if pairs_processed > 0 else "FAILED")
        error = None
        if not overall_success:
            failed_names = [item.get("task_alias") or item.get("task_name") for item in task_results if not item.get("success")]
            error = f"All LandSAR tasks failed: {', '.join(failed_names[:10])}" if failed_names else "LandSAR run failed."

        return RunResult(
            success=overall_success,
            engine_code=self.engine_code,
            profile=request.profile,
            job_id=request.job_id,
            pairs_processed=pairs_processed,
            pairs_failed=pairs_failed,
            output_dirs=output_dirs,
            error=error,
            detail={
                "mode": validation["mode"],
                "run_status": run_status,
                "task_count": len(task_dirs),
                "selected_tasks": [item.get("task_alias") or item.get("task_name") for item in task_results],
                "invalid_candidates": invalid_candidates,
                "task_results": task_results,
                "run_key": run_key,
                "started_at": run_started_at_text,
                "timeout_seconds": timeout,
                "dem_path": dem_source_path,
                "dem_role": "global_prepared_source",
                "console_path": console_path,
            },
        )

    def _run_console(
        self,
        command: Any,
        *,
        cwd: str,
        log_path: str,
        timeout: int,
        emit_log,
    ) -> tuple[int, str, bool]:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as log_fp:
            log_fp.write(f"\n[{_utc_text()}] command: {' '.join(command)}\n")
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=_landsar_process_env(command[0], self._home),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output_lines: List[str] = []
            line_queue: queue.Queue[Any] = queue.Queue()
            sentinel = object()

            def _reader() -> None:
                try:
                    if process.stdout is None:
                        return
                    for raw_line in iter(process.stdout.readline, b""):
                        line_queue.put(raw_line)
                finally:
                    line_queue.put(sentinel)

            reader = threading.Thread(target=_reader, name="landsar-console-reader", daemon=True)
            reader.start()
            started = time.monotonic()
            timed_out = False
            stdout_closed = False
            while True:
                try:
                    raw_item = line_queue.get(timeout=1)
                except queue.Empty:
                    raw_item = None

                if raw_item is sentinel:
                    stdout_closed = True
                elif raw_item:
                    line = _decode_line(raw_item)
                    output_lines.append(line)
                    log_fp.write(line + "\n")
                    log_fp.flush()
                    if line.strip():
                        emit_log("INFO", line.strip())

                if process.poll() is not None and stdout_closed:
                    break
                if time.monotonic() - started > timeout:
                    timed_out = True
                    process.kill()
                    break

            if timed_out:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            reader.join(timeout=5)
            while True:
                try:
                    raw_item = line_queue.get_nowait()
                except queue.Empty:
                    break
                if raw_item is sentinel or not raw_item:
                    continue
                line = _decode_line(raw_item)
                output_lines.append(line)
                log_fp.write(line + "\n")
                if line.strip():
                    emit_log("INFO", line.strip())
            if process.poll() is None:
                timed_out = True
                process.kill()
            return_code = int(process.poll() if process.poll() is not None else -9)
            if timed_out:
                log_fp.write(f"[{_utc_text()}] timeout after {timeout}s\n")
            log_fp.write(f"[{_utc_text()}] returncode={return_code}\n")
        return return_code, "\n".join(output_lines), timed_out

    def _ensure_imported_input_data(
        self,
        *,
        task_dir: str,
        task_name: str,
        task_alias: str,
        pair_key: str,
        export_dir: str,
        console_path: str,
        timeout: int,
        pair_index: int,
        pair_total: int,
        emit_progress,
    ) -> Dict[str, Any]:
        master_dir = os.path.join(_norm_path(task_dir), "master")
        slave_dir = os.path.join(_norm_path(task_dir), "slave")
        export_dir = _norm_path(export_dir)
        os.makedirs(export_dir, exist_ok=True)

        existing_pair = parse_lt1_slc_pair(export_dir)
        if existing_pair:
            return {
                "success": True,
                "input_data_dir": export_dir,
                "skipped": True,
                "returncode": 0,
                "command": "",
                "param_file": "",
                "stdout_tail": "",
            }

        if not _looks_like_raw_task_dir(task_dir):
            return {
                "success": False,
                "input_data_dir": export_dir,
                "returncode": -2,
                "error": "Task directory has neither valid Input_Data nor valid master/slave raw LT-1 folders.",
            }

        sat_mode = _infer_lt1_import_sat_mode(master_dir, slave_dir)
        param_file = _generate_import_param_file(
            os.path.join(export_dir, f"{IMPORT_PROID}.txt"),
            master_dir=master_dir,
            slave_dir=slave_dir,
            export_dir=export_dir,
            import_method="dir",
            sat_mode=sat_mode,
            read_xml=True,
            read_slc=True,
            export_to_new=True,
        )
        command = [console_path, param_file]
        emit_progress(
            "log",
            pair_index=pair_index,
            pair_total=pair_total,
            task_name=task_name,
            task_alias=task_alias,
            pair_key=pair_key,
            level="INFO",
            source="import",
            message=f"LandSAR import 100016 ({sat_mode}) -> {export_dir}",
        )
        rc, stdout_text, timed_out = self._run_console(
            command,
            cwd=self._home if os.path.isdir(self._home) else os.path.dirname(console_path),
            log_path=os.path.join(export_dir, f"{IMPORT_PROID}_console.log"),
            timeout=timeout,
            emit_log=lambda level, message: emit_progress(
                "log",
                pair_index=pair_index,
                pair_total=pair_total,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                level=level,
                source="import",
                message=message,
            ),
        )

        success = rc == 0 and self._is_completed_import(export_dir) and bool(parse_lt1_slc_pair(export_dir))
        error = ""
        if timed_out:
            error = f"LandSAR import timed out after {timeout}s."
            success = False
        elif rc != 0:
            error = _summarize_landsar_failure(stdout_text, "LandSAR import", rc)
        elif not self._is_completed_import(export_dir):
            error = "LandSAR import success marker is missing."
        elif not parse_lt1_slc_pair(export_dir):
            error = "LandSAR import did not produce at least two LT1*_SLC xml/tif pairs."

        return {
            "success": success,
            "input_data_dir": export_dir,
            "returncode": rc,
            "error": error,
            "command": " ".join(command),
            "param_file": param_file,
            "stdout_tail": _collect_tail(stdout_text, 3000),
        }

    def _is_completed_import(self, output_dir: str) -> bool:
        if not output_dir or not os.path.isdir(output_dir):
            return False
        log_candidates = [
            os.path.join(output_dir, f"{IMPORT_PROID}.log"),
            os.path.join(output_dir, f"{IMPORT_PROID}_console.log"),
        ]
        log_candidates.extend(str(path) for path in Path(output_dir).glob(f"*{IMPORT_PROID}*.log"))
        for log_path in log_candidates:
            if not os.path.isfile(log_path):
                continue
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                lowered = content.lower()
                if "console success" in lowered:
                    return True
                if "module [LT-1数据导入] success" in content:
                    return True
                if "lt-1" in lowered and "success" in lowered:
                    return True
            except OSError:
                continue
        return False

    def _resolve_task_identity(
        self,
        task_dir: str,
        task_name: str,
        parsed_pair: Optional[Dict[str, Any]],
    ) -> tuple[str, str, Dict[str, Any]]:
        sidecar_path = os.path.join(task_dir, PAIR_META_FILENAME)
        pair_meta: Dict[str, Any] = {}
        if os.path.isfile(sidecar_path):
            try:
                with open(sidecar_path, "r", encoding="utf-8") as fp:
                    payload = json.load(fp)
                if isinstance(payload, dict):
                    pair_meta = payload
            except Exception:
                pair_meta = {}
        task_alias = str(pair_meta.get("task_alias") or task_name).strip() or task_name
        satellite_family = normalize_satellite_family(
            pair_meta.get("master_satellite") or pair_meta.get("slave_satellite") or "lt1"
        )
        pair_key = str(pair_meta.get("pair_key") or "").strip()
        if not pair_key and parsed_pair:
            pair_key = build_fallback_pair_key(
                task_alias,
                "||".join([parsed_pair["master_xml"], parsed_pair["slave_xml"]]),
                satellite_family=satellite_family,
            )
        if not pair_key:
            pair_key = build_fallback_pair_key(task_alias, task_dir, satellite_family=satellite_family)
        return task_alias, pair_key, pair_meta

    def _build_pair_meta_payload(
        self,
        *,
        pair_key: str,
        task_alias: str,
        pair_meta: Dict[str, Any],
        parsed_pair: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            **dict(pair_meta or {}),
            "pair_key": pair_key,
            "task_alias": task_alias,
            "master_path": pair_meta.get("master_path") or parsed_pair["master_tif"],
            "slave_path": pair_meta.get("slave_path") or parsed_pair["slave_tif"],
            "master_satellite": pair_meta.get("master_satellite") or "LT-1",
            "slave_satellite": pair_meta.get("slave_satellite") or "LT-1",
            "master_imaging_date": pair_meta.get("master_imaging_date") or parsed_pair.get("master_date"),
            "slave_imaging_date": pair_meta.get("slave_imaging_date") or parsed_pair.get("slave_date"),
        }

    def _write_run_metadata(
        self,
        *,
        run_dir: str,
        native_output_dir: str,
        task_dir: str,
        task_name: str,
        task_alias: str,
        pair_key: str,
        run_key: str,
        request: RunRequest,
        pair_meta: Dict[str, Any],
        params: Dict[str, Any],
        dem_path: str,
        landsar_output_dir: str,
        primary_file: str,
        source_files: List[str],
        returncode: int,
        started_at: str,
        dem_source_path: str = "",
        dem_crop: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "run_key": run_key,
            "pair_key": pair_key,
            "task_name": task_name,
            "task_alias": task_alias,
            "engine_code": self.engine_code,
            "profile_code": request.profile,
            "source_root": _norm_path(request.extra.get("__source_root_override") or request.root_dir),
            "task_dir": _norm_path(request.extra.get("__source_task_dir_override") or task_dir),
            "work_dir": landsar_output_dir,
            "output_dir": _norm_path(run_dir),
            "native_output_dir": _norm_path(native_output_dir),
            "started_at": started_at,
            "finished_at": _utc_text(),
            "params": {
                **dict(params or {}),
                "dem_path": dem_path,
                "dem_source_path": dem_source_path or dem_path,
                "dem_crop": dem_crop or {},
            },
            "metrics": {
                "returncode": returncode,
                "primary_file": primary_file,
                "source_files": source_files,
            },
            "master_path": pair_meta.get("master_path"),
            "slave_path": pair_meta.get("slave_path"),
            "master_satellite": pair_meta.get("master_satellite"),
            "slave_satellite": pair_meta.get("slave_satellite"),
            "master_imaging_date": pair_meta.get("master_imaging_date"),
            "slave_imaging_date": pair_meta.get("slave_imaging_date"),
            "master_imaging_mode": pair_meta.get("master_imaging_mode"),
            "slave_imaging_mode": pair_meta.get("slave_imaging_mode"),
            "master_polarization": pair_meta.get("master_polarization"),
            "slave_polarization": pair_meta.get("slave_polarization"),
            "time_baseline_days": pair_meta.get("time_baseline_days"),
            "spatial_baseline_meters": pair_meta.get("spatial_baseline_meters"),
            "scene_center_distance_meters": pair_meta.get("scene_center_distance_meters"),
            "scene_pair_uid": pair_meta.get("scene_pair_uid") or pair_meta.get("pair_uid"),
            "pair_uid": pair_meta.get("pair_uid") or pair_meta.get("scene_pair_uid"),
            "network_run_id": pair_meta.get("network_run_id"),
            "network_edge_id": pair_meta.get("network_edge_id"),
            "policy_version": pair_meta.get("policy_version"),
            "selection_strategy": pair_meta.get("selection_strategy"),
        }
        write_run_metadata(run_dir, payload)
        write_run_metadata(native_output_dir, payload)

    def _build_task_result(
        self,
        *,
        task_name: str,
        task_alias: str,
        pair_key: str,
        run_key: str,
        task_dir: str,
        run_dir: str,
        native_output_dir: str,
        landsar_output_dir: str,
        command: List[str],
        success: bool,
        returncode: int,
        error: str = "",
        stdout_tail: str = "",
        primary_file: str = "",
        source_files: Optional[List[str]] = None,
        validation: Optional[Dict[str, Any]] = None,
        layout: Optional[Dict[str, Any]] = None,
        param_file: str = "",
        raw_primary_file: str = "",
        raw_coherence_file: str = "",
        dem_source_path: str = "",
        dem_path: str = "",
        dem_crop: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        command_text = command if isinstance(command, str) else " ".join(str(part) for part in command)
        return {
            "task_name": task_name,
            "task_alias": task_alias,
            "pair_key": pair_key,
            "run_key": run_key,
            "task_dir": _norm_path(task_dir),
            "run_dir": _norm_path(run_dir),
            "native_output_dir": _norm_path(native_output_dir),
            "output_dir": _norm_path(run_dir),
            "landsar_output_dir": _norm_path(landsar_output_dir),
            "primary_file": _norm_path(primary_file) if primary_file else "",
            "source_files": [_norm_path(path) for path in (source_files or [])],
            "raw_primary_file": _norm_path(raw_primary_file) if raw_primary_file else "",
            "raw_coherence_file": _norm_path(raw_coherence_file) if raw_coherence_file else "",
            "param_file": _norm_path(param_file) if param_file else "",
            "dem_source_path": _norm_path(dem_source_path) if dem_source_path else "",
            "dem_path": _norm_path(dem_path) if dem_path else "",
            "dem_crop": dem_crop or {},
            "validation": validation or {},
            "layout": layout or {},
            "command": command_text,
            "success": success,
            "returncode": returncode,
            "stdout_tail": stdout_tail,
            "stderr_tail": "",
            "error": error,
        }

    def _select_primary_file(self, output_dir: str) -> str:
        patterns = [
            "*displ.geo.tif",
            "*displ.geo.tiff",
            "*dispv.geo.tif",
            "*dispv.geo.tiff",
            "*diff.unw.geo.tif",
            "*diff.unw.geo.tiff",
            "*unw.geo.tif",
            "*unw.geo.tiff",
            "*.geo.tif",
            "*.geo.tiff",
        ]
        return self._first_matching_file(output_dir, patterns, exclude=("coh", "filcc", "wrap"))

    def _select_coherence_file(self, output_dir: str) -> str:
        return self._first_matching_file(output_dir, ["*coh.geo.tif", "*coh.geo.tiff", "*filcc.geo.tif", "*filcc.geo.tiff"])

    def _first_matching_file(self, output_dir: str, patterns: List[str], exclude: tuple[str, ...] = ()) -> str:
        root = Path(output_dir)
        if not root.is_dir():
            return ""
        for pattern in patterns:
            matches = sorted(root.rglob(pattern), key=lambda path: str(path).lower())
            for match in matches:
                if not match.is_file():
                    continue
                lower_name = match.name.lower()
                if any(token in lower_name for token in exclude):
                    continue
                return _norm_path(match)
        return ""
