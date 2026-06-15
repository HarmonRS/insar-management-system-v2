from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tarfile
import zipfile
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from geoalchemy2.shape import from_shape
from lxml import etree
from shapely.geometry import Polygon
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..config import settings
from ..models import (
    AssetInventoryIssueORM,
    AssetInventoryStateORM,
    ManagedRootORM,
    OrbitAssetORM,
    RadarDataORM,
    SceneOrbitBindingORM,
    SourceProductAssetORM,
)
from ..utils import (
    find_xml_file,
    normalize_satellite_family,
    parse_gf3_l2_dirname,
    parse_lt1_radar_filename,
    parse_xml_metadata,
)
from .pairing_state_service import pairing_state_service
from .task_service import task_service


PARSER_VERSION = "asset_inventory_v1"
S1_ORBIT_MATCH_RULE_VERSION = "s1_orbit_window_v1"
LT1_ORBIT_MATCH_RULE_VERSION = "lt1_orbit_day_v1"

_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_S1_SOURCE_RE = re.compile(
    r"^(?P<satellite>S1[A-Z])_"
    r"(?P<mode>[A-Z0-9]+)_"
    r"(?P<product>[A-Z0-9]+)_+"
    r"(?P<class>[0-9A-Z]{4})_"
    r"(?P<start>\d{8}T\d{6}(?:\.\d+)?)_"
    r"(?P<stop>\d{8}T\d{6}(?:\.\d+)?)_"
    r"(?P<absolute_orbit>\d+)_"
    r"(?P<datatake>[0-9A-F]+)_"
    r"(?P<product_uid>[0-9A-F]+)"
    r"(?:\.SAFE|\.zip)?$",
    re.IGNORECASE,
)
_S1_EOF_RE = re.compile(
    r"^(?P<satellite>S1[A-Z])_OPER_"
    r"(?P<orbit_type>AUX_[A-Z0-9]+)_"
    r"(?P<provider>[A-Z0-9]+)_"
    r"(?P<generation>\d{8}T\d{6})_"
    r"V(?P<valid_start>\d{8}T\d{6})_"
    r"(?P<valid_stop>\d{8}T\d{6})\.EOF$",
    re.IGNORECASE,
)
_LT1_ORBIT_RE = re.compile(
    r"^(?P<satellite>LT1[A-Z]?)_GpsData_GAS_C_(?P<date>\d{8})\.txt$",
    re.IGNORECASE,
)
_LT1_ARCHIVE_EXTS = (".tar.gz", ".tgz", ".zip", ".tar")
_GF3_ARCHIVE_EXTS = (".tar.gz", ".tgz", ".zip", ".tar")


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _configured_sentinel1_storage_dirs() -> List[str]:
    values = settings.SENTINEL1_STORAGE_DIRS or settings.SOURCE_PRODUCT_DIRS
    paths = [
        _normalize_path(item)
        for item in str(values or "").replace(";", ",").split(",")
        if str(item or "").strip()
    ]
    if not paths:
        paths = [_normalize_path(os.path.join(settings.BACKEND_DIR, "runtime", "sentinel1_safe"))]
    return paths


def _configured_sentinel1_archive_dirs() -> List[str]:
    values = settings.SOURCE_PRODUCT_DIRS
    paths = [
        _normalize_path(item)
        for item in str(values or "").replace(";", ",").split(",")
        if str(item or "").strip()
    ]
    deduped: List[str] = []
    for path in paths:
        if path and path not in deduped:
            deduped.append(path)
    return deduped


def _target_root_for_s1_archive(archive_path: str, target_root: Optional[str] = None) -> str:
    requested = _normalize_path(target_root or "")
    if requested:
        return requested

    storage_dirs = _configured_sentinel1_storage_dirs()
    source_dirs = [
        _normalize_path(item)
        for item in str(settings.SOURCE_PRODUCT_DIRS or "").replace(";", ",").split(",")
        if str(item or "").strip()
    ]
    if len(storage_dirs) > 1 and source_dirs and len(source_dirs) == len(storage_dirs):
        archive_norm = os.path.normcase(_normalize_path(archive_path))
        matches: List[Tuple[int, int]] = []
        for index, source_dir in enumerate(source_dirs):
            source_norm = os.path.normcase(_normalize_path(source_dir))
            if archive_norm == source_norm or archive_norm.startswith(source_norm + os.sep):
                matches.append((len(source_norm), index))
        if matches:
            _, best_index = max(matches)
            return storage_dirs[best_index]
    return storage_dirs[0]


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalize_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith("\\\\"):
        return os.path.normpath(text)
    if _WINDOWS_DRIVE_RE.match(text):
        return os.path.normpath(text)
    if text.startswith("/"):
        return text.replace("\\", "/")
    return os.path.normpath(os.path.abspath(text))


def _path_kind(path: str) -> str:
    text = str(path or "").strip()
    if text.startswith("\\\\"):
        return "unc"
    if _WINDOWS_DRIVE_RE.match(text):
        return "windows"
    if text.startswith("/mnt/"):
        return "wsl_mount"
    if text.startswith("/"):
        return "posix"
    return "relative"


def _stat_path(path: str) -> Dict[str, Optional[float]]:
    try:
        stat = os.stat(path)
        return {
            "size_bytes": int(stat.st_size),
            "mtime_epoch": float(stat.st_mtime),
            "ctime_epoch": float(stat.st_ctime),
        }
    except OSError:
        return {"size_bytes": None, "mtime_epoch": None, "ctime_epoch": None}


def _asset_uid(prefix: str, path: str) -> str:
    digest = hashlib.sha1(_normalize_path(path).lower().encode("utf-8", errors="ignore")).hexdigest()
    return f"{prefix}:{digest[:32]}"


def _strip_known_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in (".tar.gz", ".tgz"):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    if lower.endswith(".zip"):
        return name[:-4]
    if lower.endswith(".tar"):
        return name[:-4]
    if lower.endswith(".safe"):
        return name[:-5]
    return name


def _has_archive_suffix(name: str, suffixes: Sequence[str]) -> bool:
    lower = str(name or "").lower()
    return any(lower.endswith(suffix) for suffix in suffixes)


def _archive_member_base_name(member_name: str) -> str:
    text = str(member_name or "").replace("\\", "/").strip("/")
    return PurePosixPath(text).name


def _archive_member_scene_name(member_name: str, fallback: str) -> str:
    parts = [part for part in str(member_name or "").replace("\\", "/").split("/") if part]
    for part in parts:
        stem = _strip_known_suffix(part)
        if parse_lt1_radar_filename(stem) or parse_gf3_l2_dirname(stem):
            return stem
    return fallback


def _archive_read_first_matching(path: str, predicate: Callable[[str], bool]) -> Tuple[Optional[str], Optional[bytes], List[str]]:
    members: List[str] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename
                if info.is_dir():
                    continue
                members.append(name)
                if predicate(name):
                    return name, archive.read(info), members
        return None, None, members

    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                name = member.name
                if not member.isfile():
                    continue
                members.append(name)
                if predicate(name):
                    source = archive.extractfile(member)
                    if source is None:
                        continue
                    with source:
                        return name, source.read(), members
        return None, None, members

    return None, None, members


def _archive_list_matching(path: str, predicate: Callable[[str], bool], *, limit: int = 20) -> List[str]:
    matches: List[str] = []

    def _visit(name: str) -> None:
        if predicate(name):
            matches.append(name)

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                _visit(info.filename)
                if len(matches) >= limit:
                    break
        return matches

    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                _visit(member.name)
                if len(matches) >= limit:
                    break
    return matches


def _safe_archive_member_name(member_name: str, archive_path: str) -> str:
    name = str(member_name or "").replace("\\", "/").strip("/")
    if not name or name.startswith("../") or "/../" in f"/{name}/":
        raise ValueError(f"Unsafe archive member path in {archive_path}: {member_name}")
    if os.path.isabs(name) or os.path.splitdrive(name)[0]:
        raise ValueError(f"Unsafe archive member path in {archive_path}: {member_name}")
    return name


