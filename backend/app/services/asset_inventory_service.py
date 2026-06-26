from __future__ import annotations

import asyncio
import gzip
import hashlib
import math
import multiprocessing as mp
import os
import queue
import re
import shutil
import tarfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from geoalchemy2.shape import from_shape
from lxml import etree
from shapely.geometry import Polygon
from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..config import settings
from ..models import (
    AssetInventoryIssueORM,
    AssetInventoryStateORM,
    ManagedRootORM,
    OrbitAssetORM,
    OrbitAssetDerivativeORM,
    RadarDataORM,
    SARSceneGeometryProfileORM,
    SceneOrbitBindingORM,
    SourceMetadataDocumentORM,
    SourceProductAssetORM,
)
from ..utils import (
    build_corner_pixel_mapping,
    find_xml_file,
    normalize_satellite_family,
    parse_gf3_l2_dirname,
    parse_lt1_radar_filename,
    parse_xml_metadata,
)
from .pairing_state_service import pairing_state_service
from .data_service import DataService
from .image_service import image_service
from .orbit_converter import sync_orbit_pools
from .task_service import task_service


PARSER_VERSION = "asset_inventory_v2"
ARCHIVE_INTEGRITY_VERSION = "archive_integrity_v1"
S1_ORBIT_MATCH_RULE_VERSION = "s1_orbit_window_v1"
LT1_ORBIT_MATCH_RULE_VERSION = "lt1_orbit_day_v1"
ASSET_SCAN_LOG_INTERVAL = 100
ASSET_SCAN_DETAILED_PARSE_LOG_LIMIT = 200
ARCHIVE_INTEGRITY_LOG_INTERVAL = 10
DEFAULT_ASSET_SCAN_PARSE_WORKERS = 16
DEFAULT_ASSET_SCAN_PARSE_INFLIGHT = 64
DEFAULT_ASSET_SCAN_DB_BATCH_SIZE = 50

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


def _task_pool_materialize_root(source_format: str) -> str:
    base = _normalize_path(getattr(settings, "TASK_POOL_ROOT", "") or "")
    if not base:
        base = _normalize_path(os.path.join(settings.BACKEND_DIR, "runtime", "task_pool"))
    folder = {
        "S1_ZIP": "sentinel1",
        "S1_SAFE_DIR": "sentinel1",
        "LT1_ARCHIVE": "lutan1",
        "GF3_ARCHIVE": "gf3",
    }.get(str(source_format or "").upper(), "source")
    return _normalize_path(os.path.join(base, "source_materialized", folder))


def _source_ref_for_materialized_root(path: str) -> str:
    normalized = os.path.normcase(_normalize_path(path))
    task_root = os.path.normcase(_normalize_path(getattr(settings, "TASK_POOL_ROOT", "") or ""))
    if task_root and (normalized == task_root or normalized.startswith(task_root + os.sep)):
        return "TASK_POOL_ROOT"
    storage_roots = _configured_sentinel1_storage_dirs()
    for storage_root in storage_roots:
        storage_norm = os.path.normcase(_normalize_path(storage_root))
        if storage_norm and (normalized == storage_norm or normalized.startswith(storage_norm + os.sep)):
            return "SENTINEL1_STORAGE_DIRS"
    return "SOURCE_PRODUCT_DIRS"


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


def _ensure_local_runtime_path(path: str, label: str) -> str:
    normalized = _normalize_path(path)
    if _path_kind(normalized) == "unc":
        raise ValueError(f"{label} cannot use UNC path for active production: {normalized}")
    return normalized


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


def _activity_progress(progress_start: int, progress_end: int, count: int) -> int:
    start = max(0, min(100, int(progress_start)))
    end = max(start, min(100, int(progress_end)))
    collect_end = max(start, end - 6)
    if count <= 0 or collect_end <= start:
        return start
    return min(collect_end, start + 1 + int(count // 50))


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


def _file_ext_for_path(path: str) -> str:
    name = os.path.basename(str(path or ""))
    lower = name.lower()
    for suffix in (".tar.gz", ".tgz", ".zip", ".tar", ".safe", ".eof", ".txt"):
        if lower.endswith(suffix):
            return suffix
    ext = os.path.splitext(name)[1].lower()
    return ext[:32] if ext else ""


def _ordered_closed_polygon(points: Sequence[Any]) -> Optional[List[Tuple[float, float]]]:
    unique: List[Tuple[float, float]] = []
    for point in points or []:
        try:
            lon = float(point[0])
            lat = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        current = (lon, lat)
        if unique and abs(unique[-1][0] - lon) < 1e-12 and abs(unique[-1][1] - lat) < 1e-12:
            continue
        if unique and abs(unique[0][0] - lon) < 1e-12 and abs(unique[0][1] - lat) < 1e-12:
            continue
        if current not in unique:
            unique.append(current)
    if len(unique) < 3:
        return None

    candidates: List[List[Tuple[float, float]]] = []
    candidates.append(unique)
    if len(unique) == 4:
        center_lon = sum(point[0] for point in unique) / len(unique)
        center_lat = sum(point[1] for point in unique) / len(unique)
        candidates.insert(
            0,
            sorted(
                unique,
                key=lambda point: math.atan2(point[1] - center_lat, point[0] - center_lon),
            ),
        )

    for candidate in candidates:
        ring = list(candidate)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        try:
            polygon = Polygon(ring)
            if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
                return ring
        except Exception:
            continue
    return None


def _closed_polygon_if_valid(points: Sequence[Any]) -> Optional[List[Tuple[float, float]]]:
    ring: List[Tuple[float, float]] = []
    for point in points or []:
        try:
            ring.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError, IndexError):
            return None
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    try:
        polygon = Polygon(ring)
        if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
            return ring
    except Exception:
        return None
    return None


def _ordered_closed_polygon_from_corners(corners: Sequence[Dict[str, Any]]) -> Optional[List[Tuple[float, float]]]:
    entries = [
        item
        for item in (corners or [])
        if item.get("lon") is not None and item.get("lat") is not None
    ]
    if len(entries) < 3:
        return None

    by_name = {
        str(item.get("name") or "").strip().lower(): item
        for item in entries
        if str(item.get("name") or "").strip()
    }
    name_order = ["bottomleft", "bottomright", "topright", "topleft"]
    if all(name in by_name for name in name_order):
        ordered = [(by_name[name]["lon"], by_name[name]["lat"]) for name in name_order]
        valid = _closed_polygon_if_valid(ordered)
        if valid:
            return valid

    ref_entries = [
        item
        for item in entries
        if item.get("ref_row") is not None and item.get("ref_col") is not None
    ]
    if len(ref_entries) >= 4:
        min_row = min(float(item["ref_row"]) for item in ref_entries)
        max_row = max(float(item["ref_row"]) for item in ref_entries)
        min_col = min(float(item["ref_col"]) for item in ref_entries)
        max_col = max(float(item["ref_col"]) for item in ref_entries)
        targets = [(min_row, min_col), (min_row, max_col), (max_row, max_col), (max_row, min_col)]
        remaining = list(ref_entries)
        ordered_entries: List[Dict[str, Any]] = []
        for target_row, target_col in targets:
            chosen = min(
                remaining,
                key=lambda item: (
                    abs(float(item["ref_row"]) - target_row) + abs(float(item["ref_col"]) - target_col),
                    str(item.get("name") or ""),
                ),
            )
            ordered_entries.append(chosen)
            remaining.remove(chosen)
        ordered = [(item["lon"], item["lat"]) for item in ordered_entries]
        valid = _closed_polygon_if_valid(ordered)
        if valid:
            return valid

    return _ordered_closed_polygon([(item["lon"], item["lat"]) for item in entries])


def _root_supported_families(root: ManagedRootORM) -> List[str]:
    text = " ".join(
        str(value or "")
        for value in (
            root.path,
            root.root_code,
            root.root_role,
            root.display_name,
            root.source_ref,
        )
    ).lower()
    families: List[str] = []
    if "lutan" in text or "lt1" in text or "lt-1" in text:
        families.append("LT1")
    if "sentinel" in text or "sentinel1" in text or "eof" in text or "safe" in text:
        families.append("S1")
    if "gaofen" in text or "gf3" in text:
        families.append("GF3")
    return families


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


def _archive_collect_members(
    path: str,
    *,
    content_predicate: Callable[[str], bool],
    list_predicate: Callable[[str], bool],
    list_limit: int = 20,
) -> Tuple[Optional[str], Optional[bytes], List[str], List[str]]:
    content_name: Optional[str] = None
    content_data: Optional[bytes] = None
    listed: List[str] = []
    scanned: List[str] = []
    target_list_count = max(0, int(list_limit))

    def _visit(name: str, reader: Callable[[], bytes]) -> None:
        nonlocal content_name, content_data
        scanned.append(name)
        if list_predicate(name) and len(listed) < target_list_count:
            listed.append(name)
        if content_name is None and content_predicate(name):
            content_name = name
            content_data = reader()

    def _done() -> bool:
        return content_name is not None and len(listed) >= target_list_count

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                _visit(info.filename, lambda info=info: archive.read(info))
                if _done():
                    break
        return content_name, content_data, listed, scanned

    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for member in archive:
                if not member.isfile():
                    continue

                def _read(member=member) -> bytes:
                    source = archive.extractfile(member)
                    if source is None:
                        return b""
                    with source:
                        return source.read()

                _visit(member.name, _read)
                if _done():
                    break
        return content_name, content_data, listed, scanned

    return None, None, listed, scanned


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


def _archive_integrity_supported(source_format: Any, path: str) -> bool:
    normalized_format = str(source_format or "").upper()
    if normalized_format not in {"LT1_ARCHIVE", "S1_ZIP", "GF3_ARCHIVE"}:
        return False
    ext = _file_ext_for_path(path)
    if normalized_format == "S1_ZIP":
        return ext == ".zip"
    return ext in {".tar.gz", ".tgz", ".tar", ".zip"}


def _truncate_error_text(value: Any, limit: int = 1000) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _check_zip_archive_integrity(path: str) -> Dict[str, Any]:
    method = "zip_testzip"
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        member_count = 0
        total_uncompressed = 0
        for info in infos:
            _safe_archive_member_name(info.filename, path)
            if info.is_dir():
                continue
            member_count += 1
            total_uncompressed += int(info.file_size or 0)
        bad_member = archive.testzip()
        if bad_member:
            return {
                "status": "FAILED",
                "method": method,
                "error": f"ZIP CRC failed at member: {bad_member}",
                "member_count": member_count,
                "uncompressed_bytes": total_uncompressed,
            }
    return {
        "status": "OK",
        "method": method,
        "error": None,
        "member_count": member_count,
        "uncompressed_bytes": total_uncompressed,
    }


def _check_tar_archive_integrity(path: str) -> Dict[str, Any]:
    method = "tar_stream_list"
    member_count = 0
    total_uncompressed = 0
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            _safe_archive_member_name(member.name, path)
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"Unsupported TAR member type: {member.name}")
            if member.isfile():
                member_count += 1
                total_uncompressed += int(member.size or 0)
            elif member.isdir():
                continue
            else:
                raise ValueError(f"Unsupported TAR member type: {member.name}")
    return {
        "status": "OK",
        "method": method,
        "error": None,
        "member_count": member_count,
        "uncompressed_bytes": total_uncompressed,
    }


def _check_archive_integrity(path: str, source_format: Any = None) -> Dict[str, Any]:
    archive = _normalize_path(path)
    started = _utcnow()
    stat = _stat_path(archive)
    if not os.path.isfile(archive):
        return {
            "status": "FAILED",
            "method": None,
            "error": f"Archive file is missing: {archive}",
            "member_count": None,
            "size_bytes": stat.get("size_bytes"),
            "mtime_epoch": stat.get("mtime_epoch"),
            "duration_seconds": 0.0,
            "version": ARCHIVE_INTEGRITY_VERSION,
        }
    if not _archive_integrity_supported(source_format, archive):
        return {
            "status": "UNSUPPORTED",
            "method": None,
            "error": f"Unsupported archive integrity source_format={source_format} ext={_file_ext_for_path(archive)}",
            "member_count": None,
            "size_bytes": stat.get("size_bytes"),
            "mtime_epoch": stat.get("mtime_epoch"),
            "duration_seconds": 0.0,
            "version": ARCHIVE_INTEGRITY_VERSION,
        }
    try:
        ext = _file_ext_for_path(archive)
        if ext == ".zip":
            result = _check_zip_archive_integrity(archive)
        elif ext in {".tar.gz", ".tgz", ".tar"}:
            result = _check_tar_archive_integrity(archive)
        else:
            result = {
                "status": "UNSUPPORTED",
                "method": None,
                "error": f"Unsupported archive extension: {ext}",
                "member_count": None,
            }
    except Exception as exc:
        result = {
            "status": "FAILED",
            "method": "zip_testzip" if _file_ext_for_path(archive) == ".zip" else "tar_stream_list",
            "error": str(exc),
            "member_count": None,
        }
    duration = (_utcnow() - started).total_seconds()
    result.update(
        {
            "size_bytes": stat.get("size_bytes"),
            "mtime_epoch": stat.get("mtime_epoch"),
            "duration_seconds": round(max(0.0, duration), 3),
            "version": ARCHIVE_INTEGRITY_VERSION,
        }
    )
    result["error"] = _truncate_error_text(result.get("error"))
    return result


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


def _xml_int(value: Optional[str]) -> Optional[int]:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _parse_radar_xml_metadata_bytes(data: bytes) -> Tuple[Optional[List[Tuple[float, float]]], Dict[str, Any]]:
    parser = _xml_parser()
    root = etree.fromstring(data, parser=parser)

    corners: List[Dict[str, Any]] = []
    for element in root.iter():
        if etree.QName(element).localname.lower() != "scenecornercoord":
            continue
        lon = _xml_float(_xml_text_under_local_path(element, "sceneCornerCoord", "lon") or _xml_text_by_local_names(element, ["lon"]))
        lat = _xml_float(_xml_text_under_local_path(element, "sceneCornerCoord", "lat") or _xml_text_by_local_names(element, ["lat"]))
        if lon is not None and lat is not None:
            corners.append(
                {
                    "name": element.get("name"),
                    "lon": lon,
                    "lat": lat,
                    "ref_row": _xml_int(
                        _xml_text_under_local_path(element, "sceneCornerCoord", "refRow")
                        or _xml_text_by_local_names(element, ["refRow"])
                    ),
                    "ref_col": _xml_int(
                        _xml_text_under_local_path(element, "sceneCornerCoord", "refColumn")
                        or _xml_text_by_local_names(element, ["refColumn"])
                    ),
                }
            )

    coverage_polygon: Optional[List[Tuple[float, float]]] = None
    corner_details: Dict[str, Dict[str, Any]] = {}
    if len(corners) >= 4:
        coverage_polygon = _ordered_closed_polygon_from_corners(corners[:4])
        corner_details = {
            str(item.get("name")): {
                "lon": item.get("lon"),
                "lat": item.get("lat"),
                "ref_row": item.get("ref_row"),
                "ref_col": item.get("ref_col"),
            }
            for item in corners
            if item.get("name")
        }

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
        "product_type": _xml_text_by_local_names(root, ["productType"]),
        "image_data_type": _xml_text_under_local_path(root, "imageDataInfo", "imageDataType")
        or _xml_text_by_local_names(root, ["imageDataType"]),
        "product_variant": _xml_text_under_local_path(root, "orderInfo", "productVariant")
        or _xml_text_by_local_names(root, ["productVariant"]),
        "image_data_format": _xml_text_under_local_path(root, "imageDataInfo", "imageDataFormat")
        or _xml_text_by_local_names(root, ["imageDataFormat"]),
        "product_level": _xml_text_by_local_names(root, ["productLevel", "itemName"]),
        "product_unique_id": _xml_text_by_local_names(root, ["logicalProductID", "sceneID", "productID"]),
        "look_direction": (_xml_text_under_local_path(root, "acquisitionInfo", "lookDirection") or "").upper() or None,
        "corner_ref_pixels": {
            str(item.get("name")): {
                "ref_row": item.get("ref_row"),
                "ref_col": item.get("ref_col"),
            }
            for item in corners
            if item.get("name")
        },
        "corner_pixel_mapping": build_corner_pixel_mapping(corner_details),
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


def _metadata_document(
    *,
    document_type: str,
    member_path: str,
    content: bytes,
    source_format: Optional[str],
    satellite_family: Optional[str],
    archive_path: str,
    archive_mtime: Optional[float],
    parse_status: str = "OK",
    parse_error: Optional[str] = None,
) -> Dict[str, Any]:
    payload = bytes(content or b"")
    return {
        "document_type": document_type,
        "member_path": member_path or document_type,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "content_encoding": "gzip",
        "content_bytes": gzip.compress(payload),
        "content_size_bytes": len(payload),
        "source_format": source_format,
        "satellite_family": satellite_family,
        "archive_path": archive_path,
        "archive_mtime": archive_mtime,
        "parser_version": PARSER_VERSION,
        "parse_status": parse_status,
        "parse_error": parse_error,
        "extracted_at": _utcnow(),
    }


def _s1_annotation_sort_key(path: str) -> Tuple[int, str]:
    normalized = str(path or "").replace("\\", "/")
    lower = normalized.lower()
    base = PurePosixPath(normalized).name.lower()
    is_direct_annotation = "/annotation/" in lower and lower.count("/annotation/") == 1
    is_measurement_annotation = is_direct_annotation and base.startswith("s1") and base.endswith(".xml")
    if is_measurement_annotation:
        rank = 0
    elif "/annotation/calibration/" in lower:
        rank = 2
    elif "/annotation/noise/" in lower:
        rank = 3
    elif "/annotation/rfi/" in lower:
        rank = 4
    else:
        rank = 1
    return rank, lower


def _parse_s1_preview_kml_bytes(data: bytes) -> Dict[str, Any]:
    try:
        root = etree.fromstring(data, parser=_xml_parser())
    except Exception:
        return {}
    coordinate_texts = root.xpath("//*[local-name()='LatLonQuad']/*[local-name()='coordinates']/text()")
    if not coordinate_texts:
        return {}
    points: List[Tuple[float, float]] = []
    for token in str(coordinate_texts[0] or "").strip().split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if len(points) != 4:
        return {}
    mapping = {
        "bottom_left": [points[0][0], points[0][1]],
        "bottom_right": [points[1][0], points[1][1]],
        "top_right": [points[2][0], points[2][1]],
        "top_left": [points[3][0], points[3][1]],
        "source": "s1_preview_map_overlay_kml",
    }
    polygon = [points[0], points[1], points[2], points[3], points[0]]
    return {
        "preview_map_overlay_polygon": polygon,
        "corner_pixel_mapping": mapping,
    }


def _extract_s1_annotation_documents(source_path: str, *, limit: int = 16) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    stat = _stat_path(source_path)
    if os.path.isdir(source_path):
        annotation_root = os.path.join(source_path, "annotation")
        if not os.path.isdir(annotation_root):
            return docs
        candidates: List[str] = []
        for current_root, _, files in os.walk(annotation_root):
            for file_name in files:
                if file_name.lower().endswith(".xml"):
                    candidates.append(os.path.join(current_root, file_name))
        for path in sorted(candidates, key=_s1_annotation_sort_key)[: max(0, limit)]:
            try:
                with open(path, "rb") as stream:
                    data = stream.read()
                docs.append(
                    _metadata_document(
                        document_type="S1_ANNOTATION",
                        member_path=os.path.relpath(path, source_path).replace("\\", "/"),
                        content=data,
                        source_format="S1_SAFE_DIR",
                        satellite_family="S1",
                        archive_path=source_path,
                        archive_mtime=stat.get("mtime_epoch"),
                    )
                )
            except OSError:
                continue
        return docs

    try:
        with zipfile.ZipFile(source_path) as archive:
            names = [
                name
                for name in archive.namelist()
                if "/annotation/" in name.lower() and name.lower().endswith(".xml")
            ]
            for name in sorted(names, key=_s1_annotation_sort_key)[: max(0, limit)]:
                docs.append(
                    _metadata_document(
                        document_type="S1_ANNOTATION",
                        member_path=name,
                        content=archive.read(name),
                        source_format="S1_ZIP",
                        satellite_family="S1",
                        archive_path=source_path,
                        archive_mtime=stat.get("mtime_epoch"),
                    )
                )
    except Exception:
        return docs
    return docs


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

    return _ordered_closed_polygon(points)


def _bbox_from_polygon(points: Optional[List[Tuple[float, float]]]) -> Optional[Tuple[float, float, float, float]]:
    ordered = _ordered_closed_polygon(points or [])
    if not ordered or len(ordered) < 4:
        return None
    lons = [float(point[0]) for point in ordered]
    lats = [float(point[1]) for point in ordered]
    return min(lons), min(lats), max(lons), max(lats)


def _centroid_from_polygon(points: Optional[List[Tuple[float, float]]]) -> Tuple[Optional[float], Optional[float]]:
    ordered = _ordered_closed_polygon(points or [])
    if not ordered or len(ordered) < 4:
        return None, None
    try:
        poly = Polygon(ordered)
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
    stat = _stat_path(path)
    with zipfile.ZipFile(path) as archive:
        manifest_name = None
        preview_kml_name = None
        annotation_names: List[str] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            lower = name.lower()
            if manifest_name is None and (lower.endswith("/manifest.safe") or lower == "manifest.safe"):
                manifest_name = name
            if preview_kml_name is None and lower.endswith("/preview/map-overlay.kml"):
                preview_kml_name = name
            if "/annotation/" in lower and lower.endswith(".xml"):
                annotation_names.append(name)
        if not manifest_name:
            return {"manifest_parse_status": "MISSING"}
        manifest_bytes = archive.read(manifest_name)
        annotation_documents = [
            _metadata_document(
                document_type="S1_ANNOTATION",
                member_path=name,
                content=archive.read(name),
                source_format="S1_ZIP",
                satellite_family="S1",
                archive_path=path,
                archive_mtime=stat.get("mtime_epoch"),
            )
            for name in sorted(annotation_names, key=_s1_annotation_sort_key)[:16]
        ]
        preview_kml_bytes = archive.read(preview_kml_name) if preview_kml_name else None
        preview_kml_meta = _parse_s1_preview_kml_bytes(preview_kml_bytes) if preview_kml_bytes else {}
        preview_kml_documents = [
            _metadata_document(
                document_type="S1_PREVIEW_KML",
                member_path=preview_kml_name or "preview/map-overlay.kml",
                content=preview_kml_bytes,
                source_format="S1_ZIP",
                satellite_family="S1",
                archive_path=path,
                archive_mtime=stat.get("mtime_epoch"),
            )
        ] if preview_kml_bytes else []
        return {
            "manifest_parse_status": "OK",
            "manifest_path": manifest_name,
            "metadata_documents": [
                _metadata_document(
                    document_type="S1_MANIFEST",
                    member_path=manifest_name,
                    content=manifest_bytes,
                    source_format="S1_ZIP",
                    satellite_family="S1",
                    archive_path=path,
                    archive_mtime=stat.get("mtime_epoch"),
                ),
                *preview_kml_documents,
                *annotation_documents,
            ],
            **preview_kml_meta,
            **_parse_s1_manifest_bytes(manifest_bytes),
        }


def _parse_s1_safe_manifest(path: str) -> Dict[str, Any]:
    manifest_path = os.path.join(path, "manifest.safe")
    if not os.path.isfile(manifest_path):
        return {"manifest_parse_status": "MISSING"}
    stat = _stat_path(path)
    with open(manifest_path, "rb") as stream:
        manifest_bytes = stream.read()
        preview_kml_path = os.path.join(path, "preview", "map-overlay.kml")
        preview_kml_bytes = None
        if os.path.isfile(preview_kml_path):
            with open(preview_kml_path, "rb") as preview_stream:
                preview_kml_bytes = preview_stream.read()
        preview_kml_meta = _parse_s1_preview_kml_bytes(preview_kml_bytes) if preview_kml_bytes else {}
        preview_kml_documents = [
            _metadata_document(
                document_type="S1_PREVIEW_KML",
                member_path="preview/map-overlay.kml",
                content=preview_kml_bytes,
                source_format="S1_SAFE_DIR",
                satellite_family="S1",
                archive_path=path,
                archive_mtime=stat.get("mtime_epoch"),
            )
        ] if preview_kml_bytes else []
        return {
            "manifest_parse_status": "OK",
            "manifest_path": manifest_path,
            "metadata_documents": [
                _metadata_document(
                    document_type="S1_MANIFEST",
                    member_path="manifest.safe",
                    content=manifest_bytes,
                    source_format="S1_SAFE_DIR",
                    satellite_family="S1",
                    archive_path=path,
                    archive_mtime=stat.get("mtime_epoch"),
                ),
                *preview_kml_documents,
                *_extract_s1_annotation_documents(path),
            ],
            **preview_kml_meta,
            **_parse_s1_manifest_bytes(manifest_bytes),
        }


def _parse_lt1_archive_metadata(path: str) -> Dict[str, Any]:
    archive_stem = _strip_known_suffix(os.path.basename(path))
    stat = _stat_path(path)
    xml_member, xml_data, tiff_members, members = _archive_collect_members(
        path,
        content_predicate=lambda name: _archive_member_base_name(name).lower().endswith(".meta.xml"),
        list_predicate=lambda name: _archive_member_base_name(name).lower().endswith((".tiff", ".tif")),
        list_limit=8,
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
        "metadata_documents": [
            _metadata_document(
                document_type="LT1_META",
                member_path=xml_member,
                content=xml_data,
                source_format="LT1_ARCHIVE",
                satellite_family="LT1",
                archive_path=path,
                archive_mtime=stat.get("mtime_epoch"),
            )
        ],
        "coverage_polygon": coverage_polygon,
        **xml_meta,
    }