def _extract_archive_to_dir(archive_path: str, target_dir: str, *, overwrite: bool = False) -> Dict[str, Any]:
    archive = _normalize_path(archive_path)
    target = _normalize_path(target_dir)
    if not os.path.isfile(archive):
        raise FileNotFoundError(f"Archive does not exist: {archive}")
    if os.path.exists(target):
        if not overwrite:
            return {"status": "EXISTS", "archive_path": archive, "target_dir": target, "extracted": False, "member_count": None}
        shutil.rmtree(target)

    tmp_dir = target + ".materialize_tmp"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    extracted = 0
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zip_obj:
                for info in zip_obj.infolist():
                    rel_name = _safe_archive_member_name(info.filename, archive)
                    destination = os.path.abspath(os.path.join(tmp_dir, rel_name))
                    if not destination.startswith(os.path.abspath(tmp_dir) + os.sep):
                        raise ValueError(f"Unsafe ZIP member path: {info.filename}")
                    if info.is_dir():
                        os.makedirs(destination, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with zip_obj.open(info, "r") as source, open(destination, "wb") as target_stream:
                        shutil.copyfileobj(source, target_stream, length=1024 * 1024)
                    extracted += 1
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive, "r:*") as tar_obj:
                for member in tar_obj:
                    rel_name = _safe_archive_member_name(member.name, archive)
                    destination = os.path.abspath(os.path.join(tmp_dir, rel_name))
                    if not destination.startswith(os.path.abspath(tmp_dir) + os.sep):
                        raise ValueError(f"Unsafe TAR member path: {member.name}")
                    if member.isdir():
                        os.makedirs(destination, exist_ok=True)
                        continue
                    if not member.isfile():
                        continue
                    source = tar_obj.extractfile(member)
                    if source is None:
                        continue
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with source, open(destination, "wb") as target_stream:
                        shutil.copyfileobj(source, target_stream, length=1024 * 1024)
                    extracted += 1
        else:
            raise ValueError(f"Unsupported archive format: {archive}")

        if extracted <= 0:
            raise OSError(f"Archive extraction produced no files: {archive}")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(tmp_dir, target)
        return {"status": "EXTRACTED", "archive_path": archive, "target_dir": target, "extracted": True, "member_count": extracted}
    finally:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _parse_datetime_token(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith("UTC="):
        text = text[4:]
    text = text.rstrip("Z")
    try:
        if "-" in text and "." in text:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f")
        if "-" in text:
            return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
        if "." in text:
            return datetime.strptime(text, "%Y%m%dT%H%M%S.%f")
        return datetime.strptime(text, "%Y%m%dT%H%M%S")
    except ValueError:
        return None


def _xml_text_by_local_names(root: etree._Element, names: Sequence[str]) -> Optional[str]:
    name_set = {str(item).lower() for item in names}
    for element in root.iter():
        local_name = etree.QName(element).localname.lower()
        if local_name not in name_set:
            continue
        text = str(element.text or "").strip()
        if text:
            return text
    return None


def _xml_text_under_local_path(root: etree._Element, parent_name: str, child_name: str) -> Optional[str]:
    parent_key = parent_name.lower()
    child_key = child_name.lower()
    for parent in root.iter():
        if etree.QName(parent).localname.lower() != parent_key:
            continue
        for child in parent.iter():
            if child is parent:
                continue
            if etree.QName(child).localname.lower() == child_key:
                text = str(child.text or "").strip()
                if text:
                    return text
    return None


def _xml_float(value: Optional[str]) -> Optional[float]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_radar_xml_metadata_bytes(data: bytes) -> Tuple[Optional[List[Tuple[float, float]]], Dict[str, Any]]:
    parser = _xml_parser()
    root = etree.fromstring(data, parser=parser)

    corners: List[Tuple[float, float]] = []
    for element in root.iter():
        if etree.QName(element).localname.lower() != "scenecornercoord":
            continue
        lon = _xml_float(_xml_text_under_local_path(element, "sceneCornerCoord", "lon") or _xml_text_by_local_names(element, ["lon"]))
        lat = _xml_float(_xml_text_under_local_path(element, "sceneCornerCoord", "lat") or _xml_text_by_local_names(element, ["lat"]))
        if lon is not None and lat is not None:
            corners.append((lon, lat))

    coverage_polygon: Optional[List[Tuple[float, float]]] = None
    if len(corners) >= 4:
        coverage_polygon = corners[:4]
        if coverage_polygon[0] != coverage_polygon[-1]:
            coverage_polygon.append(coverage_polygon[0])

    start_time = (
        _xml_text_under_local_path(root, "start", "timeUTC")
        or _xml_text_by_local_names(root, ["startTime", "start_time", "beginPosition"])
    )
    stop_time = (
        _xml_text_under_local_path(root, "stop", "timeUTC")
        or _xml_text_by_local_names(root, ["stopTime", "stop_time", "endPosition"])
    )
    center_lon = _xml_float(_xml_text_under_local_path(root, "sceneCenterCoord", "lon"))
    center_lat = _xml_float(_xml_text_under_local_path(root, "sceneCenterCoord", "lat"))
    metadata = {
        "orbit_direction": (_xml_text_by_local_names(root, ["pass", "orbitDirection"]) or "").upper() or None,
        "imaging_mode": _xml_text_under_local_path(root, "acquisitionInfo", "imagingMode")
        or _xml_text_under_local_path(root, "orderInfo", "imagingMode")
        or _xml_text_by_local_names(root, ["imagingMode"]),
        "polarization": _xml_text_under_local_path(root, "acquisitionInfo", "polarisationMode")
        or _xml_text_under_local_path(root, "polarisationList", "polLayer")
        or _xml_text_under_local_path(root, "polList", "polLayer")
        or _xml_text_by_local_names(root, ["polarisationMode", "polarization", "polarisation", "polLayer"]),
        "receiving_station": _xml_text_under_local_path(root, "generationInfo", "receivingStation")
        or _xml_text_by_local_names(root, ["receivingStation"]),
        "satellite_mode": _xml_text_by_local_names(root, ["satelliteMode"]),
        "orbit_circle": _xml_text_under_local_path(root, "missionInfo", "absOrbit")
        or _xml_text_by_local_names(root, ["absOrbit", "absoluteOrbit"]),
        "relative_orbit": _xml_text_under_local_path(root, "missionInfo", "relOrbit")
        or _xml_text_by_local_names(root, ["relOrbit", "relativeOrbit"]),
        "scene_center_lon": center_lon,
        "scene_center_lat": center_lat,
        "acquisition_time_utc": start_time,
        "acquisition_stop_time_utc": stop_time,
        "product_type": _xml_text_under_local_path(root, "imageDataInfo", "imageDataType")
        or _xml_text_under_local_path(root, "orderInfo", "productVariant")
        or _xml_text_by_local_names(root, ["productType", "imageDataType", "productVariant"]),
        "image_data_format": _xml_text_under_local_path(root, "imageDataInfo", "imageDataFormat")
        or _xml_text_by_local_names(root, ["imageDataFormat"]),
        "product_level": _xml_text_by_local_names(root, ["productLevel", "itemName"]),
        "product_unique_id": _xml_text_by_local_names(root, ["logicalProductID", "sceneID", "productID"]),
        "look_direction": (_xml_text_under_local_path(root, "acquisitionInfo", "lookDirection") or "").upper() or None,
        "coverage_polygon": coverage_polygon,
    }
    return coverage_polygon, {key: value for key, value in metadata.items() if value not in (None, "", [])}


def _date_start_stop(date_yyyymmdd: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    try:
        start = datetime.strptime(date_yyyymmdd, "%Y%m%d")
    except ValueError:
        return None, None
    return start, start + timedelta(days=1)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


def _xml_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        load_dtd=False,
        no_network=True,
        huge_tree=False,
        recover=False,
    )


def _local_name(element: etree._Element) -> str:
    try:
        return etree.QName(element).localname
    except Exception:
        return str(element.tag).split("}")[-1]


def _first_text_by_local_name(root: etree._Element, names: Sequence[str]) -> Optional[str]:
    wanted = {name.lower() for name in names}
    for element in root.iter():
        if _local_name(element).lower() in wanted:
            text = (element.text or "").strip()
            if text:
                return text
    return None


def _texts_by_local_name(root: etree._Element, name: str) -> List[str]:
    wanted = name.lower()
    values: List[str] = []
    for element in root.iter():
        if _local_name(element).lower() != wanted:
            continue
        text = (element.text or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _s1_polygon_from_coordinates(text: Optional[str]) -> Optional[List[Tuple[float, float]]]:
    if not text:
        return None
    points: List[Tuple[float, float]] = []
    for token in re.split(r"\s+", text.strip()):
        if not token:
            continue
        parts = [part for part in re.split(r"[,;]", token) if part]
        if len(parts) < 2:
            continue
        try:
            first = float(parts[0])
            second = float(parts[1])
        except ValueError:
            continue

        # Sentinel-1 manifest gml:coordinates commonly stores lat,lon.
        if abs(first) > 90.0 and abs(second) <= 90.0:
            lon, lat = first, second
        else:
            lon, lat = second, first
        points.append((lon, lat))

    if len(points) < 3:
        return None
    if points[0] != points[-1]:
        points.append(points[0])
    return points


def _bbox_from_polygon(points: Optional[List[Tuple[float, float]]]) -> Optional[Tuple[float, float, float, float]]:
    if not points or len(points) < 3:
        return None
    lons = [float(point[0]) for point in points]
    lats = [float(point[1]) for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def _centroid_from_polygon(points: Optional[List[Tuple[float, float]]]) -> Tuple[Optional[float], Optional[float]]:
    if not points or len(points) < 3:
        return None, None
    try:
        poly = Polygon(points)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            return None, None
        return float(poly.centroid.x), float(poly.centroid.y)
    except Exception:
        return None, None


def _parse_s1_source_name(name: str) -> Optional[Dict[str, Any]]:
    base = _strip_known_suffix(os.path.basename(name or ""))
    match = _S1_SOURCE_RE.match(base)
    if not match:
        return None

    start_time = _parse_datetime_token(match.group("start"))
    stop_time = _parse_datetime_token(match.group("stop"))
    class_token = match.group("class").upper()
    polarization = class_token[-2:] if len(class_token) >= 2 else class_token
    absolute_orbit = match.group("absolute_orbit").lstrip("0") or match.group("absolute_orbit")

    return {
        "logical_product_uid": base,
        "satellite": match.group("satellite").upper(),
        "satellite_family": "S1",
        "source_format": "S1_ZIP" if name.lower().endswith(".zip") else "S1_SAFE_DIR",
        "product_type": match.group("product").upper(),
        "product_level": "L1",
        "imaging_mode": match.group("mode").upper(),
        "polarization": polarization,
        "absolute_orbit": absolute_orbit,
        "acquisition_start_time_utc": start_time,
        "acquisition_stop_time_utc": stop_time,
        "imaging_date": match.group("start")[:8],
        "source_product_token": class_token,
        "metadata": {
            "filename_datatake": match.group("datatake").upper(),
            "filename_product_uid": match.group("product_uid").upper(),
            "filename_absolute_orbit": match.group("absolute_orbit"),
            "filename_class_token": class_token,
        },
    }


def _parse_s1_manifest_bytes(data: bytes) -> Dict[str, Any]:
    root = etree.fromstring(data, parser=_xml_parser())
    start_time = _parse_datetime_token(_first_text_by_local_name(root, ["startTime"]))
    stop_time = _parse_datetime_token(_first_text_by_local_name(root, ["stopTime"]))
    product_type = _first_text_by_local_name(root, ["productType"])
    mode = _first_text_by_local_name(root, ["mode"])
    orbit_direction = _first_text_by_local_name(root, ["pass"])
    polarizations = _texts_by_local_name(root, "transmitterReceiverPolarisation")
    absolute_orbit = _first_text_by_local_name(root, ["orbitNumber"])
    relative_orbit = _first_text_by_local_name(root, ["relativeOrbitNumber"])
    coordinates = _first_text_by_local_name(root, ["coordinates"])
    coverage_polygon = _s1_polygon_from_coordinates(coordinates)

    values: Dict[str, Any] = {
        "manifest_start_time": start_time,
        "manifest_stop_time": stop_time,
        "manifest_product_type": product_type.strip().upper() if product_type else None,
        "manifest_mode": mode.strip().upper() if mode else None,
        "manifest_orbit_direction": orbit_direction.strip().upper() if orbit_direction else None,
        "manifest_polarizations": [item.strip().upper() for item in polarizations if item.strip()],
        "manifest_absolute_orbit": absolute_orbit.strip() if absolute_orbit else None,
        "manifest_relative_orbit": relative_orbit.strip() if relative_orbit else None,
        "coverage_polygon": coverage_polygon,
    }
    return {key: value for key, value in values.items() if value not in (None, "", [])}


def _parse_s1_zip_manifest(path: str) -> Dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        manifest_name = next(
            (name for name in archive.namelist() if name.lower().endswith("/manifest.safe") or name.lower() == "manifest.safe"),
            None,
        )
        if not manifest_name:
            return {"manifest_parse_status": "MISSING"}
        return {
            "manifest_parse_status": "OK",
            "manifest_path": manifest_name,
            **_parse_s1_manifest_bytes(archive.read(manifest_name)),
        }


def _parse_s1_safe_manifest(path: str) -> Dict[str, Any]:
    manifest_path = os.path.join(path, "manifest.safe")
    if not os.path.isfile(manifest_path):
        return {"manifest_parse_status": "MISSING"}
    with open(manifest_path, "rb") as stream:
        return {
            "manifest_parse_status": "OK",
            "manifest_path": manifest_path,
            **_parse_s1_manifest_bytes(stream.read()),
        }


def _parse_lt1_archive_metadata(path: str) -> Dict[str, Any]:
    archive_stem = _strip_known_suffix(os.path.basename(path))
    xml_member, xml_data, members = _archive_read_first_matching(
        path,
        lambda name: _archive_member_base_name(name).lower().endswith(".meta.xml"),
    )
    tiff_members = _archive_list_matching(
        path,
        lambda name: _archive_member_base_name(name).lower().endswith((".tiff", ".tif")),
        limit=8,
    )
    if not xml_member or not xml_data:
        return {
            "archive_parse_status": "MISSING_XML",
            "archive_member_count_scanned": len(members),
            "contained_tiff_members": tiff_members,
        }
    coverage_polygon, xml_meta = _parse_radar_xml_metadata_bytes(xml_data)
    return {
        "archive_parse_status": "OK",
        "archive_xml_member": xml_member,
        "archive_scene_name": _archive_member_scene_name(xml_member, archive_stem),
        "contained_tiff_members": tiff_members,
        "coverage_polygon": coverage_polygon,
        **xml_meta,
    }


def _parse_gf3_archive_metadata(path: str) -> Dict[str, Any]:
    archive_stem = _strip_known_suffix(os.path.basename(path))
    xml_member, xml_data, members = _archive_read_first_matching(
        path,
        lambda name: _archive_member_base_name(name).lower().endswith(".xml"),
    )
    quicklooks = _archive_list_matching(
        path,
        lambda name: _archive_member_base_name(name).lower().endswith((".jpg", ".jpeg", ".png", ".bmp", "_ql.tif", "_ql.tiff")),
        limit=8,
    )
    if not xml_member or not xml_data:
        return {
            "archive_parse_status": "MISSING_XML",
            "archive_member_count_scanned": len(members),
            "quicklook_members": quicklooks,
        }
    coverage_polygon, xml_meta = _parse_radar_xml_metadata_bytes(xml_data)
    return {
        "archive_parse_status": "OK",
        "archive_xml_member": xml_member,
        "archive_scene_name": _archive_member_scene_name(xml_member, archive_stem),
        "quicklook_members": quicklooks,
        "coverage_polygon": coverage_polygon,
        **xml_meta,
    }


def _parse_s1_eof_header(path: str) -> Dict[str, Any]:
    try:
        root = etree.parse(path, parser=_xml_parser()).getroot()
    except Exception as exc:
        return {"header_parse_status": "FAILED", "header_parse_error": str(exc)}

    def _time(name: str) -> Optional[datetime]:
        return _parse_datetime_token(_first_text_by_local_name(root, [name]))

    mission = _first_text_by_local_name(root, ["Mission"])
    file_type = _first_text_by_local_name(root, ["File_Type"])
    return {
        "header_parse_status": "OK",
        "header_mission": mission,
        "header_file_type": file_type,
        "header_validity_start": _time("Validity_Start"),
        "header_validity_stop": _time("Validity_Stop"),
        "header_creation_date": _time("Creation_Date"),
    }


def _parse_source_entry(path: str, root: ManagedRootORM) -> Optional[Dict[str, Any]]:
    path = _normalize_path(path)
    name = os.path.basename(path)
    lower_name = name.lower()
    stat = _stat_path(path)
    now = _utcnow()
    name_stem = _strip_known_suffix(name)

    if lower_name.endswith(".zip") and name.upper().startswith("S1"):
        name_meta = _parse_s1_source_name(name)
        if not name_meta:
            return None
        parse_status = "OK"
        parse_error = None
        manifest_meta: Dict[str, Any] = {}
        try:
            manifest_meta = _parse_s1_zip_manifest(path)
        except Exception as exc:
            parse_status = "PARTIAL"
            parse_error = str(exc)
            manifest_meta = {"manifest_parse_status": "FAILED", "manifest_parse_error": str(exc)}
        return _build_s1_source_asset(path, root, name_meta, manifest_meta, stat, parse_status, parse_error, now)

    if _has_archive_suffix(name, _LT1_ARCHIVE_EXTS) and name_stem.upper().startswith("LT1"):
        parsed = parse_lt1_radar_filename(name_stem)
        if not parsed:
            return None
        parse_status = "OK"
        parse_error = None
        archive_meta: Dict[str, Any] = {}
        try:
            archive_meta = _parse_lt1_archive_metadata(path)
            if archive_meta.get("archive_parse_status") != "OK":
                parse_status = "PARTIAL"
                parse_error = str(archive_meta.get("archive_parse_status") or "archive metadata incomplete")
        except Exception as exc:
            parse_status = "PARTIAL"
            parse_error = str(exc)
            archive_meta = {"archive_parse_status": "FAILED", "archive_parse_error": str(exc)}
        return _build_lt1_source_asset(
            path,
            root,
            parsed,
            archive_meta,
            archive_meta.get("coverage_polygon"),
            stat,
            now,
            source_format="LT1_ARCHIVE",
            archive_path=path,
            parser_name="lt1_archive_metadata",
            parse_status=parse_status,
            parse_error=parse_error,
        )

    if _has_archive_suffix(name, _GF3_ARCHIVE_EXTS) and name_stem.upper().startswith("GF3"):
        parsed = parse_gf3_l2_dirname(name_stem)
        if not parsed:
            return None
        parse_status = "OK"
        parse_error = None
        archive_meta = {}
        try:
            archive_meta = _parse_gf3_archive_metadata(path)
            if archive_meta.get("archive_parse_status") != "OK":
                parse_status = "PARTIAL"
                parse_error = str(archive_meta.get("archive_parse_status") or "archive metadata incomplete")
        except Exception as exc:
            parse_status = "PARTIAL"
            parse_error = str(exc)
            archive_meta = {"archive_parse_status": "FAILED", "archive_parse_error": str(exc)}
        return _build_gf3_archive_asset(path, root, parsed, archive_meta, stat, parse_status, parse_error, now)

    if lower_name.endswith(".safe") and os.path.isdir(path) and name.upper().startswith("S1"):
        name_meta = _parse_s1_source_name(name)
        if not name_meta:
            return None
        name_meta["source_format"] = "S1_SAFE_DIR"
        parse_status = "OK"
        parse_error = None
        manifest_meta = {}
        try:
            manifest_meta = _parse_s1_safe_manifest(path)
        except Exception as exc:
            parse_status = "PARTIAL"
            parse_error = str(exc)
            manifest_meta = {"manifest_parse_status": "FAILED", "manifest_parse_error": str(exc)}
        return _build_s1_source_asset(path, root, name_meta, manifest_meta, stat, parse_status, parse_error, now)

    if os.path.isdir(path) and name.upper().startswith("LT1"):
        parsed = parse_lt1_radar_filename(name)
        if not parsed:
            return None
        coverage_polygon = None
        xml_meta: Dict[str, Any] = {}
        xml_path = find_xml_file(path)
        if xml_path:
            try:
                coverage_polygon, parsed_xml = parse_xml_metadata(xml_path)
                if parsed_xml:
                    xml_meta = parsed_xml
            except Exception as exc:
                xml_meta = {"xml_parse_error": str(exc), "xml_path": xml_path}
        return _build_lt1_source_asset(path, root, parsed, xml_meta, coverage_polygon, stat, now)

    return None


def _build_s1_source_asset(
    path: str,
    root: ManagedRootORM,
    name_meta: Dict[str, Any],
    manifest_meta: Dict[str, Any],
    stat: Dict[str, Optional[float]],
    parse_status: str,
    parse_error: Optional[str],
    now: datetime,
) -> Dict[str, Any]:
    metadata = dict(name_meta.get("metadata") or {})
    metadata.update(manifest_meta)
    coverage_polygon = manifest_meta.get("coverage_polygon")
    centroid_lon, centroid_lat = _centroid_from_polygon(coverage_polygon)
    bbox = _bbox_from_polygon(coverage_polygon)
    metadata.update(
        {
            "coverage_polygon": coverage_polygon,
            "coverage_bbox": bbox,
            "scene_center_lon": centroid_lon,
            "scene_center_lat": centroid_lat,
        }
    )

    manifest_pols = manifest_meta.get("manifest_polarizations") or []
    if manifest_pols:
        metadata["polarization_channels"] = manifest_pols

    return {
        "asset_uid": _asset_uid("source", path),
        "logical_product_uid": name_meta.get("logical_product_uid"),
        "satellite_family": "S1",
        "satellite": name_meta.get("satellite"),
        "source_format": name_meta.get("source_format") or "S1_ZIP",
        "product_type": manifest_meta.get("manifest_product_type") or name_meta.get("product_type"),
        "product_level": name_meta.get("product_level"),
        "imaging_mode": manifest_meta.get("manifest_mode") or name_meta.get("imaging_mode"),
        "polarization": name_meta.get("polarization"),
        "absolute_orbit": manifest_meta.get("manifest_absolute_orbit") or name_meta.get("absolute_orbit"),
        "relative_orbit": manifest_meta.get("manifest_relative_orbit"),
        "orbit_direction": manifest_meta.get("manifest_orbit_direction"),
        "acquisition_start_time_utc": manifest_meta.get("manifest_start_time") or name_meta.get("acquisition_start_time_utc"),
        "acquisition_stop_time_utc": manifest_meta.get("manifest_stop_time") or name_meta.get("acquisition_stop_time_utc"),
        "imaging_date": name_meta.get("imaging_date"),
        "root_ref_id": root.id,
        "root_path": root.path,
        "file_path": path,
        "archive_path": path if path.lower().endswith(".zip") else None,
        "path_kind": _path_kind(path),
        "file_name": os.path.basename(path),
        "file_stem": _strip_known_suffix(os.path.basename(path)),
        "file_ext": os.path.splitext(path)[1].lower(),
        "size_bytes": stat.get("size_bytes"),
        "mtime_epoch": stat.get("mtime_epoch"),
        "checksum_status": "NOT_COMPUTED",
        "parser_name": "sentinel1_source_manifest",
        "parser_version": PARSER_VERSION,
        "parse_status": parse_status,
        "parse_error": parse_error,
        "parsed_at": now,
        "metadata_json": _json_safe(metadata),
        "is_active": True,
        "missing_since": None,
        "updated_at": now,
    }


def _build_lt1_source_asset(
    path: str,
    root: ManagedRootORM,
    parsed: Dict[str, Any],
    xml_meta: Dict[str, Any],
    coverage_polygon: Optional[List[Tuple[float, float]]],
    stat: Dict[str, Optional[float]],
    now: datetime,
    *,
    source_format: str = "LT1_DIR",
    archive_path: Optional[str] = None,
    parser_name: str = "lt1_source_directory",
    parse_status: str = "OK",
    parse_error: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = dict(parsed)
    metadata.update({key: value for key, value in xml_meta.items() if value not in (None, "")})
    metadata["coverage_polygon"] = coverage_polygon
    metadata["coverage_bbox"] = _bbox_from_polygon(coverage_polygon)
    centroid_lon, centroid_lat = _centroid_from_polygon(coverage_polygon)
    metadata["scene_center_lon"] = parsed.get("scene_center_lon") if parsed.get("scene_center_lon") is not None else centroid_lon
    metadata["scene_center_lat"] = parsed.get("scene_center_lat") if parsed.get("scene_center_lat") is not None else centroid_lat
    satellite = parsed.get("satellite")
    imaging_date = parsed.get("imaging_date")

    return {
        "asset_uid": _asset_uid("source", path),
        "logical_product_uid": _strip_known_suffix(os.path.basename(path)),
        "satellite_family": normalize_satellite_family(satellite),
        "satellite": satellite,
        "source_format": source_format,
        "product_type": xml_meta.get("product_type") or parsed.get("product_type"),
        "product_level": xml_meta.get("product_level") or parsed.get("product_level"),
        "imaging_mode": xml_meta.get("imaging_mode") or parsed.get("imaging_mode"),
        "polarization": xml_meta.get("polarization") or parsed.get("polarization"),
        "absolute_orbit": xml_meta.get("orbit_circle") or parsed.get("orbit_circle"),
        "relative_orbit": xml_meta.get("relative_orbit"),
        "orbit_direction": xml_meta.get("orbit_direction") or parsed.get("orbit_direction"),
        "acquisition_start_time_utc": _parse_datetime_token(xml_meta.get("acquisition_time_utc")),
        "acquisition_stop_time_utc": _parse_datetime_token(xml_meta.get("acquisition_stop_time_utc")),
        "imaging_date": imaging_date,
        "root_ref_id": root.id,
        "root_path": root.path,
        "file_path": path,
        "archive_path": archive_path,
        "path_kind": _path_kind(path),
        "file_name": os.path.basename(path),
        "file_stem": _strip_known_suffix(os.path.basename(path)),
        "file_ext": os.path.splitext(path)[1].lower(),
        "size_bytes": stat.get("size_bytes"),
        "mtime_epoch": stat.get("mtime_epoch"),
        "checksum_status": "NOT_COMPUTED",
        "parser_name": parser_name,
        "parser_version": PARSER_VERSION,
        "parse_status": parse_status,
        "parse_error": parse_error,
        "parsed_at": now,
        "metadata_json": _json_safe(metadata),
        "is_active": True,
        "missing_since": None,
        "updated_at": now,
    }


def _build_gf3_archive_asset(
    path: str,
    root: ManagedRootORM,
    parsed: Dict[str, Any],
    archive_meta: Dict[str, Any],
    stat: Dict[str, Optional[float]],
    parse_status: str,
    parse_error: Optional[str],
    now: datetime,
) -> Dict[str, Any]:
    coverage_polygon = archive_meta.get("coverage_polygon")
    metadata = dict(parsed)
    metadata.update({key: value for key, value in archive_meta.items() if value not in (None, "")})
    metadata["coverage_polygon"] = coverage_polygon
    metadata["coverage_bbox"] = _bbox_from_polygon(coverage_polygon)
    centroid_lon, centroid_lat = _centroid_from_polygon(coverage_polygon)
    metadata["scene_center_lon"] = parsed.get("scene_center_lon") if parsed.get("scene_center_lon") is not None else centroid_lon
    metadata["scene_center_lat"] = parsed.get("scene_center_lat") if parsed.get("scene_center_lat") is not None else centroid_lat
    start_time = _parse_datetime_token(archive_meta.get("acquisition_time_utc"))
    stop_time = _parse_datetime_token(archive_meta.get("acquisition_stop_time_utc"))
    imaging_date = parsed.get("imaging_date")
    if not imaging_date and start_time:
        imaging_date = start_time.strftime("%Y%m%d")
    stem = _strip_known_suffix(os.path.basename(path))

    return {
        "asset_uid": _asset_uid("source", path),
        "logical_product_uid": archive_meta.get("product_unique_id") or stem,
        "satellite_family": "GF3",
        "satellite": "GF3",
        "source_format": "GF3_ARCHIVE",
        "product_type": archive_meta.get("product_type") or parsed.get("product_type") or "L1A",
        "product_level": archive_meta.get("product_level") or parsed.get("product_level") or "L1A",
        "imaging_mode": archive_meta.get("imaging_mode") or parsed.get("imaging_mode"),
        "polarization": archive_meta.get("polarization") or parsed.get("polarization"),
        "absolute_orbit": archive_meta.get("orbit_circle") or parsed.get("orbit_circle"),
        "relative_orbit": archive_meta.get("relative_orbit"),
        "orbit_direction": archive_meta.get("orbit_direction") or parsed.get("orbit_direction"),
        "acquisition_start_time_utc": start_time,
        "acquisition_stop_time_utc": stop_time,
        "imaging_date": imaging_date,
        "root_ref_id": root.id,
        "root_path": root.path,
        "file_path": path,
        "archive_path": path,
        "path_kind": _path_kind(path),
        "file_name": os.path.basename(path),
        "file_stem": stem,
        "file_ext": os.path.splitext(path)[1].lower(),
        "size_bytes": stat.get("size_bytes"),
        "mtime_epoch": stat.get("mtime_epoch"),
        "checksum_status": "NOT_COMPUTED",
        "parser_name": "gf3_archive_metadata",
        "parser_version": PARSER_VERSION,
        "parse_status": parse_status,
        "parse_error": parse_error,
        "parsed_at": now,
        "metadata_json": _json_safe(metadata),
        "is_active": True,
        "missing_since": None,
        "updated_at": now,
    }


def _parse_orbit_entry(path: str, root: ManagedRootORM) -> Optional[Dict[str, Any]]:
    path = _normalize_path(path)
    name = os.path.basename(path)
    lower_name = name.lower()
    stat = _stat_path(path)
    now = _utcnow()

    if lower_name.endswith(".eof") and name.upper().startswith("S1"):
        match = _S1_EOF_RE.match(name)
        if not match:
            return None
        header_meta = _parse_s1_eof_header(path)
        orbit_type = match.group("orbit_type").upper()
        valid_start = header_meta.get("header_validity_start") or _parse_datetime_token(match.group("valid_start"))
        valid_stop = header_meta.get("header_validity_stop") or _parse_datetime_token(match.group("valid_stop"))
        generation_time = header_meta.get("header_creation_date") or _parse_datetime_token(match.group("generation"))
        parse_status = "OK" if header_meta.get("header_parse_status") != "FAILED" else "PARTIAL"
        quality_class = "precise" if orbit_type == "AUX_POEORB" else "restituted" if orbit_type == "AUX_RESORB" else "unknown"
        metadata = {
            "filename_provider": match.group("provider").upper(),
            "filename_generation_time": match.group("generation"),
            "filename_validity_start": match.group("valid_start"),
            "filename_validity_stop": match.group("valid_stop"),
            **header_meta,
        }
        return {
            "orbit_uid": _asset_uid("orbit", path),
            "satellite_family": "S1",
            "satellite": match.group("satellite").upper(),
            "orbit_type": orbit_type,
            "native_format": "EOF",
            "quality_class": quality_class,
            "root_ref_id": root.id,
            "root_path": root.path,
            "file_path": path,
            "file_name": name,
            "file_stem": os.path.splitext(name)[0],
            "file_ext": ".eof",
            "size_bytes": stat.get("size_bytes"),
            "mtime_epoch": stat.get("mtime_epoch"),
            "checksum_status": "NOT_COMPUTED",
            "validity_start_time_utc": valid_start,
            "validity_stop_time_utc": valid_stop,
            "generation_time_utc": generation_time,
            "published_time_utc": None,
            "parser_name": "sentinel1_eof",
            "parser_version": PARSER_VERSION,
            "parse_status": parse_status,
            "parse_error": header_meta.get("header_parse_error"),
            "parsed_at": now,
            "metadata_json": _json_safe(metadata),
            "is_active": True,
            "missing_since": None,
            "updated_at": now,
        }

    if lower_name.endswith(".txt"):
        match = _LT1_ORBIT_RE.match(name)
        if not match:
            return None
        date_text = match.group("date")
        valid_start, valid_stop = _date_start_stop(date_text)
        return {
            "orbit_uid": _asset_uid("orbit", path),
            "satellite_family": "LT1",
            "satellite": match.group("satellite").upper(),
            "orbit_type": "GPSDATA_GAS_C",
            "native_format": "TXT",
            "quality_class": "precise",
            "root_ref_id": root.id,
            "root_path": root.path,
            "file_path": path,
            "file_name": name,
            "file_stem": os.path.splitext(name)[0],
            "file_ext": ".txt",
            "size_bytes": stat.get("size_bytes"),
            "mtime_epoch": stat.get("mtime_epoch"),
            "checksum_status": "NOT_COMPUTED",
            "validity_start_time_utc": valid_start,
            "validity_stop_time_utc": valid_stop,
            "generation_time_utc": None,
            "published_time_utc": None,
            "parser_name": "lt1_gps_txt",
            "parser_version": PARSER_VERSION,
            "parse_status": "OK",
            "parse_error": None,
            "parsed_at": now,
            "metadata_json": {"orbit_date": date_text},
            "is_active": True,
            "missing_since": None,
            "updated_at": now,
        }

    return None


def _iter_source_candidates(root_path: str) -> Iterable[str]:
    stack = [_normalize_path(root_path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            name_upper = entry.name.upper()
                            if name_upper.startswith("S1") and entry.name.lower().endswith(".safe"):
                                yield _normalize_path(entry.path)
                                continue
                            if name_upper.startswith("LT1") and parse_lt1_radar_filename(entry.name):
                                yield _normalize_path(entry.path)
                                continue
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if entry.name.upper().startswith("S1") and entry.name.lower().endswith(".zip"):
                                yield _normalize_path(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue


def _iter_s1_zip_candidates(root_path: str) -> Iterable[str]:
    stack = [_normalize_path(root_path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            name_upper = entry.name.upper()
                            stem_upper = _strip_known_suffix(entry.name).upper()
                            if name_upper.startswith("S1") and entry.name.lower().endswith(".zip"):
                                yield _normalize_path(entry.path)
                                continue
                            if stem_upper.startswith("LT1") and _has_archive_suffix(entry.name, _LT1_ARCHIVE_EXTS):
                                yield _normalize_path(entry.path)
                                continue
                            if stem_upper.startswith("GF3") and _has_archive_suffix(entry.name, _GF3_ARCHIVE_EXTS):
                                yield _normalize_path(entry.path)
                                continue
                    except OSError:
                        continue
        except OSError:
            continue


def _iter_orbit_candidates(root_path: str) -> Iterable[str]:
    stack = [_normalize_path(root_path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            lower = entry.name.lower()
                            if lower.endswith(".eof") or lower.endswith(".txt"):
                                yield _normalize_path(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue


def _collect_source_assets(root: ManagedRootORM) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    entry_count = 0
    for path in _iter_source_candidates(root.path):
        entry_count += 1
        try:
            row = _parse_source_entry(path, root)
        except Exception as exc:
            row = None
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "source_parse_failed",
                    "issue_message": str(exc),
                    "source_path": path,
                }
            )
        if row is None:
            continue
        rows.append(row)
        if row.get("parse_status") in {"FAILED", "PARTIAL"}:
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "source_parse_partial" if row.get("parse_status") == "PARTIAL" else "source_parse_failed",
                    "issue_message": row.get("parse_error"),
                    "source_path": row.get("file_path"),
                }
            )
    return rows, issues, entry_count


def _collect_orbit_assets(root: ManagedRootORM) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    entry_count = 0
    for path in _iter_orbit_candidates(root.path):
        try:
            row = _parse_orbit_entry(path, root)
        except Exception as exc:
            row = None
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "orbit_parse_failed",
                    "issue_message": str(exc),
                    "source_path": path,
                }
            )
        if row is None:
            continue
        entry_count += 1
        rows.append(row)
        if row.get("parse_status") in {"FAILED", "PARTIAL"}:
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "orbit_parse_partial" if row.get("parse_status") == "PARTIAL" else "orbit_parse_failed",
                    "issue_message": row.get("parse_error"),
                    "source_path": row.get("file_path"),
                }
            )
    return rows, issues, entry_count


def _insar_source_ready(row: Dict[str, Any], coverage_polygon: Optional[List[Tuple[float, float]]]) -> Tuple[bool, Optional[str]]:
    reasons: List[str] = []
    if not coverage_polygon or len(coverage_polygon) < 3:
        reasons.append("missing_footprint")
    if not row.get("imaging_date"):
        reasons.append("missing_date")
    if not row.get("imaging_mode"):
        reasons.append("missing_imaging_mode")
    if not row.get("polarization"):
        reasons.append("missing_polarization")
    if str(row.get("product_type") or "").upper() not in {"SLC", "SSC"}:
        reasons.append("not_complex_source")
    if reasons:
        return False, ";".join(reasons)
    return True, None


def _image_data_format_for_source(row: Dict[str, Any]) -> str:
    source_format = str(row.get("source_format") or "").upper()
    if source_format in {"S1_ZIP", "LT1_ARCHIVE", "GF3_ARCHIVE"}:
        return "ARCHIVE"
    return "DIRECTORY"


class AssetInventoryService:
    async def _progress(self, task_id: Optional[str], message: str, progress: int) -> None:
        if not task_id:
            return
        await task_service.update_task(task_id, message=message, progress=max(0, min(100, int(progress))))

    async def _get_scan_roots(
        self,
        db: AsyncSession,
        *,
        inventory_types: Optional[Sequence[str]] = None,
        root_ids: Optional[Sequence[int]] = None,
    ) -> List[ManagedRootORM]:
        type_set = {str(item or "").strip().lower() for item in (inventory_types or []) if str(item or "").strip()}
        roles: List[str] = []
        if not type_set or "source_product" in type_set or "source" in type_set:
            roles.extend(
                [
                    "source_product_pool",
                    "source_pool_gf3_archive",
                ]
            )
        if not type_set or "orbit_asset" in type_set or "orbit" in type_set:
            roles.append("orbit_asset_pool")
        stmt = (
            select(ManagedRootORM)
            .where(ManagedRootORM.enabled == True)  # noqa: E712
            .where(ManagedRootORM.root_role.in_(roles))
            .order_by(ManagedRootORM.root_role.asc(), ManagedRootORM.id.asc())
        )
        if root_ids:
            stmt = stmt.where(ManagedRootORM.id.in_([int(item) for item in root_ids]))
        result = await db.execute(stmt)
        return result.scalars().all()

    async def scan_configured_roots(
        self,
        db: Optional[AsyncSession] = None,
        *,
        inventory_types: Optional[Sequence[str]] = None,
        root_ids: Optional[Sequence[int]] = None,
        bind_orbits: bool = True,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        generated_session = db is None
        if generated_session:
            db = _new_session()
        assert db is not None

        try:
            await self._progress(task_id, "Preparing source/orbit asset scan...", 2)
            roots = await self._get_scan_roots(db, inventory_types=inventory_types, root_ids=root_ids)
            results: List[Dict[str, Any]] = []
            totals = {
                "source_roots": 0,
                "orbit_roots": 0,
                "source_assets": 0,
                "orbit_assets": 0,
                "issues": 0,
                "inaccessible_roots": 0,
            }

            total_roots = len(roots)
            for index, root in enumerate(roots, start=1):
                progress = 5 + int((index - 1) / max(1, total_roots) * 75)
                await self._progress(task_id, f"Scanning {root.display_name}: {root.path}", progress)
                if root.root_role in {"source_product_pool", "source_pool_gf3_archive"}:
                    result = await self.scan_source_root(db, root)
                    totals["source_roots"] += 1
                    totals["source_assets"] += int(result.get("asset_count") or 0)
                elif root.root_role == "orbit_asset_pool":
                    result = await self.scan_orbit_root(db, root)
                    totals["orbit_roots"] += 1
                    totals["orbit_assets"] += int(result.get("asset_count") or 0)
                else:
                    continue
                totals["issues"] += int(result.get("issue_count") or 0)
                if result.get("status") == "INACCESSIBLE":
                    totals["inaccessible_roots"] += 1
                results.append(result)
                await db.commit()

            binding_summary: Dict[str, Any] = {}
            if bind_orbits:
                await self._progress(task_id, "Binding scenes to precise orbit assets...", 88)
                binding_summary = await self.bind_scene_orbits(db)
                await db.commit()

            summary = {
                "message": "Asset inventory scan completed",
                "root_count": total_roots,
                **totals,
                "binding": binding_summary,
                "results": results,
            }
            await self._progress(task_id, "Asset inventory scan completed", 100)
            return summary
        except Exception:
            if db is not None:
                await db.rollback()
            raise
        finally:
            if generated_session and db is not None:
                await db.close()

    async def scan_source_root(self, db: AsyncSession, root: ManagedRootORM) -> Dict[str, Any]:
        started_at = _utcnow()
        state = await self._ensure_state(db, root, "source_product", started_at)
        if not os.path.isdir(root.path):
            await self._finish_state(
                db,
                state,
                status="INACCESSIBLE",
                started_at=started_at,
                entry_count=0,
                asset_count=0,
                issue_count=1,
                error=f"Source product root is not accessible: {root.path}",
            )
            await self._replace_root_issues(
                db,
                root,
                "source_product",
                [
                    {
                        "severity": "error",
                        "issue_code": "root_inaccessible",
                        "issue_message": f"Source product root is not accessible: {root.path}",
                        "source_path": root.path,
                    }
                ],
            )
            return {"root_id": root.id, "inventory_type": "source_product", "status": "INACCESSIBLE", "asset_count": 0, "issue_count": 1}

        rows, issues, entry_count = await asyncio.to_thread(_collect_source_assets, root)
        seen_paths = [row["file_path"] for row in rows]
        now = _utcnow()
        for row in rows:
            stmt = pg_insert(SourceProductAssetORM).values(row)
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["file_path"],
                set_={
                    "asset_uid": excluded.asset_uid,
                    "logical_product_uid": excluded.logical_product_uid,
                    "satellite_family": excluded.satellite_family,
                    "satellite": excluded.satellite,
                    "source_format": excluded.source_format,
                    "product_type": excluded.product_type,
                    "product_level": excluded.product_level,
                    "imaging_mode": excluded.imaging_mode,
                    "polarization": excluded.polarization,
                    "absolute_orbit": excluded.absolute_orbit,
                    "relative_orbit": excluded.relative_orbit,
                    "orbit_direction": excluded.orbit_direction,
                    "acquisition_start_time_utc": excluded.acquisition_start_time_utc,
                    "acquisition_stop_time_utc": excluded.acquisition_stop_time_utc,
                    "imaging_date": excluded.imaging_date,
                    "root_ref_id": excluded.root_ref_id,
                    "root_path": excluded.root_path,
                    "archive_path": excluded.archive_path,
                    "path_kind": excluded.path_kind,
                    "file_name": excluded.file_name,
                    "file_stem": excluded.file_stem,
                    "file_ext": excluded.file_ext,
                    "size_bytes": excluded.size_bytes,
                    "mtime_epoch": excluded.mtime_epoch,
                    "checksum_status": excluded.checksum_status,
                    "parser_name": excluded.parser_name,
                    "parser_version": excluded.parser_version,
                    "parse_status": excluded.parse_status,
                    "parse_error": excluded.parse_error,
                    "parsed_at": excluded.parsed_at,
                    "metadata_json": excluded.metadata_json,
                    "is_active": True,
                    "missing_since": None,
                    "updated_at": now,
                },
            )
            await db.execute(stmt)
        await db.flush()

        asset_ids_by_path: Dict[str, int] = {}
        if seen_paths:
            result = await db.execute(
                select(SourceProductAssetORM.file_path, SourceProductAssetORM.id).where(SourceProductAssetORM.file_path.in_(seen_paths))
            )
            asset_ids_by_path = {str(path): int(asset_id) for path, asset_id in result.all()}
            await self._upsert_radar_records_for_source_assets(db, rows, asset_ids_by_path)

        await self._mark_missing_source_assets(db, root, seen_paths, now)
        await self._replace_root_issues(db, root, "source_product", issues)
        await self._finish_state(
            db,
            state,
            status="OK" if not any(item.get("severity") == "error" for item in issues) else "WARNING",
            started_at=started_at,
            entry_count=entry_count,
            asset_count=len(rows),
            issue_count=len(issues),
            error=None,
        )
        return {
            "root_id": root.id,
            "root_path": root.path,
            "inventory_type": "source_product",
            "status": state.status,
            "entry_count": entry_count,
            "asset_count": len(rows),
            "issue_count": len(issues),
        }

    async def scan_orbit_root(self, db: AsyncSession, root: ManagedRootORM) -> Dict[str, Any]:
        started_at = _utcnow()
        state = await self._ensure_state(db, root, "orbit_asset", started_at)
        if not os.path.isdir(root.path):
            await self._finish_state(
                db,
                state,
                status="INACCESSIBLE",
                started_at=started_at,
                entry_count=0,
                asset_count=0,
                issue_count=1,
                error=f"Orbit asset root is not accessible: {root.path}",
            )
            await self._replace_root_issues(
                db,
                root,
                "orbit_asset",
                [
                    {
                        "severity": "error",
                        "issue_code": "root_inaccessible",
                        "issue_message": f"Orbit asset root is not accessible: {root.path}",
                        "source_path": root.path,
                    }
                ],
            )
            return {"root_id": root.id, "inventory_type": "orbit_asset", "status": "INACCESSIBLE", "asset_count": 0, "issue_count": 1}

        rows, issues, entry_count = await asyncio.to_thread(_collect_orbit_assets, root)
        seen_paths = [row["file_path"] for row in rows]
        now = _utcnow()
        for row in rows:
            stmt = pg_insert(OrbitAssetORM).values(row)
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["file_path"],
                set_={
                    "orbit_uid": excluded.orbit_uid,
                    "satellite_family": excluded.satellite_family,
                    "satellite": excluded.satellite,
                    "orbit_type": excluded.orbit_type,
                    "native_format": excluded.native_format,
                    "quality_class": excluded.quality_class,
                    "root_ref_id": excluded.root_ref_id,
                    "root_path": excluded.root_path,
                    "file_name": excluded.file_name,
                    "file_stem": excluded.file_stem,
                    "file_ext": excluded.file_ext,
                    "size_bytes": excluded.size_bytes,
                    "mtime_epoch": excluded.mtime_epoch,
                    "checksum_status": excluded.checksum_status,
                    "validity_start_time_utc": excluded.validity_start_time_utc,
                    "validity_stop_time_utc": excluded.validity_stop_time_utc,
                    "generation_time_utc": excluded.generation_time_utc,
                    "published_time_utc": excluded.published_time_utc,
                    "parser_name": excluded.parser_name,
                    "parser_version": excluded.parser_version,
                    "parse_status": excluded.parse_status,
                    "parse_error": excluded.parse_error,
                    "parsed_at": excluded.parsed_at,
                    "metadata_json": excluded.metadata_json,
                    "is_active": True,
                    "missing_since": None,
                    "updated_at": now,
                },
            )
            await db.execute(stmt)

        await self._mark_missing_orbit_assets(db, root, seen_paths, now)
        await self._replace_root_issues(db, root, "orbit_asset", issues)
        await self._finish_state(
            db,
            state,
            status="OK" if not any(item.get("severity") == "error" for item in issues) else "WARNING",
            started_at=started_at,
            entry_count=entry_count,
            asset_count=len(rows),
            issue_count=len(issues),
            error=None,
        )
        return {
            "root_id": root.id,
            "root_path": root.path,
            "inventory_type": "orbit_asset",
            "status": state.status,
            "entry_count": entry_count,
            "asset_count": len(rows),
            "issue_count": len(issues),
        }

    async def _find_source_asset_root(
        self,
        db: AsyncSession,
        target_path: str,
    ) -> Optional[ManagedRootORM]:
        target_norm = os.path.normcase(_normalize_path(target_path))
        result = await db.execute(
            select(ManagedRootORM)
            .where(ManagedRootORM.enabled == True)  # noqa: E712
            .where(ManagedRootORM.root_role == "source_product_pool")
            .order_by(func.length(ManagedRootORM.path).desc())
        )
        for root in result.scalars().all():
            root_norm = os.path.normcase(_normalize_path(root.path))
            if target_norm == root_norm or target_norm.startswith(root_norm + os.sep):
                return root
        return None

    async def ensure_source_root_for_path(
        self,
        db: AsyncSession,
        root_path: str,
        *,
        source_ref: str = "SENTINEL1_STORAGE_DIRS",
    ) -> ManagedRootORM:
        from .root_registry_service import root_registry_service

        root = await self._find_source_asset_root(db, root_path)
        if root is not None:
            return root

        await root_registry_service.sync_from_settings(db)
        root = await self._find_source_asset_root(db, root_path)
        if root is not None:
            return root

        normalized = _normalize_path(root_path)
        root_code = f"source_product_pool__sentinel1_storage_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]}"
        root = ManagedRootORM(
            root_code=root_code,
            root_role="source_product_pool",
            display_name="Sentinel-1 Storage Pool",
            path=normalized,
            path_kind=_path_kind(normalized),
            source_kind="env",
            source_ref=source_ref,
            scan_mode="file_pool",
            enabled=True,
            exists_flag=os.path.exists(normalized),
            metadata_json={
                "env_var": source_ref,
                "created_by": "sentinel1_unpack",
            },
        )
        db.add(root)
        await db.flush()
        return root

    async def scan_source_path_after_unpack(
        self,
        db: AsyncSession,
        source_path: str,
        *,
        bind_orbits: bool = True,
    ) -> Dict[str, Any]:
        path = _normalize_path(source_path)
        root = await self._find_source_asset_root(db, path)
        if root is None:
            return {
                "scanned": False,
                "reason": "Sentinel-1 storage directory is not registered as a source product pool.",
                "source_path": path,
            }

        row = await asyncio.to_thread(_parse_source_entry, path, root)
        if row is None:
            return {
                "scanned": False,
                "reason": "Unpacked Sentinel-1 SAFE could not be parsed.",
                "source_path": path,
                "root_id": root.id,
            }

        now = _utcnow()
        stmt = pg_insert(SourceProductAssetORM).values(row)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=["file_path"],
            set_={
                "asset_uid": excluded.asset_uid,
                "logical_product_uid": excluded.logical_product_uid,
                "satellite_family": excluded.satellite_family,
                "satellite": excluded.satellite,
                "source_format": excluded.source_format,
                "product_type": excluded.product_type,
                "product_level": excluded.product_level,
                "imaging_mode": excluded.imaging_mode,
                "polarization": excluded.polarization,
                "absolute_orbit": excluded.absolute_orbit,
                "relative_orbit": excluded.relative_orbit,
                "orbit_direction": excluded.orbit_direction,
                "acquisition_start_time_utc": excluded.acquisition_start_time_utc,
                "acquisition_stop_time_utc": excluded.acquisition_stop_time_utc,
                "imaging_date": excluded.imaging_date,
                "root_ref_id": excluded.root_ref_id,
                "root_path": excluded.root_path,
                "archive_path": excluded.archive_path,
                "path_kind": excluded.path_kind,
                "file_name": excluded.file_name,
                "file_stem": excluded.file_stem,
                "file_ext": excluded.file_ext,
                "size_bytes": excluded.size_bytes,
                "mtime_epoch": excluded.mtime_epoch,
                "checksum_status": excluded.checksum_status,
                "parser_name": excluded.parser_name,
                "parser_version": excluded.parser_version,
                "parse_status": excluded.parse_status,
                "parse_error": excluded.parse_error,
                "parsed_at": excluded.parsed_at,
                "metadata_json": excluded.metadata_json,
                "is_active": True,
                "missing_since": None,
                "updated_at": now,
            },
        )
        await db.execute(stmt)
        await db.flush()

        result = await db.execute(
            select(SourceProductAssetORM.id).where(SourceProductAssetORM.file_path == path)
        )
        asset_id = result.scalar_one_or_none()
        radar_data_id = None
        if asset_id is not None:
            await self._upsert_radar_records_for_source_assets(db, [row], {path: int(asset_id)})
            radar_result = await db.execute(
                select(RadarDataORM.id).where(RadarDataORM.file_path == path)
            )
            radar_data_id = radar_result.scalar_one_or_none()
        binding_summary: Dict[str, Any] = {}
        if bind_orbits and radar_data_id is not None:
            binding_summary = await self.bind_scene_orbits(db, radar_data_ids=[int(radar_data_id)])
        return {
            "scanned": True,
            "root_id": root.id,
            "root_path": root.path,
            "asset_id": asset_id,
            "radar_data_id": radar_data_id,
            "parse_status": row.get("parse_status"),
            "binding": binding_summary,
        }

    def unpack_sentinel1_archive(
        self,
        archive_path: str,
        *,
        target_root: Optional[str] = None,
        overwrite: bool = False,
        min_disk_space_gb: Optional[float] = None,
        tmp_suffix: Optional[str] = None,
        delete_archive: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Dict[str, Any]:
        archive = _normalize_path(archive_path)
        if not os.path.isfile(archive):
            raise FileNotFoundError(archive)
        if not archive.lower().endswith(".zip"):
            raise ValueError("Only Sentinel-1 ZIP archives can be unpacked.")

        target_dir = _target_root_for_s1_archive(archive, target_root)
        os.makedirs(target_dir, exist_ok=True)
        tmp_suffix_text = str(tmp_suffix or os.getenv("UNPACK_TMP_SUFFIX") or ".unpack_tmp").strip() or ".unpack_tmp"
        min_free_gb = min_disk_space_gb
        if min_free_gb is None:
            min_free_gb = _parse_float(os.getenv("UNPACK_MIN_DISK_SPACE_GB"), 50.0)
        should_delete_archive = (
            bool(delete_archive)
            if delete_archive is not None
            else _parse_bool(os.getenv("UNPACK_DELETE_ARCHIVE"), False)
        )

        def _log(level: str, message: str) -> None:
            if log_callback:
                log_callback(level, message)

        def _progress(progress: int, message: str) -> None:
            if progress_callback:
                progress_callback(progress, message)

        _progress(3, "Reading Sentinel-1 ZIP manifest...")
        with zipfile.ZipFile(archive) as zip_obj:
            names = zip_obj.namelist()
            if not names:
                raise ValueError("ZIP archive is empty.")
            safe_dirs = {
                name.split("/", 1)[0]
                for name in names
                if "/" in name and name.split("/", 1)[0].lower().endswith(".safe")
            }
            if len(safe_dirs) != 1:
                raise ValueError("Expected exactly one top-level .SAFE directory in Sentinel-1 ZIP.")
            safe_name = next(iter(safe_dirs))
            output_safe_dir = _normalize_path(os.path.join(target_dir, safe_name))
            tmp_dir = output_safe_dir + tmp_suffix_text
            lock_path = output_safe_dir + ".unpacking"
            target_root_abs = os.path.abspath(target_dir)
            output_abs = os.path.abspath(output_safe_dir)
            if not output_abs.startswith(target_root_abs + os.sep):
                raise ValueError("Unsafe Sentinel-1 target path.")
            for member in names:
                member_target = os.path.abspath(os.path.join(target_dir, member))
                if not member_target.startswith(target_root_abs + os.sep):
                    raise ValueError(f"Unsafe ZIP member path: {member}")
            required_bytes = sum(max(0, int(info.file_size or 0)) for info in zip_obj.infolist())

            _, _, free_bytes = shutil.disk_usage(target_dir)
            min_free_bytes = int(float(min_free_gb or 0) * (1024 ** 3))
            if free_bytes - required_bytes < min_free_bytes:
                raise OSError(
                    "Sentinel-1 storage has insufficient free space: "
                    f"needed {required_bytes / (1024 ** 3):.2f} GB, "
                    f"free {free_bytes / (1024 ** 3):.2f} GB, "
                    f"min free after {float(min_free_gb or 0):.2f} GB"
                )

            if os.path.exists(output_safe_dir):
                if not overwrite:
                    return {
                        "status": "EXISTS",
                        "archive_path": archive,
                        "target_root": target_dir,
                        "safe_dir": output_safe_dir,
                        "extracted": False,
                        "member_count": len(names),
                    }
                shutil.rmtree(output_safe_dir)
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir)
            if os.path.exists(lock_path):
                raise OSError(f"Sentinel-1 unpack lock exists: {lock_path}")

            os.makedirs(tmp_dir, exist_ok=True)
            with open(lock_path, "w", encoding="utf-8") as stream:
                stream.write(_utcnow().isoformat())

            try:
                total_members = len(names)
                for index, member in enumerate(names, start=1):
                    if index % 100 == 0 or index == total_members:
                        pct = 5 + int(index / max(1, total_members) * 85)
                        _progress(pct, f"Extracting Sentinel-1 SAFE ({index}/{total_members})")
                    rel_member = member.split("/", 1)[1] if "/" in member else ""
                    if not rel_member:
                        continue
                    destination = os.path.abspath(os.path.join(tmp_dir, rel_member))
                    if not destination.startswith(os.path.abspath(tmp_dir) + os.sep):
                        raise ValueError(f"Unsafe ZIP member path: {member}")
                    info = zip_obj.getinfo(member)
                    if info.is_dir():
                        os.makedirs(destination, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with zip_obj.open(info, "r") as source, open(destination, "wb") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)

                if not os.listdir(tmp_dir):
                    raise OSError("Extracted SAFE directory is empty.")
                os.replace(tmp_dir, output_safe_dir)
            finally:
                if os.path.exists(lock_path):
                    try:
                        os.remove(lock_path)
                    except OSError:
                        pass
                if os.path.exists(tmp_dir):
                    try:
                        shutil.rmtree(tmp_dir)
                    except OSError:
                        pass

        if should_delete_archive:
            os.remove(archive)
            _log("INFO", f"Deleted Sentinel-1 ZIP after unpack: {archive}")

        _progress(92, "Sentinel-1 SAFE extracted.")
        return {
            "status": "EXTRACTED",
            "archive_path": archive,
            "target_root": target_dir,
            "safe_dir": output_safe_dir,
            "extracted": True,
            "member_count": len(names),
        }

    def materialize_source_asset(
        self,
        asset: SourceProductAssetORM,
        *,
        target_root: Optional[str] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        source_format = str(asset.source_format or "").upper()
        source_path = _normalize_path(str(asset.archive_path or asset.file_path or ""))
        if not source_path:
            raise ValueError("Source asset path is empty.")
        if source_format == "S1_ZIP":
            return self.unpack_sentinel1_archive(source_path, target_root=target_root, overwrite=overwrite)
        if source_format not in {"LT1_ARCHIVE", "GF3_ARCHIVE"}:
            if os.path.isdir(source_path):
                return {
                    "status": "DIRECTORY_READY",
                    "source_path": source_path,
                    "target_dir": source_path,
                    "extracted": False,
                    "source_format": source_format,
                }
            raise ValueError(f"Source format is not materializable from archive: {source_format}")

        requested_root = _normalize_path(target_root or "")
        if not requested_root:
            requested_root = _normalize_path(os.path.join(settings.PYINT_WORK_ROOT, "source_materialized", source_format.lower()))
        scene_name = _strip_known_suffix(os.path.basename(source_path))
        target_dir = os.path.join(requested_root, scene_name)
        result = _extract_archive_to_dir(source_path, target_dir, overwrite=overwrite)
        result["source_format"] = source_format
        return result

    async def run_sentinel1_unpack_task(self, task_id: str, payload: Optional[Dict[str, Any]] = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        asset_id = payload.get("asset_id")
        if not asset_id:
            raise ValueError("asset_id is required.")

        await task_service.start_task(task_id, message="Sentinel-1 unpack started")

        async with _new_session() as db:
            asset = await db.get(SourceProductAssetORM, int(asset_id))
            if asset is None:
                raise ValueError("Source product asset not found.")
            if asset.source_format != "S1_ZIP":
                raise ValueError("Only Sentinel-1 ZIP assets can be unpacked.")

            archive_path = asset.file_path
            target_root = payload.get("target_root") or None
            overwrite = bool(payload.get("overwrite", False))
            min_disk_space_gb = payload.get("min_disk_space_gb")
            delete_archive = payload.get("delete_archive") if "delete_archive" in payload else None
            tmp_suffix = payload.get("tmp_suffix") or os.getenv("UNPACK_TMP_SUFFIX") or ".unpack_tmp"

            loop = asyncio.get_running_loop()

            def _log(level: str, message: str) -> None:
                async def _add() -> None:
                    await task_service.add_log(task_id, level, message)

                asyncio.run_coroutine_threadsafe(_add(), loop)

            def _progress(progress: int, message: str) -> None:
                async def _update() -> None:
                    await task_service.update_task(task_id, progress=progress, message=message)

                asyncio.run_coroutine_threadsafe(_update(), loop)

            result = await asyncio.to_thread(
                self.unpack_sentinel1_archive,
                archive_path,
                target_root=target_root,
                overwrite=overwrite,
                min_disk_space_gb=min_disk_space_gb,
                tmp_suffix=tmp_suffix,
                delete_archive=delete_archive,
                progress_callback=_progress,
                log_callback=_log,
            )

            if result.get("target_root"):
                await self.ensure_source_root_for_path(db, str(result["target_root"]))
                await db.commit()

            metadata = dict(asset.metadata_json or {})
            metadata["last_unpacked_safe_dir"] = result.get("safe_dir")
            metadata["last_unpacked_target_root"] = result.get("target_root")
            metadata["last_unpacked_at"] = _utcnow().isoformat()
            metadata["last_unpacked_status"] = result.get("status")
            asset.metadata_json = metadata
            await db.commit()

            scan_summary: Dict[str, Any] = {}
            if result.get("safe_dir"):
                await task_service.update_task(task_id, progress=94, message="Scanning unpacked Sentinel-1 SAFE...")
                scan_summary = await self.scan_source_path_after_unpack(db, str(result["safe_dir"]), bind_orbits=True)
                await db.commit()
            await task_service.add_log(task_id, "INFO", f"Sentinel-1 unpack result: {result}")
            if scan_summary:
                await task_service.add_log(task_id, "INFO", f"Sentinel-1 SAFE inventory scan: {scan_summary}")

            await task_service.update_task(
                task_id,
                status="COMPLETED",
                progress=100,
                message=(
                    "Sentinel-1 unpack complete: "
                    f"{os.path.basename(str(result.get('safe_dir') or '')) or result.get('status')}"
                ),
            )

    async def run_sentinel1_unpack_batch_task(self, task_id: str, payload: Optional[Dict[str, Any]] = None) -> None:
        payload = payload if isinstance(payload, dict) else {}
        overwrite = bool(payload.get("overwrite", False))
        min_disk_space_gb = payload.get("min_disk_space_gb")
        delete_archive = payload.get("delete_archive") if "delete_archive" in payload else None
        target_root = payload.get("target_root") or None
        scan_before_unpack = bool(payload.get("scan_before_unpack", True))

        await task_service.start_task(task_id, message="Sentinel-1 batch unpack started")

        async with _new_session() as db:
            if scan_before_unpack:
                await task_service.update_task(task_id, progress=5, message="Refreshing Sentinel-1 inventory...")
                await self.scan_configured_roots(
                    db,
                    inventory_types=["source_product", "orbit_asset"],
                    bind_orbits=True,
                    task_id=task_id,
                )
                await db.commit()

            archive_dirs = _configured_sentinel1_archive_dirs()
            archives: List[Dict[str, Any]] = []
            seen_archives: set[str] = set()
            for archive_dir in archive_dirs:
                if not archive_dir or not os.path.isdir(archive_dir):
                    continue
                for archive_path in _iter_s1_zip_candidates(archive_dir):
                    normalized_path = _normalize_path(archive_path)
                    if not normalized_path or normalized_path in seen_archives:
                        continue
                    seen_archives.add(normalized_path)
                    name_meta = _parse_s1_source_name(os.path.basename(normalized_path)) or {}
                    archives.append(
                        {
                            "file_path": normalized_path,
                            "logical_product_uid": name_meta.get("logical_product_uid"),
                        }
                    )
            archives.sort(key=lambda item: (str(item.get("logical_product_uid") or ""), str(item.get("file_path") or "")))
            if not archives:
                raise ValueError("No Sentinel-1 ZIP archives were found in SOURCE_PRODUCT_DIRS.")

            loop = asyncio.get_running_loop()

            def _log(level: str, message: str) -> None:
                async def _add() -> None:
                    await task_service.add_log(task_id, level, message)

                asyncio.run_coroutine_threadsafe(_add(), loop)

            processed = 0
            skipped = 0
            failed = 0
            total = len(archives)

            for index, archive_item in enumerate(archives, start=1):
                archive_path = str(archive_item.get("file_path") or "")
                logical_product_uid = str(archive_item.get("logical_product_uid") or "").strip()
                asset_name = os.path.basename(archive_path or logical_product_uid or f"archive-{index}")
                await task_service.update_task(
                    task_id,
                    progress=10 + int((index - 1) / max(1, total) * 80),
                    message=f"Processing Sentinel-1 archive {index}/{total}: {asset_name}",
                )

                if await self._s1_zip_has_unpacked_safe(
                    db,
                    {
                        "source_format": "S1_ZIP",
                        "logical_product_uid": logical_product_uid,
                    },
                ) and not overwrite:
                    skipped += 1
                    await task_service.add_log(task_id, "INFO", f"Skipping already unpacked Sentinel-1 archive: {archive_path}")
                    continue

                try:
                    result = await asyncio.to_thread(
                        self.unpack_sentinel1_archive,
                        archive_path,
                        target_root=target_root,
                        overwrite=overwrite,
                        min_disk_space_gb=min_disk_space_gb,
                        delete_archive=delete_archive,
                        log_callback=_log,
                    )
                    if result.get("target_root"):
                        await self.ensure_source_root_for_path(db, str(result["target_root"]))
                        await db.commit()

                    scan_summary: Dict[str, Any] = {}
                    if result.get("safe_dir"):
                        await task_service.update_task(
                            task_id,
                            progress=10 + int((index - 1) / max(1, total) * 80) + 3,
                            message=f"Scanning unpacked Sentinel-1 SAFE {index}/{total}...",
                        )
                        scan_summary = await self.scan_source_path_after_unpack(db, str(result["safe_dir"]), bind_orbits=True)
                        await db.commit()

                    await task_service.add_log(task_id, "INFO", f"Sentinel-1 unpack result: {result}")
                    if scan_summary:
                        await task_service.add_log(task_id, "INFO", f"Sentinel-1 SAFE inventory scan: {scan_summary}")
                    if result.get("status") == "EXISTS":
                        skipped += 1
                    else:
                        processed += 1
                except Exception as exc:
                    failed += 1
                    await task_service.add_log(task_id, "ERROR", f"Sentinel-1 archive unpack failed: {archive_path} -> {exc}")

            status = "COMPLETED" if failed < total else "FAILED"
            message = (
                "Sentinel-1 batch unpack complete: "
                f"processed={processed}, skipped={skipped}, failed={failed}, total={total}"
            )
            await task_service.update_task(task_id, status=status, progress=100, message=message)

    async def _ensure_state(
        self,
        db: AsyncSession,
        root: ManagedRootORM,
        inventory_type: str,
        started_at: datetime,
    ) -> AssetInventoryStateORM:
        result = await db.execute(
            select(AssetInventoryStateORM).where(
                AssetInventoryStateORM.root_ref_id == root.id,
                AssetInventoryStateORM.inventory_type == inventory_type,
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = AssetInventoryStateORM(
                root_ref_id=root.id,
                inventory_type=inventory_type,
                root_path=root.path,
                scan_mode=root.scan_mode,
                status="RUNNING",
                parser_version=PARSER_VERSION,
                needs_rescan=True,
                metadata_json={"created_by": "asset_inventory_scan"},
            )
            db.add(state)
            await db.flush()
        state.status = "RUNNING"
        state.root_path = root.path
        state.scan_mode = root.scan_mode
        state.last_scan_started_at = started_at
        state.last_error = None
        state.parser_version = PARSER_VERSION
        state.updated_at = started_at
        return state

    async def _finish_state(
        self,
        db: AsyncSession,
        state: AssetInventoryStateORM,
        *,
        status: str,
        started_at: datetime,
        entry_count: int,
        asset_count: int,
        issue_count: int,
        error: Optional[str],
    ) -> None:
        state.status = status
        state.last_scan_started_at = started_at
        state.last_scan_finished_at = _utcnow()
        state.last_seen_entry_count = int(entry_count)
        state.last_asset_count = int(asset_count)
        state.last_issue_count = int(issue_count)
        state.parser_version = PARSER_VERSION
        state.needs_rescan = status not in {"OK", "WARNING"}
        state.last_error = error
        state.updated_at = _utcnow()
        db.add(state)

    async def _replace_root_issues(
        self,
        db: AsyncSession,
        root: ManagedRootORM,
        inventory_type: str,
        issues: Sequence[Dict[str, Any]],
    ) -> None:
        now = _utcnow()
        await db.execute(
            update(AssetInventoryIssueORM)
            .where(
                AssetInventoryIssueORM.root_ref_id == root.id,
                AssetInventoryIssueORM.inventory_type == inventory_type,
                AssetInventoryIssueORM.status == "OPEN",
            )
            .values(status="RESOLVED", resolved_at=now, last_seen_at=now)
        )
        for issue in issues:
            db.add(
                AssetInventoryIssueORM(
                    root_ref_id=root.id,
                    inventory_type=inventory_type,
                    severity=str(issue.get("severity") or "warning").lower(),
                    issue_code=str(issue.get("issue_code") or "unknown"),
                    issue_message=issue.get("issue_message"),
                    source_path=issue.get("source_path"),
                    status="OPEN",
                    first_seen_at=now,
                    last_seen_at=now,
                    metadata_json=issue.get("metadata_json"),
                )
            )

    async def _mark_missing_source_assets(
        self,
        db: AsyncSession,
        root: ManagedRootORM,
        seen_paths: Sequence[str],
        now: datetime,
    ) -> None:
        stmt = update(SourceProductAssetORM).where(SourceProductAssetORM.root_ref_id == root.id)
        if seen_paths:
            stmt = stmt.where(SourceProductAssetORM.file_path.notin_(list(seen_paths)))
        await db.execute(stmt.values(is_active=False, missing_since=now, updated_at=now))

    async def _mark_missing_orbit_assets(
        self,
        db: AsyncSession,
        root: ManagedRootORM,
        seen_paths: Sequence[str],
        now: datetime,
    ) -> None:
        stmt = update(OrbitAssetORM).where(OrbitAssetORM.root_ref_id == root.id)
        if seen_paths:
            stmt = stmt.where(OrbitAssetORM.file_path.notin_(list(seen_paths)))
        await db.execute(stmt.values(is_active=False, missing_since=now, updated_at=now))

    async def _upsert_radar_records_for_source_assets(
        self,
        db: AsyncSession,
        rows: Sequence[Dict[str, Any]],
        asset_ids_by_path: Dict[str, int],
    ) -> None:
        dirty_scene_ids: List[int] = []
        for row in rows:
            metadata = dict(row.get("metadata_json") or {})
            coverage_polygon = metadata.get("coverage_polygon")
            if not coverage_polygon or len(coverage_polygon) < 3:
                continue
            family = normalize_satellite_family(row.get("satellite_family") or row.get("satellite"))
            if family not in {"S1", "LT1"}:
                continue

            bbox = _bbox_from_polygon(coverage_polygon)
            if not bbox:
                continue
            try:
                poly = Polygon(coverage_polygon)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty:
                    continue
            except Exception:
                continue

            asset_id = asset_ids_by_path.get(str(row.get("file_path")))
            if not asset_id:
                continue
            if await self._s1_zip_has_unpacked_safe(db, row):
                continue
            archive_asset_id = await self._resolve_archive_asset_id_for_source_row(db, row, asset_id)
            center_lon, center_lat = _centroid_from_polygon(coverage_polygon)
            metadata_center_lon = metadata.get("scene_center_lon")
            metadata_center_lat = metadata.get("scene_center_lat")
            if metadata_center_lon is not None:
                center_lon = metadata_center_lon
            if metadata_center_lat is not None:
                center_lat = metadata_center_lat

            ready, reason = _insar_source_ready(row, coverage_polygon)
            radar_values = {
                "satellite": row.get("satellite") or "",
                "satellite_family": family,
                "imaging_date": row.get("imaging_date") or "",
                "imaging_mode": row.get("imaging_mode") or "",
                "orbit_direction": row.get("orbit_direction"),
                "polarization": row.get("polarization") or "",
                "satellite_mode": metadata.get("satellite_mode"),
                "receiving_station": metadata.get("receiving_station"),
                "orbit_circle": row.get("absolute_orbit"),
                "scene_center_lon": center_lon,
                "scene_center_lat": center_lat,
                "acquisition_time_utc": (
                    row.get("acquisition_start_time_utc").isoformat()
                    if row.get("acquisition_start_time_utc")
                    else None
                ),
                "product_type": row.get("product_type"),
                "source_product_token": metadata.get("filename_class_token") or metadata.get("source_product_token"),
                "image_data_type": "COMPLEX",
                "image_data_format": _image_data_format_for_source(row),
                "product_variant": metadata.get("product_variant"),
                "product_level": row.get("product_level"),
                "product_unique_id": row.get("logical_product_uid"),
                "look_direction": metadata.get("look_direction"),
                "acquisition_start_time_utc": row.get("acquisition_start_time_utc"),
                "acquisition_stop_time_utc": row.get("acquisition_stop_time_utc"),
                "absolute_orbit": row.get("absolute_orbit"),
                "relative_orbit": row.get("relative_orbit"),
                "source_format": row.get("source_format"),
                "source_product_ref_id": asset_id,
                "source_archive_asset_id": archive_asset_id,
                "metadata_json": _json_safe(metadata),
                "geocoded_flag": False,
                "insar_source_ready": ready,
                "insar_source_reason": reason,
                "file_path": row.get("file_path"),
                "coverage_polygon": coverage_polygon,
                "geom": from_shape(poly, srid=4326),
                "min_lon": bbox[0],
                "min_lat": bbox[1],
                "max_lon": bbox[2],
                "max_lat": bbox[3],
            }

            result = await db.execute(
                self._radar_record_match_stmt(row, asset_id, archive_asset_id)
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                db.add(
                    RadarDataORM(
                        unique_id=f"asset:{row.get('asset_uid')}",
                        has_orbit_data=False,
                        orbit_binding_status="UNBOUND",
                        is_envi_processed=False,
                        **radar_values,
                    )
                )
            else:
                before_orbit_id = existing.selected_orbit_asset_id
                for key, value in radar_values.items():
                    setattr(existing, key, value)
                if not existing.orbit_binding_status:
                    existing.orbit_binding_status = "UNBOUND"
                db.add(existing)
                if existing.id is not None and before_orbit_id != existing.selected_orbit_asset_id:
                    dirty_scene_ids.append(int(existing.id))

        await db.flush()
        if dirty_scene_ids:
            await pairing_state_service.mark_scenes_dirty(db, scene_ids=dirty_scene_ids, reason="asset_inventory_source_update", commit=False)

    async def _s1_zip_has_unpacked_safe(self, db: AsyncSession, row: Dict[str, Any]) -> bool:
        if row.get("source_format") != "S1_ZIP":
            return False
        logical_uid = str(row.get("logical_product_uid") or "").strip()
        if not logical_uid:
            return False
        result = await db.execute(
            select(func.count(SourceProductAssetORM.id)).where(
                SourceProductAssetORM.satellite_family == "S1",
                SourceProductAssetORM.source_format == "S1_SAFE_DIR",
                SourceProductAssetORM.logical_product_uid == logical_uid,
                SourceProductAssetORM.is_active == True,  # noqa: E712
            )
        )
        return int(result.scalar_one() or 0) > 0

    async def _resolve_archive_asset_id_for_source_row(
        self,
        db: AsyncSession,
        row: Dict[str, Any],
        fallback_asset_id: int,
    ) -> Optional[int]:
        if row.get("source_format") == "S1_ZIP":
            return fallback_asset_id
        if row.get("source_format") != "S1_SAFE_DIR":
            return None
        logical_uid = str(row.get("logical_product_uid") or "").strip()
        if not logical_uid:
            return None
        result = await db.execute(
            select(SourceProductAssetORM.id).where(
                SourceProductAssetORM.satellite_family == "S1",
                SourceProductAssetORM.source_format == "S1_ZIP",
                SourceProductAssetORM.logical_product_uid == logical_uid,
                SourceProductAssetORM.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    def _radar_record_match_stmt(
        self,
        row: Dict[str, Any],
        asset_id: int,
        archive_asset_id: Optional[int],
    ):
        logical_uid = str(row.get("logical_product_uid") or "").strip()
        clauses = [
            RadarDataORM.file_path == row.get("file_path"),
            RadarDataORM.unique_id == f"asset:{row.get('asset_uid')}",
        ]
        if archive_asset_id is not None:
            clauses.append(RadarDataORM.source_archive_asset_id == int(archive_asset_id))
        if row.get("source_format") == "S1_SAFE_DIR" and logical_uid:
            clauses.append(
                and_(
                    RadarDataORM.satellite_family == "S1",
                    RadarDataORM.product_unique_id == logical_uid,
                )
            )
        elif row.get("source_format") == "S1_ZIP" and logical_uid:
            clauses.append(
                and_(
                    RadarDataORM.satellite_family == "S1",
                    RadarDataORM.product_unique_id == logical_uid,
                    RadarDataORM.source_archive_asset_id == int(asset_id),
                )
            )
        return select(RadarDataORM).where(or_(*clauses))

    async def bind_scene_orbits(
        self,
        db: AsyncSession,
        radar_data_ids: Optional[Sequence[int]] = None,
    ) -> Dict[str, Any]:
        now = _utcnow()
        scene_stmt = select(RadarDataORM).where(
            RadarDataORM.satellite_family.in_(["S1", "LT1"]),
            RadarDataORM.source_product_ref_id.is_not(None),
        )
        if radar_data_ids:
            scene_stmt = scene_stmt.where(RadarDataORM.id.in_([int(item) for item in radar_data_ids]))
        scene_result = await db.execute(scene_stmt)
        scenes = scene_result.scalars().all()
        issue_stmt = update(AssetInventoryIssueORM).where(
            AssetInventoryIssueORM.issue_code.in_(["scene_missing_orbit", "scene_ambiguous_orbit"]),
            AssetInventoryIssueORM.status == "OPEN",
        )
        if radar_data_ids:
            issue_stmt = issue_stmt.where(AssetInventoryIssueORM.radar_data_id.in_([int(item) for item in radar_data_ids]))
        await db.execute(issue_stmt.values(status="RESOLVED", resolved_at=now, last_seen_at=now))
        if scenes:
            await db.execute(delete(SceneOrbitBindingORM).where(SceneOrbitBindingORM.radar_data_id.in_([scene.id for scene in scenes if scene.id])))

        matched = 0
        missing = 0
        candidate_count = 0
        dirty_scene_ids: List[int] = []
        for scene in scenes:
            candidates = await self._find_orbit_candidates(db, scene)
            if not candidates:
                scene.has_orbit_data = False
                scene.orbit_file_path = None
                scene.selected_orbit_asset_id = None
                scene.orbit_binding_status = "MISSING"
                scene.orbit_binding_reason = "No active orbit asset covers the scene acquisition window."
                missing += 1
                db.add(
                    AssetInventoryIssueORM(
                        inventory_type="orbit_asset",
                        radar_data_id=scene.id,
                        severity="warning",
                        issue_code="scene_missing_orbit",
                        issue_message=scene.orbit_binding_reason,
                        source_path=scene.file_path,
                        status="OPEN",
                        first_seen_at=now,
                        last_seen_at=now,
                        metadata_json={
                            "satellite": scene.satellite,
                            "imaging_date": scene.imaging_date,
                            "acquisition_start_time_utc": scene.acquisition_start_time_utc.isoformat()
                            if scene.acquisition_start_time_utc
                            else None,
                        },
                    )
                )
                if scene.id is not None:
                    dirty_scene_ids.append(int(scene.id))
                continue

            candidate_count += len(candidates)
            selected = candidates[0]
            for rank, (orbit, score, reason, margins, rule_version) in enumerate(candidates, start=1):
                db.add(
                    SceneOrbitBindingORM(
                        radar_data_id=scene.id,
                        orbit_asset_id=orbit.id,
                        binding_role="primary_orbit",
                        match_status="MATCHED",
                        selection_status="SELECTED" if rank == 1 else "CANDIDATE",
                        selection_rank=rank,
                        priority_score=score,
                        coverage_margin_before_seconds=margins[0],
                        coverage_margin_after_seconds=margins[1],
                        match_rule_version=rule_version,
                        match_reason=reason,
                        selected_at=now if rank == 1 else None,
                    )
                )
            selected_orbit = selected[0]
            scene.has_orbit_data = True
            scene.orbit_file_path = selected_orbit.file_path
            scene.selected_orbit_asset_id = selected_orbit.id
            scene.orbit_binding_status = "MATCHED"
            scene.orbit_binding_reason = selected[2]
            db.add(scene)
            matched += 1
            if scene.id is not None:
                dirty_scene_ids.append(int(scene.id))

            if len(candidates) > 1 and abs(float(candidates[0][1]) - float(candidates[1][1])) < 0.001:
                db.add(
                    AssetInventoryIssueORM(
                        inventory_type="orbit_asset",
                        radar_data_id=scene.id,
                        orbit_asset_id=selected_orbit.id,
                        severity="warning",
                        issue_code="scene_ambiguous_orbit",
                        issue_message="Multiple orbit assets have equivalent selection priority.",
                        source_path=scene.file_path,
                        status="OPEN",
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )

        if dirty_scene_ids:
            await pairing_state_service.mark_scenes_dirty(
                db,
                scene_ids=sorted(set(dirty_scene_ids)),
                reason="asset_inventory_orbit_binding",
                commit=False,
            )
        return {
            "scene_count": len(scenes),
            "matched_count": matched,
            "missing_count": missing,
            "candidate_count": candidate_count,
        }

    async def _find_orbit_candidates(
        self,
        db: AsyncSession,
        scene: RadarDataORM,
    ) -> List[Tuple[OrbitAssetORM, float, str, Tuple[Optional[float], Optional[float]], str]]:
        family = normalize_satellite_family(scene.satellite_family or scene.satellite)
        satellite = str(scene.satellite or "").upper()
        if family == "S1":
            scene_start = scene.acquisition_start_time_utc
            scene_stop = scene.acquisition_stop_time_utc or scene_start
            if not scene_start or not scene_stop:
                return []
            result = await db.execute(
                select(OrbitAssetORM).where(
                    OrbitAssetORM.is_active == True,  # noqa: E712
                    OrbitAssetORM.satellite_family == "S1",
                    OrbitAssetORM.satellite == satellite,
                    OrbitAssetORM.validity_start_time_utc <= scene_start,
                    OrbitAssetORM.validity_stop_time_utc >= scene_stop,
                )
            )
            rows = result.scalars().all()
            candidates = []
            for orbit in rows:
                before = (scene_start - orbit.validity_start_time_utc).total_seconds() if orbit.validity_start_time_utc else None
                after = (orbit.validity_stop_time_utc - scene_stop).total_seconds() if orbit.validity_stop_time_utc else None
                quality_score = 1000.0 if orbit.quality_class == "precise" else 500.0 if orbit.quality_class == "restituted" else 100.0
                margin_score = min(before or 0.0, after or 0.0) / 100000.0
                generation_score = (orbit.generation_time_utc.timestamp() / 1000000000.0) if orbit.generation_time_utc else 0.0
                score = quality_score + margin_score + generation_score
                reason = (
                    f"{orbit.orbit_type} covers scene window "
                    f"{scene_start.isoformat()} to {scene_stop.isoformat()}"
                )
                candidates.append((orbit, score, reason, (before, after), S1_ORBIT_MATCH_RULE_VERSION))
            return sorted(candidates, key=lambda item: item[1], reverse=True)

        if family == "LT1":
            if not scene.imaging_date:
                return []
            day_start, day_stop = _date_start_stop(str(scene.imaging_date))
            if not day_start or not day_stop:
                return []
            result = await db.execute(
                select(OrbitAssetORM).where(
                    OrbitAssetORM.is_active == True,  # noqa: E712
                    OrbitAssetORM.satellite_family == "LT1",
                    OrbitAssetORM.satellite == satellite,
                    OrbitAssetORM.validity_start_time_utc <= day_start,
                    OrbitAssetORM.validity_stop_time_utc >= day_stop,
                )
            )
            rows = result.scalars().all()
            candidates = []
            for orbit in rows:
                score = 1000.0
                reason = f"LT1 orbit date matches scene imaging_date {scene.imaging_date}"
                candidates.append((orbit, score, reason, (0.0, 0.0), LT1_ORBIT_MATCH_RULE_VERSION))
            return sorted(candidates, key=lambda item: item[1], reverse=True)

        return []

    async def get_status(self, db: AsyncSession) -> Dict[str, Any]:
        state_rows = (
            await db.execute(
                select(AssetInventoryStateORM)
                .join(ManagedRootORM, AssetInventoryStateORM.root_ref_id == ManagedRootORM.id)
                .order_by(AssetInventoryStateORM.inventory_type.asc(), AssetInventoryStateORM.root_path.asc())
            )
        ).scalars().all()
        source_count = int(
            (
                await db.execute(
                    select(func.count(SourceProductAssetORM.id)).where(
                        SourceProductAssetORM.is_active == True,  # noqa: E712
                    )
                )
            ).scalar_one()
            or 0
        )
        orbit_count = int((await db.execute(select(func.count(OrbitAssetORM.id)).where(OrbitAssetORM.is_active == True))).scalar_one() or 0)  # noqa: E712
        binding_count = int((await db.execute(select(func.count(SceneOrbitBindingORM.id)).where(SceneOrbitBindingORM.selection_status == "SELECTED"))).scalar_one() or 0)
        open_issue_count = int((await db.execute(select(func.count(AssetInventoryIssueORM.id)).where(AssetInventoryIssueORM.status == "OPEN"))).scalar_one() or 0)

        return {
            "source_asset_count": source_count,
            "orbit_asset_count": orbit_count,
            "selected_binding_count": binding_count,
            "open_issue_count": open_issue_count,
            "states": [
                {
                    "id": row.id,
                    "root_ref_id": row.root_ref_id,
                    "inventory_type": row.inventory_type,
                    "root_path": row.root_path,
                    "scan_mode": row.scan_mode,
                    "status": row.status,
                    "last_scan_started_at": row.last_scan_started_at,
                    "last_scan_finished_at": row.last_scan_finished_at,
                    "last_seen_entry_count": row.last_seen_entry_count,
                    "last_asset_count": row.last_asset_count,
                    "last_issue_count": row.last_issue_count,
                    "parser_version": row.parser_version,
                    "needs_rescan": bool(row.needs_rescan),
                    "last_error": row.last_error,
                }
                for row in state_rows
            ],
        }

    async def list_source_products(
        self,
        db: AsyncSession,
        *,
        satellite_family: Optional[str] = None,
        satellite: Optional[str] = None,
        source_format: Optional[str] = None,
        parse_status: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 200), 1000))
        safe_offset = max(0, int(offset or 0))
        filters = []
        if not include_inactive:
            filters.append(SourceProductAssetORM.is_active == True)  # noqa: E712
        if satellite_family:
            filters.append(SourceProductAssetORM.satellite_family == satellite_family.upper())
        if satellite:
            filters.append(SourceProductAssetORM.satellite == satellite.upper())
        if source_format:
            filters.append(SourceProductAssetORM.source_format == source_format.upper())
        if parse_status:
            filters.append(SourceProductAssetORM.parse_status == parse_status.upper())
        stmt = select(SourceProductAssetORM)
        count_stmt = select(func.count(SourceProductAssetORM.id))
        for item in filters:
            stmt = stmt.where(item)
            count_stmt = count_stmt.where(item)
        total = int((await db.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await db.execute(
                stmt.order_by(SourceProductAssetORM.acquisition_start_time_utc.desc().nullslast(), SourceProductAssetORM.id.desc())
                .offset(safe_offset)
                .limit(safe_limit)
            )
        ).scalars().all()
        return {
            "items": [self._source_asset_payload(row) for row in rows],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(rows) < total,
        }

    async def list_orbits(
        self,
        db: AsyncSession,
        *,
        satellite_family: Optional[str] = None,
        satellite: Optional[str] = None,
        orbit_type: Optional[str] = None,
        parse_status: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 200), 1000))
        safe_offset = max(0, int(offset or 0))
        filters = []
        if not include_inactive:
            filters.append(OrbitAssetORM.is_active == True)  # noqa: E712
        if satellite_family:
            filters.append(OrbitAssetORM.satellite_family == satellite_family.upper())
        if satellite:
            filters.append(OrbitAssetORM.satellite == satellite.upper())
        if orbit_type:
            filters.append(OrbitAssetORM.orbit_type == orbit_type.upper())
        if parse_status:
            filters.append(OrbitAssetORM.parse_status == parse_status.upper())
        stmt = select(OrbitAssetORM)
        count_stmt = select(func.count(OrbitAssetORM.id))
        for item in filters:
            stmt = stmt.where(item)
            count_stmt = count_stmt.where(item)
        total = int((await db.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await db.execute(
                stmt.order_by(OrbitAssetORM.validity_start_time_utc.desc().nullslast(), OrbitAssetORM.id.desc())
                .offset(safe_offset)
                .limit(safe_limit)
            )
        ).scalars().all()
        return {
            "items": [self._orbit_asset_payload(row) for row in rows],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(rows) < total,
        }

    async def list_issues(
        self,
        db: AsyncSession,
        *,
        status: str = "OPEN",
        severity: Optional[str] = None,
        issue_code: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 200), 1000))
        safe_offset = max(0, int(offset or 0))
        filters = []
        if status:
            filters.append(AssetInventoryIssueORM.status == status.upper())
        if severity:
            filters.append(AssetInventoryIssueORM.severity == severity.lower())
        if issue_code:
            filters.append(AssetInventoryIssueORM.issue_code == issue_code)
        stmt = select(AssetInventoryIssueORM)
        count_stmt = select(func.count(AssetInventoryIssueORM.id))
        for item in filters:
            stmt = stmt.where(item)
            count_stmt = count_stmt.where(item)
        total = int((await db.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await db.execute(
                stmt.order_by(AssetInventoryIssueORM.last_seen_at.desc(), AssetInventoryIssueORM.id.desc())
                .offset(safe_offset)
                .limit(safe_limit)
            )
        ).scalars().all()
        return {
            "items": [
                {
                    "id": row.id,
                    "root_ref_id": row.root_ref_id,
                    "inventory_type": row.inventory_type,
                    "asset_ref_id": row.asset_ref_id,
                    "radar_data_id": row.radar_data_id,
                    "orbit_asset_id": row.orbit_asset_id,
                    "severity": row.severity,
                    "issue_code": row.issue_code,
                    "issue_message": row.issue_message,
                    "source_path": row.source_path,
                    "status": row.status,
                    "first_seen_at": row.first_seen_at,
                    "last_seen_at": row.last_seen_at,
                    "resolved_at": row.resolved_at,
                    "metadata_json": row.metadata_json,
                }
                for row in rows
            ],
            "total": total,
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + len(rows) < total,
        }

    def _source_asset_payload(self, row: SourceProductAssetORM) -> Dict[str, Any]:
        return {
            "id": row.id,
            "asset_uid": row.asset_uid,
            "logical_product_uid": row.logical_product_uid,
            "satellite_family": row.satellite_family,
            "satellite": row.satellite,
            "source_format": row.source_format,
            "product_type": row.product_type,
            "product_level": row.product_level,
            "imaging_mode": row.imaging_mode,
            "polarization": row.polarization,
            "absolute_orbit": row.absolute_orbit,
            "relative_orbit": row.relative_orbit,
            "orbit_direction": row.orbit_direction,
            "acquisition_start_time_utc": row.acquisition_start_time_utc,
            "acquisition_stop_time_utc": row.acquisition_stop_time_utc,
            "imaging_date": row.imaging_date,
            "root_ref_id": row.root_ref_id,
            "root_path": row.root_path,
            "file_path": row.file_path,
            "archive_path": row.archive_path,
            "size_bytes": row.size_bytes,
            "mtime_epoch": row.mtime_epoch,
            "checksum_status": row.checksum_status,
            "parser_name": row.parser_name,
            "parser_version": row.parser_version,
            "parse_status": row.parse_status,
            "parse_error": row.parse_error,
            "parsed_at": row.parsed_at,
            "metadata_json": row.metadata_json,
            "is_active": bool(row.is_active),
            "missing_since": row.missing_since,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _orbit_asset_payload(self, row: OrbitAssetORM) -> Dict[str, Any]:
        return {
            "id": row.id,
            "orbit_uid": row.orbit_uid,
            "satellite_family": row.satellite_family,
            "satellite": row.satellite,
            "orbit_type": row.orbit_type,
            "native_format": row.native_format,
            "quality_class": row.quality_class,
            "root_ref_id": row.root_ref_id,
            "root_path": row.root_path,
            "file_path": row.file_path,
            "file_name": row.file_name,
            "size_bytes": row.size_bytes,
            "mtime_epoch": row.mtime_epoch,
            "checksum_status": row.checksum_status,
            "validity_start_time_utc": row.validity_start_time_utc,
            "validity_stop_time_utc": row.validity_stop_time_utc,
            "generation_time_utc": row.generation_time_utc,
            "published_time_utc": row.published_time_utc,
            "parser_name": row.parser_name,
            "parser_version": row.parser_version,
            "parse_status": row.parse_status,
            "parse_error": row.parse_error,
            "parsed_at": row.parsed_at,
            "metadata_json": row.metadata_json,
            "is_active": bool(row.is_active),
            "missing_since": row.missing_since,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


asset_inventory_service = AssetInventoryService()