def _parse_gf3_archive_metadata(path: str) -> Dict[str, Any]:
    archive_stem = _strip_known_suffix(os.path.basename(path))
    xml_member, xml_data, quicklooks, members = _archive_collect_members(
        path,
        content_predicate=lambda name: _archive_member_base_name(name).lower().endswith(".xml"),
        list_predicate=lambda name: _archive_member_base_name(name).lower().endswith((".jpg", ".jpeg", ".png", ".bmp", "_ql.tif", "_ql.tiff")),
        list_limit=8,
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
                with open(xml_path, "rb") as stream:
                    xml_meta["metadata_documents"] = [
                        _metadata_document(
                            document_type="LT1_META",
                            member_path=os.path.relpath(xml_path, path).replace("\\", "/"),
                            content=stream.read(),
                            source_format="LT1_DIR",
                            satellite_family="LT1",
                            archive_path=path,
                            archive_mtime=stat.get("mtime_epoch"),
                        )
                    ]
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
    metadata.update({key: value for key, value in manifest_meta.items() if key != "metadata_documents"})
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

    row = {
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
        "file_ext": _file_ext_for_path(path),
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
    row["_metadata_documents"] = manifest_meta.get("metadata_documents") or []
    return row


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
    metadata.update({key: value for key, value in xml_meta.items() if key != "metadata_documents" and value not in (None, "")})
    metadata["coverage_polygon"] = coverage_polygon
    metadata["coverage_bbox"] = _bbox_from_polygon(coverage_polygon)
    centroid_lon, centroid_lat = _centroid_from_polygon(coverage_polygon)
    metadata["scene_center_lon"] = parsed.get("scene_center_lon") if parsed.get("scene_center_lon") is not None else centroid_lon
    metadata["scene_center_lat"] = parsed.get("scene_center_lat") if parsed.get("scene_center_lat") is not None else centroid_lat
    satellite = parsed.get("satellite")
    imaging_date = parsed.get("imaging_date")

    row = {
        "asset_uid": _asset_uid("source", path),
        "logical_product_uid": _strip_known_suffix(os.path.basename(path)),
        "satellite_family": normalize_satellite_family(satellite),
        "satellite": satellite,
        "source_format": source_format,
        "product_type": parsed.get("product_type") or xml_meta.get("product_type"),
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
        "file_ext": _file_ext_for_path(path),
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
    row["_metadata_documents"] = xml_meta.get("metadata_documents") or []
    return row


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
        "file_ext": _file_ext_for_path(path),
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
                            stem_upper = _strip_known_suffix(entry.name).upper()
                            if entry.name.upper().startswith("S1") and entry.name.lower().endswith(".zip"):
                                yield _normalize_path(entry.path)
                                continue
                            if stem_upper.startswith("LT1") and _has_archive_suffix(entry.name, _LT1_ARCHIVE_EXTS):
                                yield _normalize_path(entry.path)
                                continue
                            if stem_upper.startswith("GF3") and _has_archive_suffix(entry.name, _GF3_ARCHIVE_EXTS):
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
                            if name_upper.startswith("S1") and entry.name.lower().endswith(".zip"):
                                yield _normalize_path(entry.path)
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


def _source_parse_worker_main(worker_id: int, generation: int, task_queue: Any, result_queue: Any) -> None:
    while True:
        task = task_queue.get()
        if task is None:
            return
        index = int(task.get("index") or 0)
        normalized_path = str(task.get("path") or "")
        file_name = os.path.basename(normalized_path)
        root = SimpleNamespace(id=int(task.get("root_id") or 0), path=str(task.get("root_path") or ""))
        try:
            row = _parse_source_entry(normalized_path, root)
            result_queue.put(
                {
                    "worker_id": worker_id,
                    "generation": generation,
                    "index": index,
                    "path": normalized_path,
                    "file_name": file_name,
                    "row": row,
                    "error": None,
                }
            )
        except Exception as exc:
            result_queue.put(
                {
                    "worker_id": worker_id,
                    "generation": generation,
                    "index": index,
                    "path": normalized_path,
                    "file_name": file_name,
                    "row": None,
                    "error": str(exc),
                }
            )


def _int_or_default(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


class _SourceParseProcessPool:
    def __init__(
        self,
        *,
        root_id: int,
        root_path: str,
        workers: int,
        timeout_seconds: int,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.root_id = int(root_id)
        self.root_path = str(root_path)
        self.workers = max(1, int(workers or 1))
        self.timeout_seconds = max(1, int(timeout_seconds or 1))
        self.log_callback = log_callback
        self.ctx = mp.get_context("spawn")
        self.result_queue = self.ctx.Queue()
        self.pending_tasks: List[Dict[str, Any]] = []
        self.slots: List[Dict[str, Any]] = [self._new_slot(worker_id=index) for index in range(self.workers)]

    def _log(self, level: str, message: str) -> None:
        if self.log_callback:
            self.log_callback(level, message)

    def _new_slot(self, *, worker_id: int, generation: int = 0) -> Dict[str, Any]:
        task_queue = self.ctx.Queue(maxsize=1)
        process = self.ctx.Process(
            target=_source_parse_worker_main,
            args=(worker_id, generation, task_queue, self.result_queue),
            daemon=True,
        )
        process.start()
        return {
            "worker_id": worker_id,
            "generation": generation,
            "process": process,
            "queue": task_queue,
            "task": None,
            "started_at": None,
        }

    def _stop_slot(self, slot: Dict[str, Any], *, terminate: bool = False) -> None:
        process = slot.get("process")
        task_queue = slot.get("queue")
        if process is not None:
            if process.is_alive():
                if terminate:
                    process.terminate()
                else:
                    try:
                        task_queue.put_nowait(None)
                    except Exception:
                        process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            else:
                process.join(timeout=0)
        if task_queue is not None:
            try:
                task_queue.cancel_join_thread()
            except Exception:
                pass
            try:
                task_queue.close()
            except Exception:
                pass
        slot["task"] = None
        slot["started_at"] = None

    def close(self) -> None:
        for slot in self.slots:
            self._stop_slot(slot, terminate=True)
        try:
            self.result_queue.cancel_join_thread()
        except Exception:
            pass
        try:
            self.result_queue.close()
        except Exception:
            pass

    def active_count(self) -> int:
        return len(self.pending_tasks) + sum(1 for slot in self.slots if slot.get("task") is not None)

    def submit(self, index: int, normalized_path: str) -> None:
        task = {
            "index": int(index),
            "path": normalized_path,
            "root_id": self.root_id,
            "root_path": self.root_path,
        }
        self.pending_tasks.append(task)
        self._dispatch_available()

    def drain(self, *, wait_for_one: bool) -> List[Dict[str, Any]]:
        raw_results: List[Dict[str, Any]] = []
        accepted_results: List[Dict[str, Any]] = []
        self._dispatch_available()
        if wait_for_one:
            if self.active_count() <= 0:
                return self._collect_unhealthy_workers()
            try:
                result = self.result_queue.get(timeout=0.2)
                raw_results.append(result)
            except queue.Empty:
                pass
        while True:
            try:
                result = self.result_queue.get_nowait()
            except queue.Empty:
                break
            raw_results.append(result)
        for result in raw_results:
            if self._release_completed_slot(result):
                accepted_results.append(result)
        accepted_results.extend(self._collect_unhealthy_workers())
        self._dispatch_available()
        return accepted_results

    def _dispatch_available(self) -> None:
        for idx, slot in enumerate(list(self.slots)):
            if not self.pending_tasks:
                return
            if slot.get("task") is not None:
                continue
            process = slot.get("process")
            if process is None or not process.is_alive():
                worker_id = _int_or_default(slot.get("worker_id"), idx)
                generation = _int_or_default(slot.get("generation"), 0)
                self._stop_slot(slot, terminate=True)
                self.slots[idx] = self._new_slot(worker_id=worker_id, generation=generation + 1)
                slot = self.slots[idx]
            task = self.pending_tasks.pop(0)
            slot["queue"].put(task)
            slot["task"] = task
            slot["started_at"] = time.monotonic()

    def _release_completed_slot(self, result: Dict[str, Any]) -> bool:
        result_worker_id = _int_or_default(result.get("worker_id"), -1)
        result_generation = _int_or_default(result.get("generation"), -1)
        result_index = _int_or_default(result.get("index"), -1)
        for slot in self.slots:
            if _int_or_default(slot.get("worker_id"), -1) != result_worker_id:
                continue
            if _int_or_default(slot.get("generation"), -1) != result_generation:
                return False
            task = slot.get("task")
            if task is None or _int_or_default(task.get("index"), -1) != result_index:
                return False
            slot["task"] = None
            slot["started_at"] = None
            return True
        return False

    def _collect_unhealthy_workers(self) -> List[Dict[str, Any]]:
        now = time.monotonic()
        results: List[Dict[str, Any]] = []
        for idx, slot in enumerate(list(self.slots)):
            task = slot.get("task")
            process = slot.get("process")
            worker_id = _int_or_default(slot.get("worker_id"), idx)
            generation = _int_or_default(slot.get("generation"), 0)
            if task is None:
                if process is None or not process.is_alive():
                    self._stop_slot(slot, terminate=True)
                    self.slots[idx] = self._new_slot(worker_id=worker_id, generation=generation + 1)
                continue
            path = str(task.get("path") or "")
            file_name = os.path.basename(path)
            if process is None or not process.is_alive():
                exitcode = getattr(process, "exitcode", None)
                self._log(
                    "WARNING",
                    f"Source archive metadata parse worker exited unexpectedly (exitcode={exitcode}): {file_name}",
                )
                self._stop_slot(slot, terminate=True)
                self.slots[idx] = self._new_slot(worker_id=worker_id, generation=generation + 1)
                results.append(
                    {
                        "worker_id": worker_id,
                        "generation": generation,
                        "index": _int_or_default(task.get("index"), 0),
                        "path": path,
                        "file_name": file_name,
                        "row": None,
                        "error": f"parse worker exited unexpectedly (exitcode={exitcode})",
                    }
                )
                continue
            started_at = slot.get("started_at")
            if started_at is None:
                continue
            elapsed = now - float(started_at)
            if elapsed < self.timeout_seconds:
                continue
            self._log(
                "WARNING",
                f"Source archive metadata parse timed out after {self.timeout_seconds}s: {file_name}",
            )
            self._stop_slot(slot, terminate=True)
            self.slots[idx] = self._new_slot(worker_id=worker_id, generation=generation + 1)
            results.append(
                {
                    "worker_id": worker_id,
                    "generation": generation,
                    "index": _int_or_default(task.get("index"), 0),
                    "path": path,
                    "file_name": file_name,
                    "row": None,
                    "error": f"parse timed out after {self.timeout_seconds}s",
                }
            )
        self._dispatch_available()
        return results


def _same_mtime(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(float(left) - float(right)) <= 0.001
    except (TypeError, ValueError):
        return False


def _same_size(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return False


def _cached_source_asset_is_unchanged(
    cached: Optional[Dict[str, Any]],
    stat: Dict[str, Optional[float]],
    root: ManagedRootORM,
    *,
    skip_unchanged_failures: bool = True,
) -> bool:
    if not cached:
        return False
    source_format = str(cached.get("source_format") or "").upper()
    if source_format not in {"S1_ZIP", "LT1_ARCHIVE", "GF3_ARCHIVE"}:
        return False
    try:
        if int(cached.get("root_ref_id") or 0) != int(root.id or 0):
            return False
    except (TypeError, ValueError):
        return False
    if not bool(cached.get("is_active")):
        return False
    if str(cached.get("parser_version") or "") != PARSER_VERSION:
        return False
    parse_status = str(cached.get("parse_status") or "").upper()
    allowed_statuses = {"OK"}
    if skip_unchanged_failures:
        allowed_statuses.update({"PARTIAL", "FAILED"})
    if parse_status not in allowed_statuses:
        return False
    return _same_size(cached.get("size_bytes"), stat.get("size_bytes")) and _same_mtime(
        cached.get("mtime_epoch"),
        stat.get("mtime_epoch"),
    )


def _cached_orbit_asset_is_unchanged(
    cached: Optional[Dict[str, Any]],
    stat: Dict[str, Optional[float]],
    root: ManagedRootORM,
) -> bool:
    if not cached:
        return False
    native_format = str(cached.get("native_format") or "").upper()
    if native_format not in {"TXT", "EOF"}:
        return False
    try:
        if int(cached.get("root_ref_id") or 0) != int(root.id or 0):
            return False
    except (TypeError, ValueError):
        return False
    if not bool(cached.get("is_active")):
        return False
    if str(cached.get("parser_version") or "") != PARSER_VERSION:
        return False
    if str(cached.get("parse_status") or "").upper() != "OK":
        return False
    return _same_size(cached.get("size_bytes"), stat.get("size_bytes")) and _same_mtime(
        cached.get("mtime_epoch"),
        stat.get("mtime_epoch"),
    )


def _collect_source_assets_incremental(
    root: ManagedRootORM,
    existing_by_path: Dict[str, Dict[str, Any]],
    *,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
    progress_start: int = 0,
    progress_end: int = 100,
    parse_workers: int = DEFAULT_ASSET_SCAN_PARSE_WORKERS,
    parse_inflight: int = DEFAULT_ASSET_SCAN_PARSE_INFLIGHT,
    skip_unchanged_failures: bool = True,
    row_batch_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    row_batch_size: int = DEFAULT_ASSET_SCAN_DB_BATCH_SIZE,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, int, List[str]]:
    rows: List[Dict[str, Any]] = []
    pending_rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    seen_paths: List[str] = []
    seen_path_set: set[str] = set()
    entry_count = 0
    skipped_unchanged = 0
    skipped_unchanged_ok = 0
    skipped_unchanged_failed = 0
    parse_attempts = 0
    parse_completed = 0
    last_progress_count = 0
    last_parse_wait_log_at = time.monotonic()
    parse_workers = max(1, min(int(parse_workers or 1), 32))
    parse_inflight = max(parse_workers, min(int(parse_inflight or parse_workers), parse_workers * 4))
    row_batch_size = max(1, int(row_batch_size or 1))
    parse_timeout_seconds = max(60, int(getattr(settings, "ASSET_SCAN_PARSE_TIMEOUT_SECONDS", 600) or 600))

    def _log(level: str, message: str) -> None:
        if log_callback:
            log_callback(level, message)

    def _progress(message: str) -> None:
        if progress_callback:
            progress_callback(_activity_progress(progress_start, progress_end, entry_count), message)

    def _mark_seen(path: str) -> None:
        normalized = _normalize_path(path)
        if normalized and normalized not in seen_path_set:
            seen_path_set.add(normalized)
            seen_paths.append(normalized)

    def _emit_row_batch(*, force: bool = False) -> None:
        if not row_batch_callback or not pending_rows:
            return
        if not force and len(pending_rows) < row_batch_size:
            return
        batch = pending_rows[:]
        pending_rows.clear()
        row_batch_callback(batch)

    def _handle_parse_result(result: Dict[str, Any]) -> None:
        nonlocal parse_completed, last_progress_count
        parse_completed += 1
        normalized_path = str(result.get("path") or "")
        file_name = str(result.get("file_name") or os.path.basename(normalized_path))
        exc = result.get("error")
        if exc is not None:
            _mark_seen(normalized_path)
            _log("WARNING", f"Source archive metadata parse failed: {file_name}: {exc}")
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "source_parse_failed",
                    "issue_message": str(exc),
                    "source_path": normalized_path,
                }
            )
            return
        row = result.get("row")
        if row is None:
            _mark_seen(normalized_path)
            return
        rows.append(row)
        pending_rows.append(row)
        _mark_seen(str(row["file_path"]))
        if row.get("parse_status") in {"FAILED", "PARTIAL"}:
            _log(
                "WARNING",
                f"Source archive metadata {str(row.get('parse_status')).lower()}: "
                f"{os.path.basename(str(row.get('file_path') or normalized_path))}: {row.get('parse_error')}",
            )
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "source_parse_partial" if row.get("parse_status") == "PARTIAL" else "source_parse_failed",
                    "issue_message": row.get("parse_error"),
                    "source_path": row.get("file_path"),
                }
            )
        if entry_count - last_progress_count >= ASSET_SCAN_LOG_INTERVAL:
            _progress(
                "Scanning source archives: "
                f"candidates={entry_count}, skipped={skipped_unchanged}, parsed={len(rows)}, "
                f"completed={parse_completed}/{parse_attempts}, issues={len(issues)}"
            )
            last_progress_count = entry_count
        _emit_row_batch()

    _log(
        "INFO",
        "Source root discovery started: "
        f"{root.path} (workers={parse_workers}, inflight={parse_inflight}, "
        f"db_batch_size={row_batch_size}, parse_timeout={parse_timeout_seconds}s, "
        f"skip_unchanged_failures={skip_unchanged_failures})",
    )
    parse_pool = _SourceParseProcessPool(
        root_id=int(root.id or 0),
        root_path=root.path,
        workers=parse_workers,
        timeout_seconds=parse_timeout_seconds,
        log_callback=_log,
    )

    def _log_parse_wait_if_needed() -> None:
        nonlocal last_parse_wait_log_at
        now = time.monotonic()
        if now - last_parse_wait_log_at < 30:
            return
        _log(
            "INFO",
            "Source archive metadata parsing in progress: "
            f"pending={len(pending_indices)}, active_or_queued={parse_pool.active_count()}, "
            f"completed={parse_completed}/{parse_attempts}, timeout={parse_timeout_seconds}s",
        )
        last_parse_wait_log_at = now

    try:
        pending_indices: set[int] = set()
        for path in _iter_source_candidates(root.path):
            entry_count += 1
            normalized_path = _normalize_path(path)
            stat = _stat_path(normalized_path)
            cached = existing_by_path.get(normalized_path)
            if _cached_source_asset_is_unchanged(
                cached,
                stat,
                root,
                skip_unchanged_failures=skip_unchanged_failures,
            ):
                _mark_seen(normalized_path)
                skipped_unchanged += 1
                cached_status = str((cached or {}).get("parse_status") or "").upper()
                if cached_status == "OK":
                    skipped_unchanged_ok += 1
                else:
                    skipped_unchanged_failed += 1
                if skipped_unchanged % ASSET_SCAN_LOG_INTERVAL == 0:
                    _log(
                        "INFO",
                        "Skipped unchanged source archives: "
                        f"{skipped_unchanged} (ok={skipped_unchanged_ok}, failed_cached={skipped_unchanged_failed}, "
                        f"candidates={entry_count})",
                    )
                if entry_count - last_progress_count >= ASSET_SCAN_LOG_INTERVAL:
                    _progress(
                        "Scanning source archives: "
                        f"candidates={entry_count}, skipped={skipped_unchanged}, parsed={len(rows)}, "
                        f"completed={parse_completed}/{parse_attempts}, issues={len(issues)}"
                    )
                    last_progress_count = entry_count
                continue
            parse_attempts += 1
            file_name = os.path.basename(normalized_path)
            if parse_attempts <= ASSET_SCAN_DETAILED_PARSE_LOG_LIMIT or parse_attempts % ASSET_SCAN_LOG_INTERVAL == 0:
                _log("INFO", f"Extracting source archive metadata {parse_attempts}: {file_name}")
            if parse_attempts <= 50 or parse_attempts % 25 == 0:
                _progress(
                    "Extracting source archive metadata: "
                    f"{file_name} (changed/new={parse_attempts}, completed={parse_completed}, "
                    f"workers={parse_workers}, skipped={skipped_unchanged}, issue={len(issues)})"
                )
            parse_pool.submit(parse_attempts, normalized_path)
            pending_indices.add(parse_attempts)
            while parse_pool.active_count() >= parse_inflight:
                for result in parse_pool.drain(wait_for_one=True):
                    pending_indices.discard(int(result.get("index") or 0))
                    _handle_parse_result(result)
                _log_parse_wait_if_needed()
            for result in parse_pool.drain(wait_for_one=False):
                pending_indices.discard(int(result.get("index") or 0))
                _handle_parse_result(result)
        while pending_indices:
            drained = parse_pool.drain(wait_for_one=True)
            if not drained:
                _log_parse_wait_if_needed()
                continue
            for result in drained:
                pending_indices.discard(int(result.get("index") or 0))
                _handle_parse_result(result)
            _log_parse_wait_if_needed()
    finally:
        parse_pool.close()
    _emit_row_batch(force=True)
    _log(
        "INFO",
        "Source root discovery finished: "
        f"candidates={entry_count}, skipped={skipped_unchanged}, "
        f"skipped_ok={skipped_unchanged_ok}, skipped_failed_cached={skipped_unchanged_failed}, "
        f"changed_or_new={len(rows)}, parse_attempts={parse_attempts}, issues={len(issues)}",
    )
    _progress(
        "Source archive discovery finished: "
        f"candidates={entry_count}, skipped={skipped_unchanged}, changed_or_new={len(rows)}, "
        f"workers={parse_workers}"
    )
    return rows, issues, entry_count, skipped_unchanged, seen_paths


def _collect_orbit_assets_incremental(
    root: ManagedRootORM,
    existing_by_path: Dict[str, Dict[str, Any]],
    *,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    log_callback: Optional[Callable[[str, str], None]] = None,
    progress_start: int = 0,
    progress_end: int = 100,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, int, List[str]]:
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    seen_paths: List[str] = []
    entry_count = 0
    skipped_unchanged = 0
    parse_attempts = 0
    last_progress_count = 0

    def _log(level: str, message: str) -> None:
        if log_callback:
            log_callback(level, message)

    def _progress(message: str) -> None:
        if progress_callback:
            progress_callback(_activity_progress(progress_start, progress_end, entry_count), message)

    _log("INFO", f"Orbit root discovery started: {root.path}")
    for path in _iter_orbit_candidates(root.path):
        entry_count += 1
        normalized_path = _normalize_path(path)
        stat = _stat_path(normalized_path)
        if _cached_orbit_asset_is_unchanged(existing_by_path.get(normalized_path), stat, root):
            seen_paths.append(normalized_path)
            skipped_unchanged += 1
            if skipped_unchanged % ASSET_SCAN_LOG_INTERVAL == 0:
                _log(
                    "INFO",
                    f"Skipped unchanged orbit assets: {skipped_unchanged} (candidates={entry_count})",
                )
            if entry_count - last_progress_count >= ASSET_SCAN_LOG_INTERVAL:
                _progress(
                    "Scanning orbit assets: "
                    f"candidates={entry_count}, skipped={skipped_unchanged}, parsed={len(rows)}, issues={len(issues)}"
                )
                last_progress_count = entry_count
            continue
        parse_attempts += 1
        file_name = os.path.basename(normalized_path)
        if parse_attempts <= ASSET_SCAN_DETAILED_PARSE_LOG_LIMIT or parse_attempts % ASSET_SCAN_LOG_INTERVAL == 0:
            _log("INFO", f"Extracting orbit asset metadata {parse_attempts}: {file_name}")
        if parse_attempts <= 50 or parse_attempts % 25 == 0:
            _progress(
                "Extracting orbit asset metadata: "
                f"{file_name} (changed/new={parse_attempts}, skipped={skipped_unchanged})"
            )
        try:
            row = _parse_orbit_entry(normalized_path, root)
        except Exception as exc:
            row = None
            _log("WARNING", f"Orbit asset parse failed: {file_name}: {exc}")
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "orbit_parse_failed",
                    "issue_message": str(exc),
                    "source_path": normalized_path,
                }
            )
        if row is None:
            continue
        rows.append(row)
        seen_paths.append(str(row["file_path"]))
        if row.get("parse_status") in {"FAILED", "PARTIAL"}:
            _log(
                "WARNING",
                f"Orbit asset metadata {str(row.get('parse_status')).lower()}: "
                f"{os.path.basename(str(row.get('file_path') or normalized_path))}: {row.get('parse_error')}",
            )
            issues.append(
                {
                    "severity": "warning",
                    "issue_code": "orbit_parse_partial" if row.get("parse_status") == "PARTIAL" else "orbit_parse_failed",
                    "issue_message": row.get("parse_error"),
                    "source_path": row.get("file_path"),
                }
            )
        if entry_count - last_progress_count >= ASSET_SCAN_LOG_INTERVAL:
            _progress(
                "Scanning orbit assets: "
                f"candidates={entry_count}, skipped={skipped_unchanged}, parsed={len(rows)}, issues={len(issues)}"
            )
            last_progress_count = entry_count
    _log(
        "INFO",
        "Orbit root discovery finished: "
        f"candidates={entry_count}, skipped={skipped_unchanged}, changed_or_new={len(rows)}, issues={len(issues)}",
    )
    _progress(
        "Orbit asset discovery finished: "
        f"candidates={entry_count}, skipped={skipped_unchanged}, changed_or_new={len(rows)}"
    )
    return rows, issues, entry_count, skipped_unchanged, seen_paths


def _insar_source_ready(row: Dict[str, Any], coverage_polygon: Optional[List[Tuple[float, float]]]) -> Tuple[bool, Optional[str]]:
    reasons: List[str] = []
    metadata = dict(row.get("metadata_json") or {})
    if not coverage_polygon or len(coverage_polygon) < 3:
        reasons.append("missing_footprint")
    if not row.get("imaging_date"):
        reasons.append("missing_date")
    if not row.get("imaging_mode"):
        reasons.append("missing_imaging_mode")
    if not row.get("polarization"):
        reasons.append("missing_polarization")
    complex_tokens = {
        str(row.get("product_type") or "").strip().upper(),
        str(metadata.get("image_data_type") or "").strip().upper(),
        str(metadata.get("product_variant") or "").strip().upper(),
        str(metadata.get("filename_class_token") or "").strip().upper(),
        str(metadata.get("source_product_token") or "").strip().upper(),
    }
    if not complex_tokens.intersection({"COMPLEX", "SLC", "SSC"}):
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

    def _normalize_scan_families(self, families: Optional[Sequence[str]]) -> List[str]:
        normalized: List[str] = []
        for item in families or []:
            family = str(normalize_satellite_family(item) or item or "").strip().upper()
            if family and family not in normalized:
                normalized.append(family)
        return normalized

    def _scan_includes_type(self, inventory_types: Optional[Sequence[str]], *names: str) -> bool:
        type_set = {str(item or "").strip().lower() for item in (inventory_types or []) if str(item or "").strip()}
        if not type_set:
            return True
        return bool(type_set.intersection({str(name).strip().lower() for name in names}))

    def _normalize_family_filter(self, satellite_family: Optional[str]) -> List[str]:
        values: List[str] = []
        for raw in re.split(r"[,;]", str(satellite_family or "")):
            family = str(normalize_satellite_family(raw) or raw or "").strip().upper()
            if family and family not in values:
                values.append(family)
        return values

    def _thread_callbacks(
        self,
        task_id: Optional[str],
        loop: asyncio.AbstractEventLoop,
    ) -> Tuple[
        Optional[Callable[[int, str], None]],
        Optional[Callable[[str, str], None]],
        Callable[[], Awaitable[None]],
    ]:
        async def _noop() -> None:
            return None

        if not task_id:
            return None, None, _noop

        pending: List[Any] = []

        def _submit(coro: Any) -> None:
            pending.append(asyncio.run_coroutine_threadsafe(coro, loop))

        def _progress(progress: int, message: str) -> None:
            _submit(
                task_service.update_task(
                    task_id,
                    progress=max(0, min(100, int(progress))),
                    message=message,
                )
            )

        def _log(level: str, message: str) -> None:
            _submit(task_service.add_log(task_id, level, message))

        async def _drain() -> None:
            while pending:
                current = pending[:]
                pending.clear()
                await asyncio.gather(
                    *(asyncio.wrap_future(item) for item in current),
                    return_exceptions=True,
                )

        return _progress, _log, _drain

    async def build_archive_preview_caches(
        self,
        db: Optional[AsyncSession] = None,
        *,
        families: Optional[Sequence[str]] = None,
        limit: int = 0,
        force: bool = False,
        apply: bool = True,
        task_id: Optional[str] = None,
        progress_start: int = 84,
        progress_end: int = 96,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        progress_interval: int = 1,
    ) -> Dict[str, Any]:
        generated_session = db is None
        if generated_session:
            db = _new_session()
        assert db is not None

        family_map = {
            "LT1": {"LT1_ARCHIVE"},
            "S1": {"S1_ZIP"},
        }
        requested_families = self._normalize_scan_families(families) or ["LT1", "S1"]
        target_families = [item for item in requested_families if item in family_map]
        source_formats = sorted(
            {
                source_format
                for family in target_families
                for source_format in family_map.get(family, set())
            }
        )
        summary: Dict[str, Any] = {
            "records_seen": 0,
            "candidate_count": 0,
            "ready": 0,
            "cached": 0,
            "skipped_ready": 0,
            "failed": 0,
            "missing_source": 0,
            "raw_cached": 0,
            "raw_skipped": 0,
            "raw_failed": 0,
            "families": target_families,
        }
        if not target_families or not source_formats:
            await self._progress(task_id, "No LT1/S1 archive previews to build for selected families.", progress_end)
            return summary

        try:
            stmt = (
                select(RadarDataORM)
                .where(RadarDataORM.satellite_family.in_(target_families))
                .where(RadarDataORM.source_format.in_(source_formats))
                .order_by(RadarDataORM.satellite_family.asc(), RadarDataORM.id.asc())
            )
            result = await db.execute(stmt)
            records = list(result.scalars().all())
            summary["records_seen"] = len(records)
            candidates: List[RadarDataORM] = []
            for record in records:
                unique_id = record.unique_id or record.file_path
                raw_cache_path = DataService.get_radar_raw_cache_path(unique_id, record.file_path)
                geo_cache_path = DataService.get_radar_geo_cache_path(unique_id, record.file_path)
                ready = (
                    (record.preview_cache_status or "NONE") == "READY"
                    and (record.preview_cache_version or "") == settings.RADAR_GEO_CACHE_VERSION
                    and bool(record.preview_cache_path or geo_cache_path)
                    and os.path.exists(record.preview_cache_path or geo_cache_path)
                    and os.path.exists(raw_cache_path)
                )
                if ready and not force:
                    summary["skipped_ready"] += 1
                    continue
                candidates.append(record)

            if limit and limit > 0:
                candidates = candidates[: int(limit)]
            summary["candidate_count"] = len(candidates)
            if progress_callback:
                progress_callback(
                    {
                        "event": "planned",
                        "records_seen": summary["records_seen"],
                        "candidate_count": summary["candidate_count"],
                        "skipped_ready": summary["skipped_ready"],
                        "families": target_families,
                    }
                )
            if not apply:
                return summary
            if not candidates:
                await self._progress(
                    task_id,
                    f"Archive preview cache already ready: skipped={summary['skipped_ready']}",
                    progress_end,
                )
                if progress_callback:
                    progress_callback({"event": "completed", **summary})
                return summary

            await self._progress(
                task_id,
                f"Building archive preview cache: candidates={len(candidates)}, skipped_ready={summary['skipped_ready']}",
                progress_start,
            )
            thumb_size = (settings.RADAR_THUMBNAIL_MAX_SIZE, settings.RADAR_THUMBNAIL_MAX_SIZE)
            total = len(candidates)
            progress_interval = max(1, int(progress_interval or 1))
            for index, record in enumerate(candidates, start=1):
                unique_id = record.unique_id or record.file_path
                raw_cache_path = DataService.get_radar_raw_cache_path(unique_id, record.file_path)
                geo_cache_path = DataService.get_radar_geo_cache_path(unique_id, record.file_path)
                product_name = os.path.basename(str(record.file_path or ""))
                preview_source = await asyncio.to_thread(DataService.find_radar_preview_source, record.file_path)

                if not preview_source:
                    record.preview_cache_status = "NONE"
                    record.preview_cache_path = None
                    record.preview_cache_version = settings.RADAR_GEO_CACHE_VERSION
                    record.preview_cache_updated_at = _utcnow()
                    record.preview_cache_error = "preview_source_not_found"
                    summary["missing_source"] += 1
                    if task_id:
                        await task_service.add_log(task_id, "WARNING", f"Preview source missing: {product_name}")
                    db.add(record)
                else:
                    coverage_polygon = DataService._normalize_coverage_polygon(record.coverage_polygon)
                    try:
                        bbox = (
                            float(record.min_lon),
                            float(record.min_lat),
                            float(record.max_lon),
                            float(record.max_lat),
                        )
                        if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
                            bbox = None
                    except (TypeError, ValueError):
                        bbox = None

                    if not coverage_polygon or not bbox:
                        record.preview_cache_status = "FAILED"
                        record.preview_cache_path = None
                        record.preview_cache_version = settings.RADAR_GEO_CACHE_VERSION
                        record.preview_cache_updated_at = _utcnow()
                        record.preview_cache_error = "invalid_coverage_polygon" if not coverage_polygon else "invalid_bbox"
                        summary["failed"] += 1
                        if task_id:
                            await task_service.add_log(task_id, "ERROR", f"Preview geometry invalid: {product_name}: {record.preview_cache_error}")
                        db.add(record)
                    else:
                        source_corner_mapping = DataService.get_radar_record_corner_mapping(record)
                        ok_geo, geo_error = await asyncio.to_thread(
                            image_service.create_geocorrected_radar_cached_image,
                            preview_source,
                            geo_cache_path,
                            coverage_polygon,
                            bbox,
                            source_corner_mapping,
                            thumb_size,
                            settings.RADAR_GEO_CACHE_QUALITY,
                        )
                        ok_raw = await asyncio.to_thread(
                            image_service.create_radar_cached_image,
                            preview_source,
                            raw_cache_path,
                            thumb_size,
                        )
                        if ok_raw:
                            summary["raw_cached"] += 1
                        else:
                            summary["raw_failed"] += 1

                        if ok_geo and os.path.exists(geo_cache_path):
                            record.preview_cache_status = "READY"
                            record.preview_cache_path = geo_cache_path
                            record.preview_cache_error = None
                            summary["ready"] += 1
                            summary["cached"] += 1
                            if task_id:
                                await task_service.add_log(task_id, "INFO", f"Preview cache ready: {index}/{total} {product_name}")
                        else:
                            record.preview_cache_status = "FAILED"
                            record.preview_cache_path = None
                            record.preview_cache_error = geo_error or ("raw_cache_ready_only" if ok_raw else "preview_cache_build_failed")
                            summary["failed"] += 1
                            if task_id:
                                await task_service.add_log(task_id, "ERROR", f"Preview cache failed: {product_name}: {record.preview_cache_error}")
                        record.preview_cache_version = settings.RADAR_GEO_CACHE_VERSION
                        record.preview_cache_updated_at = _utcnow()
                        db.add(record)

                progress = progress_start + int(index / max(1, total) * max(1, progress_end - progress_start))
                await self._progress(
                    task_id,
                    f"Building archive preview cache ({index}/{total}): ready={summary['ready']}, failed={summary['failed']}, missing={summary['missing_source']}",
                    min(progress_end, progress),
                )
                if progress_callback and (
                    index == 1
                    or index == total
                    or index % progress_interval == 0
                    or (record.preview_cache_status or "").upper() == "FAILED"
                ):
                    progress_callback(
                        {
                            "event": "item",
                            "processed": index,
                            "total": total,
                            "records_seen": summary["records_seen"],
                            "candidate_count": summary["candidate_count"],
                            "skipped_ready": summary["skipped_ready"],
                            "ready": summary["ready"],
                            "cached": summary["cached"],
                            "failed": summary["failed"],
                            "missing_source": summary["missing_source"],
                            "raw_cached": summary["raw_cached"],
                            "raw_failed": summary["raw_failed"],
                            "product_name": product_name,
                            "status": record.preview_cache_status,
                            "error": record.preview_cache_error,
                        }
                    )
                await db.commit()

            await self._progress(
                task_id,
                (
                    "Archive preview cache completed: "
                    f"ready={summary['ready']}, skipped={summary['skipped_ready']}, "
                    f"failed={summary['failed']}, missing={summary['missing_source']}"
                ),
                progress_end,
            )
            if progress_callback:
                progress_callback({"event": "completed", **summary})
            return summary
        except Exception:
            if db is not None:
                await db.rollback()
            raise
        finally:
            if generated_session and db is not None:
                await db.close()

    async def _get_scan_roots(
        self,
        db: AsyncSession,
        *,
        inventory_types: Optional[Sequence[str]] = None,
        root_ids: Optional[Sequence[int]] = None,
        families: Optional[Sequence[str]] = None,
    ) -> List[ManagedRootORM]:
        type_set = {str(item or "").strip().lower() for item in (inventory_types or []) if str(item or "").strip()}
        family_set = {
            str(normalize_satellite_family(item) or item or "").strip().upper()
            for item in (families or [])
            if str(item or "").strip()
        }
        family_set.discard("")
        roles: List[str] = []
        if not type_set or "source_product" in type_set or "source" in type_set:
            roles.extend(
                [
                    "source_product_pool",
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
        roots = result.scalars().all()
        if family_set:
            roots = [
                root
                for root in roots
                if family_set.intersection(_root_supported_families(root))
            ]
        return roots

    async def scan_configured_roots(
        self,
        db: Optional[AsyncSession] = None,
        *,
        inventory_types: Optional[Sequence[str]] = None,
        root_ids: Optional[Sequence[int]] = None,
        families: Optional[Sequence[str]] = None,
        bind_orbits: bool = True,
        build_previews: bool = True,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        generated_session = db is None
        if generated_session:
            db = _new_session()
        assert db is not None

        try:
            await self._progress(task_id, "Preparing source/orbit asset scan...", 2)
            roots = await self._get_scan_roots(
                db,
                inventory_types=inventory_types,
                root_ids=root_ids,
                families=families,
            )
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
                progress = 5 + int((index - 1) / max(1, total_roots) * 78)
                next_progress = 5 + int(index / max(1, total_roots) * 78)
                await self._progress(task_id, f"Scanning {root.display_name}: {root.path}", progress)
                if root.root_role == "source_product_pool":
                    result = await self.scan_source_root(
                        db,
                        root,
                        task_id=task_id,
                        progress_start=progress,
                        progress_end=next_progress,
                    )
                    totals["source_roots"] += 1
                    totals["source_assets"] += int(result.get("asset_count") or 0)
                elif root.root_role == "orbit_asset_pool":
                    result = await self.scan_orbit_root(
                        db,
                        root,
                        task_id=task_id,
                        progress_start=progress,
                        progress_end=next_progress,
                    )
                    totals["orbit_roots"] += 1
                    totals["orbit_assets"] += int(result.get("asset_count") or 0)
                else:
                    continue
                await self._progress(
                    task_id,
                    f"Finished {root.display_name}: assets={result.get('asset_count', 0)}, issues={result.get('issue_count', 0)}",
                    max(progress, next_progress - 1),
                )
                totals["issues"] += int(result.get("issue_count") or 0)
                if result.get("status") == "INACCESSIBLE":
                    totals["inaccessible_roots"] += 1
                results.append(result)
                await db.commit()

            binding_summary: Dict[str, Any] = {}
            if bind_orbits:
                await self._progress(task_id, "Binding scenes to precise orbit assets...", 84)
                binding_summary = await self.bind_scene_orbits(db)
                await db.commit()

            preview_summary: Dict[str, Any] = {}
            should_build_previews = (
                build_previews
                and totals["source_roots"] > 0
                and self._scan_includes_type(inventory_types, "source_product", "source")
            )
            if should_build_previews:
                preview_summary = await self.build_archive_preview_caches(
                    db,
                    families=families,
                    task_id=task_id,
                    progress_start=88,
                    progress_end=98,
                )
                await db.commit()

            summary = {
                "message": "Asset inventory scan completed",
                "root_count": total_roots,
                **totals,
                "binding": binding_summary,
                "preview_cache": preview_summary,
                "results": results,
            }
            await self._progress(task_id, "Asset inventory scan completed", 100)
            return summary
        except Exception as exc:
            if db is not None:
                await db.rollback()
                if "roots" in locals():
                    try:
                        await self._fail_running_states_for_roots(
                            db,
                            roots,
                            inventory_types=inventory_types,
                            error=str(exc),
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
            raise
        finally:
            if generated_session and db is not None:
                await db.close()

    async def audit_source_archive_integrity(
        self,
        db: Optional[AsyncSession] = None,
        *,
        families: Optional[Sequence[str]] = None,
        source_formats: Optional[Sequence[str]] = None,
        asset_ids: Optional[Sequence[int]] = None,
        force: bool = False,
        limit: Optional[int] = None,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        generated_session = db is None
        if generated_session:
            db = _new_session()
        assert db is not None

        safe_limit: Optional[int] = None
        if limit is not None:
            try:
                parsed_limit = int(limit)
                safe_limit = parsed_limit if parsed_limit > 0 else None
            except (TypeError, ValueError):
                safe_limit = None

        family_filter = self._normalize_scan_families(families)
        format_filter = [
            str(item or "").strip().upper()
            for item in (source_formats or [])
            if str(item or "").strip()
        ]
        if not format_filter:
            format_filter = ["LT1_ARCHIVE", "S1_ZIP"]
        format_filter = [item for item in format_filter if item in {"LT1_ARCHIVE", "S1_ZIP", "GF3_ARCHIVE"}]
        if not format_filter:
            format_filter = ["LT1_ARCHIVE", "S1_ZIP"]

        try:
            await self._progress(task_id, "Preparing source archive integrity audit...", 2)
            filters = [
                SourceProductAssetORM.is_active == True,  # noqa: E712
                SourceProductAssetORM.source_format.in_(format_filter),
            ]
            if family_filter:
                filters.append(SourceProductAssetORM.satellite_family.in_(family_filter))
            if asset_ids:
                filters.append(SourceProductAssetORM.id.in_([int(item) for item in asset_ids]))
            stmt = (
                select(SourceProductAssetORM)
                .where(*filters)
                .order_by(
                    SourceProductAssetORM.satellite_family.asc().nullslast(),
                    SourceProductAssetORM.imaging_date.asc().nullslast(),
                    SourceProductAssetORM.id.asc(),
                )
            )
            if safe_limit:
                stmt = stmt.limit(safe_limit)
            rows = (await db.execute(stmt)).scalars().all()
            total = len(rows)
            summary: Dict[str, Any] = {
                "message": "Source archive integrity audit completed",
                "total": total,
                "checked": 0,
                "skipped": 0,
                "ok": 0,
                "failed": 0,
                "unsupported": 0,
                "missing": 0,
                "force": bool(force),
                "families": family_filter,
                "source_formats": format_filter,
                "version": ARCHIVE_INTEGRITY_VERSION,
            }
            if total <= 0:
                await self._progress(task_id, "No source archives matched integrity audit filters.", 100)
                return summary
            if task_id:
                await task_service.add_log(
                    task_id,
                    "INFO",
                    (
                        "Source archive integrity audit candidates: "
                        f"total={total}, families={family_filter or 'ALL'}, formats={format_filter}, force={bool(force)}"
                    ),
                )

            for index, asset in enumerate(rows, start=1):
                file_path = _normalize_path(asset.file_path or "")
                file_name = os.path.basename(file_path) or str(asset.logical_product_uid or asset.id)
                progress = 5 + int((index - 1) / max(1, total) * 90)
                stat = _stat_path(file_path)
                unchanged = (
                    _same_size(asset.size_bytes, stat.get("size_bytes"))
                    and _same_mtime(asset.mtime_epoch, stat.get("mtime_epoch"))
                )
                previous_status = str(asset.archive_integrity_status or "NOT_CHECKED").upper()
                previous_version = str(asset.archive_integrity_version or "")
                can_skip = (
                    not force
                    and unchanged
                    and previous_version == ARCHIVE_INTEGRITY_VERSION
                    and previous_status in {"OK", "FAILED", "UNSUPPORTED"}
                )
                if can_skip:
                    summary["skipped"] += 1
                    if previous_status == "OK":
                        summary["ok"] += 1
                    elif previous_status == "FAILED":
                        summary["failed"] += 1
                    elif previous_status == "UNSUPPORTED":
                        summary["unsupported"] += 1
                    if index <= 5 or index % ARCHIVE_INTEGRITY_LOG_INTERVAL == 0 or index == total:
                        await self._progress(
                            task_id,
                            f"Skipping unchanged archive integrity {index}/{total}: {file_name}",
                            5 + int(index / max(1, total) * 90),
                        )
                    if task_id and (summary["skipped"] <= 5 or summary["skipped"] % ARCHIVE_INTEGRITY_LOG_INTERVAL == 0):
                        await task_service.add_log(
                            task_id,
                            "INFO",
                            f"Skipped unchanged archive integrity: {summary['skipped']} skipped, {file_name}, status={previous_status}",
                        )
                    continue

                await self._progress(
                    task_id,
                    f"Checking archive integrity {index}/{total}: {file_name}",
                    progress,
                )
                if task_id:
                    await task_service.add_log(
                        task_id,
                        "INFO",
                        f"Checking archive integrity {index}/{total}: {file_path}",
                    )
                result = await asyncio.to_thread(_check_archive_integrity, file_path, asset.source_format)
                checked_at = _utcnow()
                status = str(result.get("status") or "FAILED").upper()
                asset.size_bytes = result.get("size_bytes")
                asset.mtime_epoch = result.get("mtime_epoch")
                asset.archive_integrity_status = status
                asset.archive_integrity_method = result.get("method")
                asset.archive_integrity_checked_at = checked_at
                asset.archive_integrity_error = result.get("error")
                asset.archive_integrity_version = ARCHIVE_INTEGRITY_VERSION
                asset.archive_integrity_member_count = result.get("member_count")
                asset.updated_at = checked_at
                db.add(asset)
                summary["checked"] += 1
                if status == "OK":
                    summary["ok"] += 1
                    await self._resolve_archive_integrity_issue(db, asset, now=checked_at)
                elif status == "UNSUPPORTED":
                    summary["unsupported"] += 1
                    await self._resolve_archive_integrity_issue(db, asset, now=checked_at)
                else:
                    summary["failed"] += 1
                    if str(result.get("error") or "").lower().startswith("archive file is missing"):
                        summary["missing"] += 1
                    await self._record_archive_integrity_issue(db, asset, result, now=checked_at)
                await db.commit()
                if task_id:
                    level = "INFO" if status == "OK" else "WARNING" if status == "UNSUPPORTED" else "ERROR"
                    duration = result.get("duration_seconds")
                    detail = (
                        f"Archive integrity {status}: {file_name}, "
                        f"members={result.get('member_count')}, duration={duration}s"
                    )
                    if result.get("error"):
                        detail += f", error={result.get('error')}"
                    await task_service.add_log(task_id, level, detail)

            await self._progress(
                task_id,
                (
                    "Source archive integrity audit completed: "
                    f"checked={summary['checked']}, skipped={summary['skipped']}, "
                    f"ok={summary['ok']}, failed={summary['failed']}, unsupported={summary['unsupported']}"
                ),
                100,
            )
            return summary
        except Exception:
            if db is not None:
                await db.rollback()
            raise
        finally:
            if generated_session and db is not None:
                await db.close()

    async def scan_source_root(
        self,
        db: AsyncSession,
        root: ManagedRootORM,
        *,
        task_id: Optional[str] = None,
        progress_start: int = 0,
        progress_end: int = 100,
    ) -> Dict[str, Any]:
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

        existing_result = await db.execute(
            select(SourceProductAssetORM).where(SourceProductAssetORM.root_ref_id == root.id)
        )
        existing_by_path = {
            str(asset.file_path): {
                "root_ref_id": asset.root_ref_id,
                "source_format": asset.source_format,
                "size_bytes": asset.size_bytes,
                "mtime_epoch": asset.mtime_epoch,
                "parser_version": asset.parser_version,
                "parse_status": asset.parse_status,
                "is_active": bool(asset.is_active),
            }
            for asset in existing_result.scalars().all()
            if asset.file_path
        }

        loop = asyncio.get_running_loop()
        progress_callback, log_callback, drain_thread_events = self._thread_callbacks(task_id, loop)
        batch_write_start = max(progress_start, progress_end - 10)
        batch_write_end = max(batch_write_start, progress_end - 3)
        persisted_changed_count = 0

        async def _upsert_source_asset_batch(rows_batch: Sequence[Dict[str, Any]], *, batch_index: int) -> int:
            if not rows_batch:
                return 0
            now = _utcnow()
            db_rows = [
                {key: value for key, value in row.items() if not str(key).startswith("_")}
                for row in rows_batch
            ]
            for row in db_rows:
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
                        "archive_integrity_status": "NOT_CHECKED",
                        "archive_integrity_method": None,
                        "archive_integrity_checked_at": None,
                        "archive_integrity_error": None,
                        "archive_integrity_version": None,
                        "archive_integrity_member_count": None,
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

            changed_paths = [str(row["file_path"]) for row in rows_batch if row.get("file_path")]
            asset_ids_by_path: Dict[str, int] = {}
            if changed_paths:
                result = await db.execute(
                    select(SourceProductAssetORM.file_path, SourceProductAssetORM.id).where(
                        SourceProductAssetORM.file_path.in_(changed_paths)
                    )
                )
                asset_ids_by_path = {str(path): int(asset_id) for path, asset_id in result.all()}
                await self._upsert_metadata_documents_for_source_assets(db, rows_batch, asset_ids_by_path)
                await self._upsert_radar_records_for_source_assets(db, rows_batch, asset_ids_by_path)

            await db.commit()
            if task_id:
                await task_service.add_log(
                    task_id,
                    "INFO",
                    f"Source asset DB batch committed: batch={batch_index}, rows={len(rows_batch)}",
                )
                await self._progress(
                    task_id,
                    f"Committed source asset DB batch {batch_index}: rows={len(rows_batch)}",
                    batch_write_start,
                )
            return len(rows_batch)

        batch_index = 0

        def _persist_row_batch(rows_batch: List[Dict[str, Any]]) -> None:
            nonlocal batch_index, persisted_changed_count
            if not rows_batch:
                return
            batch_index += 1
            future = asyncio.run_coroutine_threadsafe(
                _upsert_source_asset_batch(rows_batch, batch_index=batch_index),
                loop,
            )
            persisted = int(future.result())
            persisted_changed_count += persisted

        rows, issues, entry_count, skipped_unchanged, seen_paths = await asyncio.to_thread(
            _collect_source_assets_incremental,
            root,
            existing_by_path,
            progress_callback=progress_callback,
            log_callback=log_callback,
            progress_start=progress_start,
            progress_end=max(progress_start + 1, progress_end - 8),
            parse_workers=settings.ASSET_SCAN_PARSE_WORKERS,
            parse_inflight=settings.ASSET_SCAN_PARSE_INFLIGHT,
            skip_unchanged_failures=settings.ASSET_SCAN_SKIP_UNCHANGED_FAILURES,
            row_batch_callback=_persist_row_batch if task_id else None,
            row_batch_size=settings.ASSET_SCAN_DB_BATCH_SIZE,
        )
        await drain_thread_events()
        if not task_id and rows:
            persisted_changed_count += await _upsert_source_asset_batch(rows, batch_index=1)
        now = _utcnow()
        if task_id:
            await task_service.add_log(
                task_id,
                "INFO",
                "Source asset DB batch upsert finished: "
                f"changed_or_new={len(rows)}, persisted={persisted_changed_count}, "
                f"batches={batch_index}, skipped={skipped_unchanged}, seen={len(seen_paths)}",
            )

        await self._progress(task_id, "Marking missing source assets and refreshing scan issues...", max(progress_end - 2, progress_start))
        await self._mark_missing_source_assets(db, root, seen_paths, now)
        await self._replace_root_issues(db, root, "source_product", issues)
        await self._finish_state(
            db,
            state,
            status="OK" if not any(item.get("severity") == "error" for item in issues) else "WARNING",
            started_at=started_at,
            entry_count=entry_count,
            asset_count=len(seen_paths),
            issue_count=len(issues),
            error=None,
        )
        if task_id:
            await task_service.add_log(
                task_id,
                "INFO",
                "Source root scan summary: "
                f"path={root.path}, candidates={entry_count}, active={len(seen_paths)}, "
                f"changed_or_new={len(rows)}, skipped={skipped_unchanged}, issues={len(issues)}",
            )
        return {
            "root_id": root.id,
            "root_path": root.path,
            "inventory_type": "source_product",
            "status": state.status,
            "entry_count": entry_count,
            "asset_count": len(seen_paths),
            "changed_asset_count": len(rows),
            "unchanged_asset_count": skipped_unchanged,
            "issue_count": len(issues),
        }

    async def scan_orbit_root(
        self,
        db: AsyncSession,
        root: ManagedRootORM,
        *,
        task_id: Optional[str] = None,
        progress_start: int = 0,
        progress_end: int = 100,
    ) -> Dict[str, Any]:
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

        existing_result = await db.execute(
            select(OrbitAssetORM).where(OrbitAssetORM.root_ref_id == root.id)
        )
        existing_by_path = {
            str(asset.file_path): {
                "root_ref_id": asset.root_ref_id,
                "native_format": asset.native_format,
                "size_bytes": asset.size_bytes,
                "mtime_epoch": asset.mtime_epoch,
                "parser_version": asset.parser_version,
                "parse_status": asset.parse_status,
                "is_active": bool(asset.is_active),
            }
            for asset in existing_result.scalars().all()
            if asset.file_path
        }

        loop = asyncio.get_running_loop()
        progress_callback, log_callback, drain_thread_events = self._thread_callbacks(task_id, loop)
        rows, issues, entry_count, skipped_unchanged, seen_paths = await asyncio.to_thread(
            _collect_orbit_assets_incremental,
            root,
            existing_by_path,
            progress_callback=progress_callback,
            log_callback=log_callback,
            progress_start=progress_start,
            progress_end=max(progress_start + 1, progress_end - 6),
        )
        await drain_thread_events()
        now = _utcnow()
        write_start = max(progress_start, progress_end - 5)
        write_end = max(write_start, progress_end - 2)
        await self._progress(
            task_id,
            f"Writing orbit asset index: changed_or_new={len(rows)}, skipped={skipped_unchanged}",
            write_start,
        )
        if task_id:
            await task_service.add_log(
                task_id,
                "INFO",
                f"Orbit asset DB upsert started: changed_or_new={len(rows)}, skipped={skipped_unchanged}, seen={len(seen_paths)}, issues={len(issues)}",
            )
        for index, row in enumerate(rows, start=1):
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
            if index % ASSET_SCAN_LOG_INTERVAL == 0 or index == len(rows):
                progress_value = write_start
                if rows and write_end > write_start:
                    progress_value = write_start + int(index / max(1, len(rows)) * (write_end - write_start))
                await self._progress(
                    task_id,
                    f"Writing orbit asset index: {index}/{len(rows)} changed_or_new, skipped={skipped_unchanged}",
                    progress_value,
                )
        await db.flush()

        derivative_summary: Dict[str, Any] = {}
        has_lt1_seen = any(
            str(existing_by_path.get(path, {}).get("native_format") or "").upper() == "TXT"
            for path in seen_paths
        ) or any(row.get("satellite_family") == "LT1" for row in rows)
        if has_lt1_seen and settings.ORBIT_POOL_ENVI:
            await self._progress(task_id, "Syncing LT-1 TXT orbit production pool...", max(progress_end - 3, progress_start))
            isce2_pool = settings.ORBIT_POOL_ISCE2 if settings.ISCE2_ENABLED else ""
            try:
                derivative_summary = await asyncio.to_thread(
                    sync_orbit_pools,
                    root.path,
                    settings.ORBIT_POOL_ENVI,
                    isce2_pool,
                    settings.ORBIT_POOL_LANDSAR,
                    bool(settings.ISCE2_ENABLED),
                )
            except Exception as exc:
                derivative_summary = {"error": str(exc)}
                issues.append(
                    {
                        "severity": "warning",
                        "issue_code": "lt1_orbit_pool_sync_failed",
                        "issue_message": str(exc),
                        "source_path": root.path,
                    }
                )

            derivative_summary["db_derivatives"] = await self._record_lt1_orbit_pool_derivatives_for_paths(db, seen_paths, now=now)
            if task_id:
                envi_summary = derivative_summary.get("envi") or {}
                isce2_summary = derivative_summary.get("isce2") or {}
                db_derivatives = derivative_summary.get("db_derivatives") or {}
                await task_service.add_log(
                    task_id,
                    "INFO" if not derivative_summary.get("error") else "WARN",
                    (
                        "LT-1 orbit production pool sync: "
                        f"txt copied={len(envi_summary.get('copied', []) or [])}, "
                        f"updated={len(envi_summary.get('updated', []) or [])}, "
                        f"skipped={len(envi_summary.get('skipped', []) or [])}; "
                        + (
                            f"isce2 converted={len(isce2_summary.get('converted', []) or [])}, "
                            f"reconverted={len(isce2_summary.get('reconverted', []) or [])}; "
                            if settings.ISCE2_ENABLED
                            else "isce2 disabled; "
                        )
                        +
                        f"derivatives recorded={int(db_derivatives.get('recorded') or 0)}"
                    ),
                )

        await self._progress(task_id, "Marking missing orbit assets and refreshing scan issues...", max(progress_end - 2, progress_start))
        await self._mark_missing_orbit_assets(db, root, seen_paths, now)
        await self._replace_root_issues(db, root, "orbit_asset", issues)
        await self._finish_state(
            db,
            state,
            status="OK" if not any(item.get("severity") == "error" for item in issues) else "WARNING",
            started_at=started_at,
            entry_count=entry_count,
            asset_count=len(seen_paths),
            issue_count=len(issues),
            error=None,
        )
        if task_id:
            await task_service.add_log(
                task_id,
                "INFO",
                "Orbit root scan summary: "
                f"path={root.path}, candidates={entry_count}, active={len(seen_paths)}, "
                f"changed_or_new={len(rows)}, skipped={skipped_unchanged}, issues={len(issues)}",
            )
        return {
            "root_id": root.id,
            "root_path": root.path,
            "inventory_type": "orbit_asset",
            "status": state.status,
            "entry_count": entry_count,
            "asset_count": len(seen_paths),
            "changed_asset_count": len(rows),
            "unchanged_asset_count": skipped_unchanged,
            "issue_count": len(issues),
            "derivative_summary": derivative_summary,
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
        source_ref: str = "TASK_POOL_ROOT",
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
        source_ref_text = str(source_ref or "TASK_POOL_ROOT").strip() or "TASK_POOL_ROOT"
        source_ref_slug = re.sub(r"[^a-z0-9]+", "_", source_ref_text.lower()).strip("_") or "task_pool_root"
        root_code = f"source_product_pool__{source_ref_slug}_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:12]}"
        root = ManagedRootORM(
            root_code=root_code,
            root_role="source_product_pool",
            display_name="Task Pool Materialized Source Pool" if source_ref_text == "TASK_POOL_ROOT" else "Source Product Pool",
            path=normalized,
            path_kind=_path_kind(normalized),
            source_kind="env",
            source_ref=source_ref_text,
            scan_mode="file_pool",
            enabled=True,
            exists_flag=os.path.exists(normalized),
            metadata_json={
                "env_var": source_ref_text,
                "created_by": "source_materialize",
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
        db_row = {key: value for key, value in row.items() if not str(key).startswith("_")}
        stmt = pg_insert(SourceProductAssetORM).values(db_row)
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
                "archive_integrity_status": "NOT_CHECKED",
                "archive_integrity_method": None,
                "archive_integrity_checked_at": None,
                "archive_integrity_error": None,
                "archive_integrity_version": None,
                "archive_integrity_member_count": None,
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
            await self._upsert_metadata_documents_for_source_assets(db, [row], {path: int(asset_id)})
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
        _ensure_local_runtime_path(archive, "Sentinel-1 archive source")
        if not os.path.isfile(archive):
            raise FileNotFoundError(archive)
        if not archive.lower().endswith(".zip"):
            raise ValueError("Only Sentinel-1 ZIP archives can be unpacked.")

        target_dir = _ensure_local_runtime_path(
            _target_root_for_s1_archive(archive, target_root),
            "Sentinel-1 unpack target_root",
        )
        os.makedirs(target_dir, exist_ok=True)
        tmp_suffix_text = str(tmp_suffix or os.getenv("UNPACK_TMP_SUFFIX") or ".unpack_tmp").strip() or ".unpack_tmp"
        min_free_gb = min_disk_space_gb
        if min_free_gb is None:
            min_free_gb = _parse_float(os.getenv("UNPACK_MIN_DISK_SPACE_GB"), 50.0)
        def _log(level: str, message: str) -> None:
            if log_callback:
                log_callback(level, message)

        def _progress(progress: int, message: str) -> None:
            if progress_callback:
                progress_callback(progress, message)

        if delete_archive:
            _log("WARNING", "Ignoring delete_archive=true; Sentinel-1 ZIP archives are the source of record.")

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
        _ensure_local_runtime_path(source_path, "Source asset path")
        if source_format == "S1_ZIP":
            requested_root = _normalize_path(target_root or "") or _task_pool_materialize_root(source_format)
            requested_root = _ensure_local_runtime_path(requested_root, "Source materialize target_root")
            return self.unpack_sentinel1_archive(source_path, target_root=requested_root, overwrite=overwrite)
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
            requested_root = _task_pool_materialize_root(source_format)
        requested_root = _ensure_local_runtime_path(requested_root, "Source materialize target_root")
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
            target_root = _ensure_local_runtime_path(
                payload.get("target_root") or _task_pool_materialize_root("S1_ZIP"),
                "Sentinel-1 unpack target_root",
            )
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
                target_root_text = str(result["target_root"])
                await self.ensure_source_root_for_path(
                    db,
                    target_root_text,
                    source_ref=_source_ref_for_materialized_root(target_root_text),
                )
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
        target_root = _ensure_local_runtime_path(
            payload.get("target_root") or _task_pool_materialize_root("S1_ZIP"),
            "Sentinel-1 batch unpack target_root",
        )
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
                        target_root_text = str(result["target_root"])
                        await self.ensure_source_root_for_path(
                            db,
                            target_root_text,
                            source_ref=_source_ref_for_materialized_root(target_root_text),
                        )
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

    async def _fail_running_states_for_roots(
        self,
        db: AsyncSession,
        roots: Sequence[ManagedRootORM],
        *,
        inventory_types: Optional[Sequence[str]],
        error: str,
    ) -> None:
        now = _utcnow()
        values = {
            "status": "FAILED",
            "last_scan_finished_at": now,
            "last_error": str(error or "Asset inventory scan failed"),
            "needs_rescan": True,
            "updated_at": now,
        }
        for root in roots:
            inventory_type: Optional[str] = None
            if root.root_role == "source_product_pool":
                inventory_type = "source_product"
            elif root.root_role == "orbit_asset_pool":
                inventory_type = "orbit_asset"
            if not inventory_type or not self._scan_includes_type(inventory_types, inventory_type):
                continue
            await db.execute(
                update(AssetInventoryStateORM)
                .where(
                    AssetInventoryStateORM.root_ref_id == root.id,
                    AssetInventoryStateORM.inventory_type == inventory_type,
                    AssetInventoryStateORM.status == "RUNNING",
                )
                .values(**values)
            )

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

    async def _resolve_archive_integrity_issue(
        self,
        db: AsyncSession,
        asset: SourceProductAssetORM,
        *,
        now: datetime,
    ) -> None:
        if asset.id is None:
            return
        await db.execute(
            update(AssetInventoryIssueORM)
            .where(
                AssetInventoryIssueORM.asset_ref_id == int(asset.id),
                AssetInventoryIssueORM.inventory_type == "source_product",
                AssetInventoryIssueORM.issue_code == "source_archive_integrity_failed",
                AssetInventoryIssueORM.status == "OPEN",
            )
            .values(status="RESOLVED", resolved_at=now, last_seen_at=now)
        )

    async def _record_archive_integrity_issue(
        self,
        db: AsyncSession,
        asset: SourceProductAssetORM,
        result: Dict[str, Any],
        *,
        now: datetime,
    ) -> None:
        if asset.id is None:
            return
        await self._resolve_archive_integrity_issue(db, asset, now=now)
        db.add(
            AssetInventoryIssueORM(
                root_ref_id=asset.root_ref_id,
                inventory_type="source_product",
                asset_ref_id=int(asset.id),
                severity="error",
                issue_code="source_archive_integrity_failed",
                issue_message=result.get("error") or "Source archive integrity check failed.",
                source_path=asset.file_path,
                status="OPEN",
                first_seen_at=now,
                last_seen_at=now,
                metadata_json={
                    "asset_uid": asset.asset_uid,
                    "logical_product_uid": asset.logical_product_uid,
                    "satellite_family": asset.satellite_family,
                    "source_format": asset.source_format,
                    "method": result.get("method"),
                    "member_count": result.get("member_count"),
                    "duration_seconds": result.get("duration_seconds"),
                    "version": ARCHIVE_INTEGRITY_VERSION,
                },
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

    async def _record_lt1_orbit_pool_derivatives(
        self,
        db: AsyncSession,
        rows: Sequence[Dict[str, Any]],
        *,
        now: datetime,
    ) -> Dict[str, Any]:
        lt1_rows = [row for row in rows if row.get("satellite_family") == "LT1"]
        if not lt1_rows or not settings.ORBIT_POOL_ENVI:
            return {"recorded": 0, "missing": 0}

        paths = [str(row.get("file_path") or "") for row in lt1_rows if row.get("file_path")]
        result = await db.execute(select(OrbitAssetORM).where(OrbitAssetORM.file_path.in_(paths)))
        assets_by_path = {str(asset.file_path): asset for asset in result.scalars().all()}

        recorded = 0
        missing = 0
        for row in lt1_rows:
            asset = assets_by_path.get(str(row.get("file_path") or ""))
            if not asset or not asset.id:
                continue
            satellite = str(row.get("satellite") or "").upper()
            file_name = str(row.get("file_name") or "").strip()
            pool_path = _normalize_path(os.path.join(settings.ORBIT_POOL_ENVI, satellite, file_name))
            if not os.path.isfile(pool_path):
                missing += 1
                continue
            stat = _stat_path(pool_path)
            stmt = pg_insert(OrbitAssetDerivativeORM).values(
                orbit_asset_id=int(asset.id),
                engine_code="lt1_txt_pool",
                derivative_format="LT1_TXT",
                derivative_role="production_orbit_txt",
                pool_path=pool_path,
                size_bytes=stat.get("size_bytes"),
                mtime_epoch=stat.get("mtime_epoch"),
                checksum_sha256=None,
                generation_status="READY",
                generation_error=None,
                generated_at=now,
                metadata_json=_json_safe(
                    {
                        "pool_root": settings.ORBIT_POOL_ENVI,
                        "layout": "satellite_split",
                        "consumers": ["ENVI/SARscape", "Gamma/PyINT D-InSAR", "Gamma SBAS"],
                    }
                ),
                created_at=now,
                updated_at=now,
            )
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["orbit_asset_id", "engine_code", "derivative_format", "pool_path"],
                set_={
                    "derivative_role": excluded.derivative_role,
                    "size_bytes": excluded.size_bytes,
                    "mtime_epoch": excluded.mtime_epoch,
                    "generation_status": "READY",
                    "generation_error": None,
                    "generated_at": now,
                    "metadata_json": excluded.metadata_json,
                    "updated_at": now,
                },
            )
            await db.execute(stmt)
            recorded += 1
        return {"recorded": recorded, "missing": missing, "pool_root": settings.ORBIT_POOL_ENVI}

    async def _record_lt1_orbit_pool_derivatives_for_paths(
        self,
        db: AsyncSession,
        paths: Sequence[str],
        *,
        now: datetime,
    ) -> Dict[str, Any]:
        unique_paths = []
        seen = set()
        for path in paths:
            normalized = _normalize_path(str(path or ""))
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_paths.append(normalized)
        if not unique_paths or not settings.ORBIT_POOL_ENVI:
            return {"recorded": 0, "missing": 0}

        result = await db.execute(
            select(OrbitAssetORM).where(
                OrbitAssetORM.file_path.in_(unique_paths),
                OrbitAssetORM.satellite_family == "LT1",
                OrbitAssetORM.is_active == True,  # noqa: E712
            )
        )
        rows = [
            {
                "satellite_family": asset.satellite_family,
                "satellite": asset.satellite,
                "file_name": asset.file_name,
                "file_path": asset.file_path,
            }
            for asset in result.scalars().all()
        ]
        return await self._record_lt1_orbit_pool_derivatives(db, rows, now=now)

    async def _upsert_radar_records_for_source_assets(
        self,
        db: AsyncSession,
        rows: Sequence[Dict[str, Any]],
        asset_ids_by_path: Dict[str, int],
    ) -> None:
        dirty_scene_ids: List[int] = []
        profile_inputs: List[Tuple[Dict[str, Any], int, int]] = []
        for row in rows:
            metadata = dict(row.get("metadata_json") or {})
            coverage_polygon = _ordered_closed_polygon(metadata.get("coverage_polygon") or [])
            if not coverage_polygon or len(coverage_polygon) < 3:
                continue
            metadata["coverage_polygon"] = coverage_polygon
            metadata["coverage_bbox"] = _bbox_from_polygon(coverage_polygon)
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
                scene = RadarDataORM(
                    unique_id=f"asset:{row.get('asset_uid')}",
                    has_orbit_data=False,
                    orbit_binding_status="UNBOUND",
                    is_envi_processed=False,
                    **radar_values,
                )
                db.add(scene)
                await db.flush()
                if scene.id is not None:
                    dirty_scene_ids.append(int(scene.id))
                    profile_inputs.append((row, asset_id, int(scene.id)))
            else:
                before_orbit_id = existing.selected_orbit_asset_id
                for key, value in radar_values.items():
                    setattr(existing, key, value)
                if not existing.orbit_binding_status:
                    existing.orbit_binding_status = "UNBOUND"
                db.add(existing)
                if existing.id is not None:
                    profile_inputs.append((row, asset_id, int(existing.id)))
                if existing.id is not None and before_orbit_id != existing.selected_orbit_asset_id:
                    dirty_scene_ids.append(int(existing.id))

        await db.flush()
        if profile_inputs:
            await self._upsert_geometry_profiles(db, profile_inputs)
            await self._attach_radar_ids_to_metadata_documents(db, profile_inputs)
            for _, _, radar_id in profile_inputs:
                if radar_id not in dirty_scene_ids:
                    dirty_scene_ids.append(radar_id)
        if dirty_scene_ids:
            await pairing_state_service.mark_scenes_dirty(db, scene_ids=dirty_scene_ids, reason="asset_inventory_source_update", commit=False)

    async def _upsert_metadata_documents_for_source_assets(
        self,
        db: AsyncSession,
        rows: Sequence[Dict[str, Any]],
        asset_ids_by_path: Dict[str, int],
    ) -> None:
        now = _utcnow()
        for row in rows:
            asset_id = asset_ids_by_path.get(str(row.get("file_path")))
            if not asset_id:
                continue
            for doc in row.get("_metadata_documents") or []:
                values = {
                    "source_asset_id": int(asset_id),
                    "satellite_family": doc.get("satellite_family") or row.get("satellite_family"),
                    "source_format": doc.get("source_format") or row.get("source_format"),
                    "document_type": doc.get("document_type") or "UNKNOWN",
                    "member_path": doc.get("member_path") or "",
                    "content_sha256": doc.get("content_sha256") or "",
                    "content_encoding": doc.get("content_encoding") or "gzip",
                    "content_bytes": doc.get("content_bytes") or b"",
                    "content_size_bytes": doc.get("content_size_bytes"),
                    "archive_path": doc.get("archive_path") or row.get("archive_path") or row.get("file_path"),
                    "archive_mtime": doc.get("archive_mtime") if doc.get("archive_mtime") is not None else row.get("mtime_epoch"),
                    "parser_version": doc.get("parser_version") or PARSER_VERSION,
                    "parse_status": doc.get("parse_status") or "OK",
                    "parse_error": doc.get("parse_error"),
                    "extracted_at": doc.get("extracted_at") or now,
                    "updated_at": now,
                }
                stmt = pg_insert(SourceMetadataDocumentORM).values(values)
                excluded = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_source_metadata_document_member",
                    set_={
                        "radar_data_id": excluded.radar_data_id,
                        "satellite_family": excluded.satellite_family,
                        "source_format": excluded.source_format,
                        "content_sha256": excluded.content_sha256,
                        "content_encoding": excluded.content_encoding,
                        "content_bytes": excluded.content_bytes,
                        "content_size_bytes": excluded.content_size_bytes,
                        "archive_path": excluded.archive_path,
                        "archive_mtime": excluded.archive_mtime,
                        "parser_version": excluded.parser_version,
                        "parse_status": excluded.parse_status,
                        "parse_error": excluded.parse_error,
                        "extracted_at": excluded.extracted_at,
                        "updated_at": now,
                    },
                )
                await db.execute(stmt)
        await db.flush()

    async def _attach_radar_ids_to_metadata_documents(
        self,
        db: AsyncSession,
        profile_inputs: Sequence[Tuple[Dict[str, Any], int, int]],
    ) -> None:
        for row, asset_id, radar_id in profile_inputs:
            await db.execute(
                update(SourceMetadataDocumentORM)
                .where(SourceMetadataDocumentORM.source_asset_id == int(asset_id))
                .values(
                    radar_data_id=int(radar_id),
                    satellite_family=row.get("satellite_family"),
                    source_format=row.get("source_format"),
                    updated_at=_utcnow(),
                )
            )

    async def _upsert_geometry_profiles(
        self,
        db: AsyncSession,
        profile_inputs: Sequence[Tuple[Dict[str, Any], int, int]],
    ) -> None:
        now = _utcnow()
        for row, asset_id, radar_id in profile_inputs:
            metadata = dict(row.get("metadata_json") or {})
            footprint = _ordered_closed_polygon(metadata.get("coverage_polygon") or [])
            footprint_geom = None
            if footprint and len(footprint) >= 4:
                try:
                    poly = Polygon(footprint)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    if not poly.is_empty:
                        footprint_geom = from_shape(poly, srid=4326)
                except Exception:
                    footprint_geom = None

            reasons: List[str] = []
            for key, reason in (
                ("satellite_family", "missing_satellite_family"),
                ("imaging_mode", "missing_imaging_mode"),
                ("polarization", "missing_polarization"),
                ("orbit_direction", "missing_orbit_direction"),
            ):
                if not row.get(key):
                    reasons.append(reason)
            if not footprint:
                reasons.append("missing_footprint")
            family = normalize_satellite_family(row.get("satellite_family") or row.get("satellite"))
            relative_orbit = row.get("relative_orbit")
            if family == "S1" and not relative_orbit:
                reasons.append("missing_relative_orbit")

            metadata_quality = "READY" if not reasons else ("PARTIAL" if footprint else "INCOMPLETE")
            production_readiness = "READY" if metadata_quality == "READY" else "CANDIDATE"
            values = {
                "source_asset_id": int(asset_id),
                "radar_data_id": int(radar_id),
                "satellite_family": family,
                "satellite": row.get("satellite"),
                "source_format": row.get("source_format"),
                "imaging_mode": row.get("imaging_mode"),
                "polarization": row.get("polarization"),
                "orbit_direction": row.get("orbit_direction"),
                "look_direction": metadata.get("look_direction"),
                "absolute_orbit": row.get("absolute_orbit"),
                "relative_orbit": relative_orbit,
                "acquisition_start_time_utc": row.get("acquisition_start_time_utc"),
                "acquisition_stop_time_utc": row.get("acquisition_stop_time_utc"),
                "scene_center_lon": metadata.get("scene_center_lon"),
                "scene_center_lat": metadata.get("scene_center_lat"),
                "footprint_geom": footprint_geom,
                "footprint_polygon": _json_safe(footprint),
                "swath_summary_json": metadata.get("swath_summary"),
                "burst_summary_json": metadata.get("burst_summary"),
                "incidence_angle_min": metadata.get("incidence_angle_min"),
                "incidence_angle_max": metadata.get("incidence_angle_max"),
                "doppler_summary_json": metadata.get("doppler_summary"),
                "state_vector_summary_json": metadata.get("state_vector_summary"),
                "metadata_quality": metadata_quality,
                "production_readiness": production_readiness,
                "readiness_reasons_json": reasons,
                "parser_version": PARSER_VERSION,
                "parsed_at": now,
                "updated_at": now,
            }
            stmt = pg_insert(SARSceneGeometryProfileORM).values(values)
            excluded = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_asset_id"],
                set_={
                    "radar_data_id": excluded.radar_data_id,
                    "satellite_family": excluded.satellite_family,
                    "satellite": excluded.satellite,
                    "source_format": excluded.source_format,
                    "imaging_mode": excluded.imaging_mode,
                    "polarization": excluded.polarization,
                    "orbit_direction": excluded.orbit_direction,
                    "look_direction": excluded.look_direction,
                    "absolute_orbit": excluded.absolute_orbit,
                    "relative_orbit": excluded.relative_orbit,
                    "acquisition_start_time_utc": excluded.acquisition_start_time_utc,
                    "acquisition_stop_time_utc": excluded.acquisition_stop_time_utc,
                    "scene_center_lon": excluded.scene_center_lon,
                    "scene_center_lat": excluded.scene_center_lat,
                    "footprint_geom": excluded.footprint_geom,
                    "footprint_polygon": excluded.footprint_polygon,
                    "swath_summary_json": excluded.swath_summary_json,
                    "burst_summary_json": excluded.burst_summary_json,
                    "incidence_angle_min": excluded.incidence_angle_min,
                    "incidence_angle_max": excluded.incidence_angle_max,
                    "doppler_summary_json": excluded.doppler_summary_json,
                    "state_vector_summary_json": excluded.state_vector_summary_json,
                    "metadata_quality": excluded.metadata_quality,
                    "production_readiness": excluded.production_readiness,
                    "readiness_reasons_json": excluded.readiness_reasons_json,
                    "parser_version": excluded.parser_version,
                    "parsed_at": excluded.parsed_at,
                    "updated_at": now,
                },
            )
            await db.execute(stmt)
        await db.flush()

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
        file_path_match = RadarDataORM.file_path == row.get("file_path")
        unique_id_match = RadarDataORM.unique_id == f"asset:{row.get('asset_uid')}"
        clauses = [
            file_path_match,
            unique_id_match,
        ]
        priority = [
            (file_path_match, 0),
            (unique_id_match, 1),
        ]
        if archive_asset_id is not None:
            archive_match = RadarDataORM.source_archive_asset_id == int(archive_asset_id)
            clauses.append(archive_match)
            priority.append((archive_match, 2))
        if row.get("source_format") == "S1_SAFE_DIR" and logical_uid:
            logical_match = and_(
                RadarDataORM.satellite_family == "S1",
                RadarDataORM.product_unique_id == logical_uid,
            )
            clauses.append(logical_match)
            priority.append((logical_match, 3))
        elif row.get("source_format") == "S1_ZIP" and logical_uid:
            logical_match = and_(
                RadarDataORM.satellite_family == "S1",
                RadarDataORM.product_unique_id == logical_uid,
            )
            clauses.append(logical_match)
            priority.append((logical_match, 3))
        elif row.get("source_format") == "LT1_ARCHIVE" and logical_uid:
            logical_match = and_(
                RadarDataORM.satellite_family == "LT1",
                RadarDataORM.product_unique_id == logical_uid,
            )
            clauses.append(logical_match)
            priority.append((logical_match, 3))
        return (
            select(RadarDataORM)
            .where(or_(*clauses))
            .order_by(case(*priority, else_=9), RadarDataORM.id.asc())
            .limit(1)
        )

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
        root_rows = (
            await db.execute(
                select(ManagedRootORM)
                .where(ManagedRootORM.enabled == True)  # noqa: E712
                .where(ManagedRootORM.root_role.in_(["source_product_pool", "orbit_asset_pool"]))
                .order_by(ManagedRootORM.root_role.asc(), ManagedRootORM.path.asc())
            )
        ).scalars().all()
        state_rows = (
            await db.execute(
                select(AssetInventoryStateORM, ManagedRootORM)
                .join(ManagedRootORM, AssetInventoryStateORM.root_ref_id == ManagedRootORM.id)
                .where(ManagedRootORM.enabled == True)  # noqa: E712
                .where(ManagedRootORM.root_role.in_(["source_product_pool", "orbit_asset_pool"]))
                .order_by(AssetInventoryStateORM.inventory_type.asc(), AssetInventoryStateORM.root_path.asc())
            )
        ).all()
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
        integrity_rows = (
            await db.execute(
                select(SourceProductAssetORM.archive_integrity_status, func.count(SourceProductAssetORM.id))
                .where(
                    SourceProductAssetORM.is_active == True,  # noqa: E712
                    SourceProductAssetORM.source_format.in_(["LT1_ARCHIVE", "S1_ZIP"]),
                )
                .group_by(SourceProductAssetORM.archive_integrity_status)
            )
        ).all()
        archive_integrity_counts = {
            str(status or "NOT_CHECKED").upper(): int(count or 0)
            for status, count in integrity_rows
        }

        return {
            "source_asset_count": source_count,
            "orbit_asset_count": orbit_count,
            "selected_binding_count": binding_count,
            "open_issue_count": open_issue_count,
            "archive_integrity_counts": archive_integrity_counts,
            "roots": [
                {
                    "id": row.id,
                    "root_code": row.root_code,
                    "root_role": row.root_role,
                    "display_name": row.display_name,
                    "root_path": row.path,
                    "path_kind": row.path_kind,
                    "source_ref": row.source_ref,
                    "scan_mode": row.scan_mode,
                    "enabled": bool(row.enabled),
                    "exists_flag": bool(row.exists_flag),
                    "supported_families": _root_supported_families(row),
                }
                for row in root_rows
            ],
            "states": [
                {
                    "id": row.id,
                    "root_ref_id": row.root_ref_id,
                    "root_role": root.root_role,
                    "root_code": root.root_code,
                    "display_name": root.display_name,
                    "source_ref": root.source_ref,
                    "enabled": bool(root.enabled),
                    "exists_flag": bool(root.exists_flag),
                    "supported_families": _root_supported_families(root),
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
                for row, root in state_rows
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
        family_filter = self._normalize_family_filter(satellite_family)
        if family_filter:
            filters.append(SourceProductAssetORM.satellite_family.in_(family_filter))
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
        family_filter = self._normalize_family_filter(satellite_family)
        if family_filter:
            filters.append(OrbitAssetORM.satellite_family.in_(family_filter))
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
            "archive_integrity_status": row.archive_integrity_status,
            "archive_integrity_method": row.archive_integrity_method,
            "archive_integrity_checked_at": row.archive_integrity_checked_at,
            "archive_integrity_error": row.archive_integrity_error,
            "archive_integrity_version": row.archive_integrity_version,
            "archive_integrity_member_count": row.archive_integrity_member_count,
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
