"""
数据导入服务

提供数据扫描和导入功能：
- 扫描雷达数据目录
- 扫描 D-InSAR 结果目录
- 扫描灾害点数据

优化策略：
- 使用 geopandas/pyogrio 读取 Shapefile
- 利用 PostGIS 空间索引
- 支持增量扫描
"""
import os
import json
import asyncio
import hashlib
import re
import tarfile
import zipfile
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime
from shapely.geometry import Point, Polygon, shape, mapping
from shapely.ops import unary_union
import geopandas as gpd

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, text, update, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from geoalchemy2.shape import from_shape
from geoalchemy2.functions import ST_Intersects, ST_Intersection, ST_Area

from ..config import settings
from ..models import (
    RadarDataORM, RadarData, DinsarResultORM, HazardPointORM, HazardPoint, ScanStateORM
)
from ..utils import (
    get_parser,
    RADAR_PARSERS,
    find_xml_file,
    normalize_satellite_family,
    parse_xml_metadata,
)
from .task_service import task_service
from .image_service import image_service
from .orbit_converter import get_source_orbit_inventory, sync_orbit_pools
from .pairing_state_service import pairing_state_service


def _safe_mtime(path: str) -> float:
    try:
        return max(os.path.getmtime(path), os.path.getctime(path))
    except OSError:
        return 0.0


_DATE_RE = re.compile(r"^\d{8}$")


def _valid_imaging_date(value: Optional[str]) -> bool:
    return bool(value and _DATE_RE.match(value))


def _extract_date_from_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8:
        return digits[:8]
    return None


def _text_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _bool_or_none(value: Any) -> Optional[bool]:
    if value is None or isinstance(value, bool):
        return value
    text_value = str(value).strip().lower()
    if text_value in {"1", "true", "yes", "y"}:
        return True
    if text_value in {"0", "false", "no", "n"}:
        return False
    return None


def _build_insar_source_readiness(
    meta: Dict[str, Any],
    coverage_polygon: Optional[List[Tuple[float, float]]],
) -> Tuple[bool, Optional[str]]:
    reasons: List[str] = []
    if not coverage_polygon or len(coverage_polygon) < 3:
        reasons.append("missing_footprint")
    if not _valid_imaging_date(_text_or_none(meta.get("imaging_date"))):
        reasons.append("missing_date")
    for field_name, reason in (
        ("orbit_direction", "missing_orbit_direction"),
        ("imaging_mode", "missing_imaging_mode"),
        ("polarization", "missing_polarization"),
        ("satellite_family", "missing_satellite_family"),
    ):
        if not _text_or_none(meta.get(field_name)):
            reasons.append(reason)

    geocoded_flag = _bool_or_none(meta.get("geocoded_flag"))
    if geocoded_flag is True:
        reasons.append("geocoded_product")

    complex_tokens = {
        _text_or_none(meta.get("image_data_type")),
        _text_or_none(meta.get("product_type")),
        _text_or_none(meta.get("source_product_token")),
        _text_or_none(meta.get("product_variant")),
    }
    normalized_tokens = {str(token).strip().upper() for token in complex_tokens if token}
    is_complex_source = bool(normalized_tokens.intersection({"COMPLEX", "SLC", "SSC"}))
    if not is_complex_source:
        reasons.append("not_complex_source")

    if reasons:
        return False, ";".join(reasons)
    return True, None


def _iter_dirs(root: str, last_mtime: float):
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            try:
                                mtime = entry.stat().st_mtime
                            except OSError:
                                mtime = 0.0
                            if last_mtime and mtime <= last_mtime:
                                continue
                            stack.append(entry.path)
                            yield entry
                    except OSError:
                        continue
        except OSError:
            continue


def _iter_files(root: str, suffix: str, last_mtime: float):
    stack = [root]
    suffix = suffix.lower()
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            try:
                                mtime = entry.stat().st_mtime
                            except OSError:
                                mtime = 0.0
                            if last_mtime and mtime <= last_mtime:
                                continue
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if entry.name.lower().endswith(suffix):
                                yield entry
                    except OSError:
                        continue
        except OSError:
            continue


_DINSAR_ISCE2_DISP_RE = re.compile(r"^.+_disp\.(?:tif|tiff)$", re.IGNORECASE)
_DINSAR_ISCE2_SKIP_RE = re.compile(r"^.+_disp_full\.(?:tif|tiff)$", re.IGNORECASE)


def _iter_dinsar_result_candidates(root: str):
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue

                        lower_name = entry.name.lower()
                        if lower_name.endswith(".hdr"):
                            base_name, _ = os.path.splitext(entry.name)
                            if not base_name.lower().endswith("_disp"):
                                continue
                            data_file_path = os.path.join(os.path.dirname(entry.path), base_name)
                            if not os.path.exists(data_file_path):
                                continue
                            source_type = "envi"
                            record_name = base_name
                        elif lower_name.endswith((".tif", ".tiff")):
                            if _DINSAR_ISCE2_SKIP_RE.match(entry.name):
                                continue
                            if not _DINSAR_ISCE2_DISP_RE.match(entry.name):
                                continue
                            data_file_path = entry.path
                            record_name, _ = os.path.splitext(entry.name)
                            source_type = "isce2"
                        else:
                            continue

                        try:
                            stat = entry.stat()
                            scan_ts = max(stat.st_mtime, stat.st_ctime)
                        except OSError:
                            scan_ts = 0.0

                        yield {
                            "name": record_name,
                            "file_path": data_file_path,
                            "scan_path": entry.path,
                            "scan_ts": scan_ts,
                            "source_type": source_type,
                        }
                    except OSError:
                        continue
        except OSError:
            continue


def _chunked(items: List[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


_RADAR_PREVIEW_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
_RADAR_PREVIEW_KEYWORDS = ("quicklook", "quick-look", "preview", "browse", "thumbnail", "thumb", "overview")
_RADAR_CACHE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_RADAR_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tgz", ".tar.gz")
_RADAR_ARCHIVE_PREVIEW_MAX_BYTES = 256 * 1024 * 1024


def _sanitize_cache_name(name: str) -> str:
    cleaned = _RADAR_CACHE_NAME_RE.sub("_", name or "")
    cleaned = cleaned.strip("._")
    return cleaned[:48] or "scene"


def _build_cache_digest(unique_id: str, file_path: str) -> str:
    return hashlib.sha1((unique_id or file_path).encode("utf-8", errors="ignore")).hexdigest()[:16]


def _build_radar_cache_filename(unique_id: str, file_path: str) -> str:
    digest = _build_cache_digest(unique_id, file_path)
    base = _sanitize_cache_name(os.path.basename(file_path))
    return f"RID_{digest}_{base}.webp"


def _build_radar_geo_cache_filename(unique_id: str, file_path: str, cache_version: str) -> str:
    digest = _build_cache_digest(unique_id, file_path)
    base = _sanitize_cache_name(os.path.basename(file_path))
    safe_version = _sanitize_cache_name(cache_version or "v")
    return f"RGID_{safe_version}_{digest}_{base}.webp"


def _has_radar_archive_suffix(path: str) -> bool:
    return str(path or "").lower().endswith(_RADAR_ARCHIVE_SUFFIXES)


def _archive_product_stem(path: str) -> str:
    base = os.path.basename(str(path or ""))
    lower_base = base.lower()
    for suffix in (".tar.gz", ".tgz", ".tar", ".zip"):
        if lower_base.endswith(suffix):
            return base[: -len(suffix)]
    return os.path.splitext(base)[0]


def _radar_archive_expected_preview_rank(archive_path: str, member_name: str) -> Optional[int]:
    product_stem = _archive_product_stem(archive_path)
    if not product_stem:
        return None

    member = str(member_name or "").replace("\\", "/").strip("/")
    while member.startswith("./"):
        member = member[2:].strip("/")
    member_lower = member.lower()
    product_lower = product_stem.lower()
    expected_names = [
        f"{product_lower}/{product_lower}.browse.jpg",
        f"{product_lower}/{product_lower}.browse.jpeg",
        f"{product_lower}/{product_lower}.browse.png",
        f"{product_lower}/{product_lower}.quicklook.jpg",
        f"{product_lower}/{product_lower}.quicklook.png",
        f"{product_lower}/{product_lower}.quick-look.png",
        f"{product_lower}/{product_lower}.thumb.jpg",
        f"{product_lower}/{product_lower}.thumb.jpeg",
        f"{product_lower}/{product_lower}.thumb.png",
        f"{product_lower}/preview/quick-look.png",
        f"{product_lower}.browse.jpg",
        f"{product_lower}.browse.jpeg",
        f"{product_lower}.browse.png",
        f"{product_lower}.thumb.jpg",
        f"{product_lower}.thumb.jpeg",
        f"{product_lower}.thumb.png",
    ]
    try:
        return expected_names.index(member_lower)
    except ValueError:
        return None


def _radar_archive_preview_score(member_name: str, size_bytes: int = 0) -> Optional[Tuple[int, int, int, int, str]]:
    normalized_name = str(member_name or "").replace("\\", "/").strip("/")
    while normalized_name.startswith("./"):
        normalized_name = normalized_name[2:].strip("/")
    lower_name = normalized_name.lower()
    base_name = os.path.basename(lower_name)
    if not base_name.endswith(_RADAR_PREVIEW_EXTENSIONS):
        return None
    if size_bytes and size_bytes > _RADAR_ARCHIVE_PREVIEW_MAX_BYTES:
        return None

    has_keyword = any(key in lower_name for key in _RADAR_PREVIEW_KEYWORDS)
    if base_name.endswith((".tif", ".tiff")) and not has_keyword:
        return None

    keyword_score = 0 if has_keyword else 2
    if base_name == "quick-look.png":
        keyword_score = -4
    elif base_name == "quicklook.png":
        keyword_score = -3
    elif base_name.startswith("quick-look.") or base_name.startswith("quicklook."):
        keyword_score = min(keyword_score, -2)
    if "/preview/" in lower_name:
        keyword_score -= 1
    if "/icons/" in lower_name:
        keyword_score += 2
    if base_name.endswith((".jpg", ".jpeg", ".png", ".webp")):
        ext_score = 0
    elif base_name.endswith(".bmp"):
        ext_score = 1
    else:
        ext_score = 2
    depth = lower_name.count("/")
    size_score = -int(size_bytes or 0)
    return (keyword_score, depth, ext_score, size_score, member_name)


def _radar_archive_preview_cache_path(archive_path: str, member_name: str) -> str:
    digest = hashlib.sha1(
        f"{archive_path}|{member_name}".encode("utf-8", errors="ignore")
    ).hexdigest()[:16]
    raw_base = os.path.basename(str(member_name or "preview"))
    base = _sanitize_cache_name(raw_base)
    _, ext = os.path.splitext(raw_base)
    ext = _RADAR_CACHE_NAME_RE.sub("", ext.lower())[:12]
    if ext and not base.lower().endswith(ext):
        base = f"{base[:max(1, 48 - len(ext))]}{ext}"
    return os.path.join(settings.CACHE_DIR, "radar_archive_preview_sources", f"APS_{digest}_{base}")


def _write_archive_preview_cache(target_path: str, source_obj: Any, archive_mtime: float) -> Optional[str]:
    try:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        if os.path.exists(target_path) and _safe_mtime(target_path) >= archive_mtime:
            return target_path
        tmp_path = f"{target_path}.tmp"
        with open(tmp_path, "wb") as target:
            while True:
                chunk = source_obj.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
        os.replace(tmp_path, target_path)
        if archive_mtime:
            os.utime(target_path, (archive_mtime, archive_mtime))
        return target_path
    except Exception:
        try:
            if "tmp_path" in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return None


def _find_radar_archive_preview_source(archive_path: str) -> Optional[str]:
    if not archive_path or not os.path.isfile(archive_path) or not _has_radar_archive_suffix(archive_path):
        return None

    archive_mtime = _safe_mtime(archive_path)
    lower_path = archive_path.lower()
    try:
        if lower_path.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                candidates = []
                info_by_name = {}
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    score = _radar_archive_preview_score(info.filename, int(info.file_size or 0))
                    if score is None:
                        continue
                    candidates.append(score)
                    info_by_name[info.filename] = info
                if not candidates:
                    return None
                candidates.sort()
                member_name = candidates[0][-1]
                target_path = _radar_archive_preview_cache_path(archive_path, member_name)
                with archive.open(info_by_name[member_name], "r") as source_obj:
                    return _write_archive_preview_cache(target_path, source_obj, archive_mtime)

        with tarfile.open(archive_path, "r:*") as archive:
            candidates = []
            member_by_name = {}
            best_rank: Optional[int] = None
            best_member: Optional[tarfile.TarInfo] = None
            for member in archive:
                if not member.isfile():
                    continue
                expected_rank = _radar_archive_expected_preview_rank(archive_path, member.name)
                if expected_rank is not None:
                    if best_rank is None or expected_rank < best_rank:
                        best_rank = expected_rank
                        best_member = member
                    if expected_rank == 0:
                        source_obj = archive.extractfile(member)
                        if source_obj is None:
                            return None
                        target_path = _radar_archive_preview_cache_path(archive_path, member.name)
                        with source_obj:
                            return _write_archive_preview_cache(target_path, source_obj, archive_mtime)
                    continue

                score = _radar_archive_preview_score(member.name, int(member.size or 0))
                if score is None:
                    if best_member is not None and int(member.size or 0) > _RADAR_ARCHIVE_PREVIEW_MAX_BYTES:
                        break
                    continue
                candidates.append(score)
                member_by_name[member.name] = member
                if os.path.basename(str(member.name or "").lower()) in {"quick-look.png", "quicklook.png"}:
                    source_obj = archive.extractfile(member)
                    if source_obj is None:
                        return None
                    target_path = _radar_archive_preview_cache_path(archive_path, member.name)
                    with source_obj:
                        return _write_archive_preview_cache(target_path, source_obj, archive_mtime)
            if best_member is not None:
                source_obj = archive.extractfile(best_member)
                if source_obj is None:
                    return None
                target_path = _radar_archive_preview_cache_path(archive_path, best_member.name)
                with source_obj:
                    return _write_archive_preview_cache(target_path, source_obj, archive_mtime)
            if not candidates:
                return None
            candidates.sort()
            member_name = candidates[0][-1]
            source_obj = archive.extractfile(member_by_name[member_name])
            if source_obj is None:
                return None
            target_path = _radar_archive_preview_cache_path(archive_path, member_name)
            with source_obj:
                return _write_archive_preview_cache(target_path, source_obj, archive_mtime)
    except Exception:
        return None
    return None


def extract_geotiff_bounds(tiff_path: str) -> Optional[List[Tuple[float, float]]]:
    """Extract coverage polygon from a GeoTIFF file using GDAL.

    Returns a list of (lon, lat) tuples forming a closed polygon (5 points),
    or None if the file cannot be read or has no valid geotransform.
    """
    try:
        from osgeo import gdal, osr
        ds = gdal.Open(tiff_path, gdal.GA_ReadOnly)
        if ds is None:
            return None
        gt = ds.GetGeoTransform()
        if gt is None or gt == (0, 1, 0, 0, 0, 1):
            ds = None
            return None
        w, h = ds.RasterXSize, ds.RasterYSize

        # Compute 4 corners in the raster's CRS
        corners_xy = [
            (gt[0], gt[3]),                                  # top-left
            (gt[0] + w * gt[1], gt[3] + w * gt[4]),         # top-right
            (gt[0] + w * gt[1] + h * gt[2], gt[3] + w * gt[4] + h * gt[5]),  # bottom-right
            (gt[0] + h * gt[2], gt[3] + h * gt[5]),         # bottom-left
        ]

        # Transform to WGS84 if necessary
        srs = osr.SpatialReference()
        srs.ImportFromWkt(ds.GetProjection())
        ds = None

        wgs84 = osr.SpatialReference()
        wgs84.ImportFromEPSG(4326)

        if srs.IsSame(wgs84):
            polygon = [(x, y) for x, y in corners_xy]
        else:
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            wgs84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            transform = osr.CoordinateTransformation(srs, wgs84)
            polygon = []
            for x, y in corners_xy:
                lon, lat, _ = transform.TransformPoint(x, y)
                polygon.append((lon, lat))

        polygon.append(polygon[0])  # close the ring
        return polygon
    except Exception:
        return None


class DataService:
    @staticmethod
    async def _get_scan_state(db: AsyncSession, data_type: str, root_path: str):
        result = await db.execute(
            select(ScanStateORM).where(
                ScanStateORM.data_type == data_type,
                ScanStateORM.root_path == root_path
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _upsert_scan_state(db: AsyncSession, data_type: str, root_path: str, last_scan_mtime: float):
        stmt = pg_insert(ScanStateORM).values({
            "data_type": data_type,
            "root_path": root_path,
            "last_scan_mtime": last_scan_mtime,
        })
        stmt = stmt.on_conflict_do_update(
            index_elements=['data_type', 'root_path'],
            set_={
                "last_scan_mtime": last_scan_mtime,
                "last_scan_at": datetime.now(),
            }
        )
        await db.execute(stmt)

    @staticmethod
    def get_radar_raw_cache_path(unique_id: str, file_path: str) -> str:
        filename = _build_radar_cache_filename(unique_id, file_path)
        return os.path.join(settings.RADAR_RAW_CACHE_DIR, filename)

    @staticmethod
    def get_radar_geo_cache_path(unique_id: str, file_path: str) -> str:
        filename = _build_radar_geo_cache_filename(
            unique_id=unique_id,
            file_path=file_path,
            cache_version=settings.RADAR_GEO_CACHE_VERSION,
        )
        return os.path.join(settings.RADAR_GEO_CACHE_DIR, filename)

    @staticmethod
    def get_radar_cache_path(unique_id: str, file_path: str) -> str:
        return DataService.get_radar_raw_cache_path(unique_id, file_path)

    @staticmethod
    def get_dinsar_cache_filename(record_id: int, record_name: str) -> str:
        safe_name = _sanitize_cache_name(record_name)
        return f"ID_{record_id}_{safe_name}.webp"

    @staticmethod
    def get_dinsar_cache_path(record_id: int, record_name: str) -> str:
        filename = DataService.get_dinsar_cache_filename(record_id, record_name)
        cache_dir = os.path.realpath(settings.DINSAR_CACHE_DIR)
        target = os.path.realpath(os.path.join(cache_dir, filename))
        if not target.startswith(cache_dir + os.sep) and target != cache_dir:
            raise ValueError(f"Path traversal detected: {record_name!r}")
        return target

    @staticmethod
    def get_dinsar_manifest_path() -> str:
        return os.path.join(settings.DINSAR_CACHE_DIR, "cache_manifest.json")

    @staticmethod
    def find_radar_preview_source(scene_dir: str) -> Optional[str]:
        if scene_dir and os.path.isfile(scene_dir) and _has_radar_archive_suffix(scene_dir):
            return _find_radar_archive_preview_source(scene_dir)
        if not scene_dir or not os.path.isdir(scene_dir):
            return None

        candidates: List[Tuple[int, int, int, int, str]] = []
        for root, _, files in os.walk(scene_dir):
            rel = os.path.relpath(root, scene_dir)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            if depth > 5:
                continue

            for name in files:
                lower_name = name.lower()
                if not lower_name.endswith(_RADAR_PREVIEW_EXTENSIONS):
                    continue

                path = os.path.join(root, name)
                root_lower = root.lower()
                keyword_score = 0 if any(key in lower_name for key in _RADAR_PREVIEW_KEYWORDS) else 1
                if lower_name == "quick-look.png":
                    keyword_score = -3
                elif lower_name == "quicklook.png":
                    keyword_score = -2
                elif lower_name.startswith("quick-look.") or lower_name.startswith("quicklook."):
                    keyword_score = min(keyword_score, -1)
                if f"{os.sep}preview" in root_lower:
                    keyword_score -= 1
                if f"{os.sep}icons" in root_lower:
                    keyword_score += 2
                ext_score = 0 if lower_name.endswith((".jpg", ".jpeg")) else 1
                try:
                    size_score = -os.path.getsize(path)
                except OSError:
                    size_score = 0
                candidates.append((keyword_score, depth, ext_score, size_score, path))

        if not candidates:
            return None

        candidates.sort()
        return candidates[0][-1]

    @staticmethod
    def get_radar_source_corner_mapping(scene_dir: str) -> Optional[Dict[str, Any]]:
        if not scene_dir or not os.path.isdir(scene_dir):
            return None
        xml_file_path = find_xml_file(scene_dir)
        if not xml_file_path:
            return None
        _, xml_meta = parse_xml_metadata(xml_file_path)
        if not xml_meta:
            return None
        corner_mapping = xml_meta.get("corner_pixel_mapping")
        if isinstance(corner_mapping, dict):
            return corner_mapping
        return None

    @staticmethod
    def get_radar_record_corner_mapping(record: Any) -> Optional[Dict[str, Any]]:
        record_state = getattr(record, "__dict__", {}) if record is not None else {}
        for metadata in (
            getattr(record, "metadata_json", None),
            getattr(record_state.get("source_product_asset"), "metadata_json", None),
            getattr(record_state.get("source_archive_asset"), "metadata_json", None),
        ):
            if not isinstance(metadata, dict):
                continue
            corner_mapping = metadata.get("corner_pixel_mapping")
            if isinstance(corner_mapping, dict):
                return corner_mapping
        return DataService.get_radar_source_corner_mapping(str(getattr(record, "file_path", "") or ""))

    @staticmethod
    def _normalize_coverage_polygon(coverage_polygon: Any) -> Optional[List[Tuple[float, float]]]:
        if isinstance(coverage_polygon, list):
            points: List[Tuple[float, float]] = []
            for point in coverage_polygon:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    points.append((float(point[0]), float(point[1])))
            return points or None
        if isinstance(coverage_polygon, dict):
            coordinates = coverage_polygon.get("coordinates")
            if isinstance(coordinates, list) and coordinates:
                ring = coordinates[0]
                if isinstance(ring, list):
                    points = []
                    for point in ring:
                        if isinstance(point, (list, tuple)) and len(point) >= 2:
                            points.append((float(point[0]), float(point[1])))
                    return points or None
        return None


    """
    数据导入服务
    
    负责扫描和导入各类数据到数据库。
    """
    
    @staticmethod
    async def scan_radar_data(
        db: AsyncSession,
        radar_dirs: List[str],
        orbit_dir: Optional[str] = None,
        task_id: Optional[str] = None,
        progress_base: int = 0,
        progress_span: int = 100
    ) -> Dict[str, Any]:
        """
        异步扫描雷达和轨道数据目录，解析文件，并将元数据存入数据库。
        
        Args:
            db: 数据库会话
            radar_dirs: 雷达数据目录列表
            orbit_dir: 精轨数据目录
            task_id: 任务 ID（用于进度更新）
            
        Returns:
            扫描结果字典
        """
        def update_progress(msg: str, prog: int):
            scaled = prog
            if task_id:
                scaled = progress_base + int((prog / 100) * progress_span)
                scaled = min(progress_base + progress_span, max(progress_base, scaled))
                asyncio.create_task(task_service.update_task(task_id, message=msg, progress=scaled))
            print(f"  [扫描进度] {scaled}% - {msg}")

        os.makedirs(settings.RADAR_RAW_CACHE_DIR, exist_ok=True)
        os.makedirs(settings.RADAR_GEO_CACHE_DIR, exist_ok=True)

        update_progress("正在扫描精轨数据...", 5)
        
        # 1. Pre-process all orbit data
        orbit_files_map: Dict[Tuple[str, str], str] = {}
        orbit_inventory = {"errors": [], "duplicate_count": 0, "files": {}}
        invalid_orbit_stems = set()
        if orbit_dir:
            orbit_inventory = await asyncio.to_thread(
                get_source_orbit_inventory,
                orbit_dir,
                True,
            )
            for item in orbit_inventory.get("files", {}).values():
                orbit_files_map[(item["satellite"], item["date"])] = item["path"]
            for err in orbit_inventory.get("errors", []):
                print(f"警告: 精轨目录扫描异常: {err}")
            if orbit_inventory.get("duplicate_count", 0):
                print(f"警告: 精轨目录存在 {orbit_inventory['duplicate_count']} 个重复 stem，已按最新文件优先。")
        else:
            print("未配置精轨数据目录，跳过精轨扫描。")

        # 1b. 同步精轨到本地引擎池（ENVI / ISCE2）
        if orbit_dir and orbit_files_map:
            update_progress("正在同步精轨到本地引擎池...", 8)
            try:
                isce2_pool = settings.ORBIT_POOL_ISCE2 if settings.ISCE2_ENABLED else ""
                sync_result = await asyncio.to_thread(
                    sync_orbit_pools,
                    orbit_dir,
                    settings.ORBIT_POOL_ENVI,
                    isce2_pool,
                    settings.ORBIT_POOL_LANDSAR,
                    bool(settings.ISCE2_ENABLED),
                )
                envi_new = len(sync_result.get("envi", {}).get("copied", []))
                envi_updated = len(sync_result.get("envi", {}).get("updated", []))
                isce2_new = len(sync_result.get("isce2", {}).get("converted", []))
                isce2_updated = len(sync_result.get("isce2", {}).get("reconverted", []))
                if settings.ISCE2_ENABLED:
                    print(
                        f"  [精轨同步] ENVI/Gamma TXT 新增 {envi_new}、刷新 {envi_updated}，"
                        f"ISCE2 XML 转换 {isce2_new}、重转 {isce2_updated}"
                    )
                else:
                    print(f"  [精轨同步] ENVI/Gamma TXT 新增 {envi_new}、刷新 {envi_updated}，ISCE2 已停用")
                invalid_orbit_stems = {
                    item.get("name")
                    for item in sync_result.get("invalid_sources", [])
                    if item.get("name")
                }
                if invalid_orbit_stems:
                    print(f"  [精轨同步] 发现 {len(invalid_orbit_stems)} 个坏源精轨，已禁止入池/入库。")
                    orbit_files_map = {
                        key: value
                        for key, value in orbit_files_map.items()
                        if f"{key[0]}_GpsData_GAS_C_{key[1]}" not in invalid_orbit_stems
                    }
                sync_errors = (
                    sync_result.get("source", {}).get("errors", [])
                    + [item.get("error", "") for item in sync_result.get("envi", {}).get("errors", [])]
                    + (
                        [item.get("error", "") for item in sync_result.get("isce2", {}).get("errors", [])]
                        if settings.ISCE2_ENABLED
                        else []
                    )
                )
                if task_id and sync_errors:
                    await task_service.add_log(
                        task_id,
                        "WARN",
                        (
                            "精轨同步存在异常: "
                            f"源目录异常 {len(sync_result.get('source', {}).get('errors', []))} 项, "
                            f"ENVI 复制异常 {len(sync_result.get('envi', {}).get('errors', []))} 项, "
                            f"ISCE2 转换异常 {len(sync_result.get('isce2', {}).get('errors', [])) if settings.ISCE2_ENABLED else 0} 项"
                        ),
                    )
                    if settings.ISCE2_ENABLED:
                        for item in sync_result.get("isce2", {}).get("errors", [])[:20]:
                            await task_service.add_log(
                                task_id,
                                "WARN",
                                f"ISCE2 转换失败: {item.get('file')} -> {item.get('error')}",
                            )
                if task_id and invalid_orbit_stems:
                    await task_service.add_log(
                        task_id,
                        "WARN",
                        f"发现 {len(invalid_orbit_stems)} 个坏源精轨，已跳过入池与轨道关联。",
                    )
                    for item in sync_result.get("invalid_sources", [])[:20]:
                        await task_service.add_log(
                            task_id,
                            "WARN",
                            (
                                f"坏源精轨: {item.get('name')} -> {item.get('error')}"
                                f" (NUL={item.get('nul_byte_count', 0)}, tail_nul={item.get('tail_has_nul_bytes', False)})"
                            ),
                        )
            except Exception as _sync_exc:
                print(f"  [精轨同步] 警告：同步失败（不影响入库）：{_sync_exc}")
        elif task_id and orbit_inventory.get("errors"):
            await task_service.add_log(
                task_id,
                "WARN",
                f"精轨目录扫描异常: {orbit_inventory['errors'][0]}",
            )

        # 2. Process all radar data folders from multiple directories
        update_progress("正在扫描雷达数据...", 10)
        processed_scenes = 0
        
        # 预先计算公共根目录
        common_base = None
        if radar_dirs:
            try:
                common_base = os.path.commonpath([os.path.abspath(d) for d in radar_dirs])
            except ValueError:
                common_base = None

        total_progress = 80
        scan_state_updates = []
        radar_cache_candidates: Dict[str, Dict[str, str]] = {}
        for idx, radar_dir in enumerate(radar_dirs):
            radar_dir_abs = os.path.abspath(radar_dir)
            state = await DataService._get_scan_state(db, "radar", radar_dir_abs)
            last_mtime = state.last_scan_mtime if state else 0.0
            max_mtime = last_mtime
            if not os.path.exists(radar_dir):
                print(f"警告: 雷达数据目录不存在，已跳过: {radar_dir}")
                continue
            root_mtime = _safe_mtime(radar_dir_abs)
            if last_mtime and root_mtime <= last_mtime:
                scan_state_updates.append(("radar", radar_dir_abs, last_mtime))
                continue

            for entry in _iter_dirs(radar_dir, last_mtime):
                folder_name = entry.name
                parsed_radar = get_parser(folder_name, RADAR_PARSERS)
                if not parsed_radar:
                    continue

                radar_folder_path = entry.path
                try:
                    stat = entry.stat()
                    scan_ts = max(stat.st_mtime, stat.st_ctime)
                except OSError:
                    scan_ts = 0
                if scan_ts <= last_mtime:
                    continue
                if scan_ts > max_mtime:
                    max_mtime = scan_ts
                print(f"  -> 正在处理: {folder_name}")
                
                xml_file_path = find_xml_file(radar_folder_path)
                if not xml_file_path:
                    continue
                
                coverage_polygon, xml_meta = parse_xml_metadata(xml_file_path)

                if not coverage_polygon or len(coverage_polygon) < 3:
                    continue

                name_meta = parsed_radar or {}
                merged_meta = dict(name_meta)
                if xml_meta:
                    prefer_name_keys = {"product_unique_id"}
                    for key, value in xml_meta.items():
                        if value in (None, ""):
                            continue
                        if key in prefer_name_keys and merged_meta.get(key):
                            continue
                        merged_meta[key] = value

                satellite = _text_or_none(merged_meta.get("satellite"))
                imaging_date = _text_or_none(merged_meta.get("imaging_date"))
                imaging_mode = _text_or_none(merged_meta.get("imaging_mode"))
                polarization = _text_or_none(merged_meta.get("polarization"))
                orbit_direction = _text_or_none(merged_meta.get("orbit_direction"))
                satellite_mode = _text_or_none(merged_meta.get("satellite_mode"))
                receiving_station = _text_or_none(merged_meta.get("receiving_station"))
                orbit_circle = _text_or_none(merged_meta.get("orbit_circle"))
                scene_center_lon = merged_meta.get("scene_center_lon")
                scene_center_lat = merged_meta.get("scene_center_lat")
                acquisition_time_utc = _text_or_none(merged_meta.get("acquisition_time_utc"))
                product_type = _text_or_none(merged_meta.get("product_type"))
                source_product_token = _text_or_none(merged_meta.get("source_product_token"))
                image_data_type = _text_or_none(merged_meta.get("image_data_type"))
                image_data_format = _text_or_none(merged_meta.get("image_data_format"))
                product_variant = _text_or_none(merged_meta.get("product_variant"))
                product_level = _text_or_none(merged_meta.get("product_level"))
                product_unique_id = _text_or_none(merged_meta.get("product_unique_id"))
                satellite_family = normalize_satellite_family(
                    _text_or_none(merged_meta.get("satellite_family")) or satellite
                )
                look_direction = _text_or_none(merged_meta.get("look_direction"))
                if look_direction:
                    look_direction = look_direction.upper()
                geocoded_flag = _bool_or_none(merged_meta.get("geocoded_flag"))

                if not satellite:
                    continue
                if not imaging_date and acquisition_time_utc:
                    imaging_date = _extract_date_from_text(acquisition_time_utc)

                if not _valid_imaging_date(imaging_date):
                    print(f"警告: 影像日期格式无效，已跳过: {imaging_date} ({radar_folder_path})")
                    continue
                has_orbit_data = (satellite, imaging_date) in orbit_files_map
                orbit_file_path = orbit_files_map.get((satellite, imaging_date))
                
                envi_import_path = os.path.join(radar_folder_path, 'envi_import')
                is_envi_processed = os.path.isdir(envi_import_path) and bool(os.listdir(envi_import_path))

                lons = [p[0] for p in coverage_polygon]
                lats = [p[1] for p in coverage_polygon]
                
                # 生成唯一 ID
                if common_base:
                    try:
                        unique_id = os.path.relpath(radar_folder_path, start=common_base)
                    except ValueError:
                        unique_id = radar_folder_path
                else:
                    unique_id = radar_folder_path

                # Data to be inserted or updated
                poly = Polygon(coverage_polygon)
                if not poly.is_valid: 
                    poly = poly.buffer(0)
                if (scene_center_lon is None or scene_center_lat is None) and poly.is_valid:
                    scene_center_lon = scene_center_lon if scene_center_lon is not None else poly.centroid.x
                    scene_center_lat = scene_center_lat if scene_center_lat is not None else poly.centroid.y

                readiness_meta = {
                    "satellite_family": satellite_family,
                    "imaging_date": imaging_date,
                    "imaging_mode": imaging_mode,
                    "orbit_direction": orbit_direction,
                    "polarization": polarization,
                    "product_type": product_type,
                    "source_product_token": source_product_token,
                    "image_data_type": image_data_type,
                    "product_variant": product_variant,
                    "geocoded_flag": geocoded_flag,
                }
                insar_source_ready, insar_source_reason = _build_insar_source_readiness(
                    readiness_meta,
                    coverage_polygon,
                )

                data_to_upsert = {
                    "unique_id": unique_id,
                    "satellite": satellite,
                    "satellite_family": satellite_family,
                    "imaging_date": imaging_date,
                    "imaging_mode": imaging_mode,
                    "orbit_direction": orbit_direction,
                    "polarization": polarization,
                    "satellite_mode": satellite_mode,
                    "receiving_station": receiving_station,
                    "orbit_circle": orbit_circle,
                    "scene_center_lon": scene_center_lon,
                    "scene_center_lat": scene_center_lat,
                    "acquisition_time_utc": acquisition_time_utc,
                    "product_type": product_type,
                    "source_product_token": source_product_token,
                    "image_data_type": image_data_type,
                    "image_data_format": image_data_format,
                    "product_variant": product_variant,
                    "product_level": product_level,
                    "product_unique_id": product_unique_id,
                    "look_direction": look_direction,
                    "geocoded_flag": geocoded_flag,
                    "insar_source_ready": insar_source_ready,
                    "insar_source_reason": insar_source_reason,
                    "file_path": radar_folder_path,
                    "has_orbit_data": has_orbit_data,
                    "orbit_file_path": orbit_file_path,
                    "is_envi_processed": is_envi_processed,
                    "coverage_polygon": coverage_polygon,
                    "geom": from_shape(poly, srid=4326),
                    "min_lon": min(lons),
                    "min_lat": min(lats),
                    "max_lon": max(lons),
                    "max_lat": max(lats),
                }

                stmt = pg_insert(RadarDataORM).values(data_to_upsert)
                update_dict = {c.name: c for c in stmt.excluded if c.name != "unique_id"}
                stmt = stmt.on_conflict_do_update(
                    index_elements=['unique_id'],
                    set_=update_dict,
                )
                await db.execute(stmt)
                radar_cache_candidates[unique_id] = {
                    "unique_id": unique_id,
                    "file_path": radar_folder_path,
                }
                processed_scenes += 1

            scan_state_updates.append(("radar", radar_dir_abs, max_mtime))

        # 3. 更新缺失精轨的现有记录
        update_progress("正在关联精轨数据...", 85)
        updated_orbits = 0
        if orbit_files_map:
            stmt_select = select(RadarDataORM).where(RadarDataORM.has_orbit_data == False)
            result = await db.execute(stmt_select)
            records_to_update = result.scalars().all()
            
            for record in records_to_update:
                key = (record.satellite, record.imaging_date)
                if key in orbit_files_map:
                    record.has_orbit_data = True
                    record.orbit_file_path = orbit_files_map[key]
                    db.add(record)
                    updated_orbits += 1

        for data_type, root_path, mtime in scan_state_updates:
            await DataService._upsert_scan_state(db, data_type, root_path, mtime)

        await db.commit()

        pairing_dirty_summary: Dict[str, Any] = {}
        # Orbit availability is filtered live by pairing queries; it is not part of
        # pairing_metric_cache, so orbit-only updates should not dirty the cache.
        dirty_scene_ids = set()
        processed_unique_ids = [key for key in radar_cache_candidates.keys() if key]
        for chunk in _chunked(processed_unique_ids, 500):
            id_result = await db.execute(
                select(RadarDataORM.id).where(RadarDataORM.unique_id.in_(chunk))
            )
            dirty_scene_ids.update(int(value) for value in id_result.scalars().all() if value is not None)

        if dirty_scene_ids:
            pairing_dirty_summary = await pairing_state_service.mark_scenes_dirty(
                db,
                scene_ids=sorted(dirty_scene_ids),
                reason="radar_scan",
                commit=True,
            )
        elif processed_scenes > 0:
            pairing_dirty_summary = await pairing_state_service.mark_global_dirty(
                db,
                reason="radar_scan",
                commit=True,
            )

        cached_previews = 0
        skipped_previews = 0
        missing_preview_sources = 0
        failed_preview_cache = 0
        cached_raw_previews = 0
        skipped_raw_previews = 0
        failed_raw_preview_cache = 0

        radar_roots = [os.path.abspath(path) for path in radar_dirs if path]
        new_candidate_keys = set(radar_cache_candidates.keys())
        candidates: List[Dict[str, Any]] = []
        if radar_roots:
            existing_res = await db.execute(select(RadarDataORM))
            for record in existing_res.scalars().all():
                if not record.file_path:
                    continue

                file_path_abs = os.path.abspath(record.file_path)
                if not any(file_path_abs.startswith(root) for root in radar_roots):
                    continue

                unique_id = record.unique_id or record.file_path
                raw_cache_path = DataService.get_radar_raw_cache_path(unique_id, record.file_path)
                geo_cache_path = DataService.get_radar_geo_cache_path(unique_id, record.file_path)
                should_include = unique_id in new_candidate_keys
                if not should_include:
                    if not os.path.exists(geo_cache_path):
                        should_include = True
                    elif (record.preview_cache_version or "") != settings.RADAR_GEO_CACHE_VERSION:
                        should_include = True
                    elif (record.preview_cache_status or "NONE") != "READY":
                        should_include = True
                    elif not os.path.exists(raw_cache_path):
                        should_include = True

                if not should_include:
                    continue

                candidates.append(
                    {
                        "id": record.id,
                        "unique_id": unique_id,
                        "file_path": record.file_path,
                        "coverage_polygon": record.coverage_polygon,
                        "min_lon": record.min_lon,
                        "min_lat": record.min_lat,
                        "max_lon": record.max_lon,
                        "max_lat": record.max_lat,
                        "preview_cache_version": record.preview_cache_version,
                        "source_corner_mapping": DataService.get_radar_record_corner_mapping(record),
                    }
                )

        total_candidates = len(candidates)
        if total_candidates > 0:
            update_progress("正在生成源影像缓存...", 90)
            workers = max(1, min(8, settings.RADAR_GEO_CACHE_WORKERS))
            thumb_size = (settings.RADAR_THUMBNAIL_MAX_SIZE, settings.RADAR_THUMBNAIL_MAX_SIZE)
            semaphore = asyncio.Semaphore(workers)

            async def _cache_one(item: Dict[str, Any]) -> Dict[str, Any]:
                async with semaphore:
                    item_id = int(item["id"])
                    unique_id = str(item.get("unique_id") or "")
                    file_path = str(item.get("file_path") or "")
                    raw_cache_path = DataService.get_radar_raw_cache_path(unique_id, file_path)
                    geo_cache_path = DataService.get_radar_geo_cache_path(unique_id, file_path)

                    preview_source = await asyncio.to_thread(DataService.find_radar_preview_source, file_path)
                    geo_exists = os.path.exists(geo_cache_path)
                    raw_exists = os.path.exists(raw_cache_path)

                    if not preview_source:
                        if geo_exists:
                            return {
                                "id": item_id,
                                "geo_status": "skipped",
                                "raw_status": "skipped" if raw_exists else "missing",
                                "db_values": {
                                    "preview_cache_status": "READY",
                                    "preview_cache_version": settings.RADAR_GEO_CACHE_VERSION,
                                    "preview_cache_path": geo_cache_path,
                                    "preview_cache_error": None,
                                },
                            }
                        return {
                            "id": item_id,
                            "geo_status": "missing_source",
                            "raw_status": "skipped" if raw_exists else "missing",
                            "db_values": {
                                "preview_cache_status": "NONE",
                                "preview_cache_version": settings.RADAR_GEO_CACHE_VERSION,
                                "preview_cache_path": None,
                                "preview_cache_error": "preview_source_not_found",
                            },
                        }

                    source_mtime = _safe_mtime(preview_source)
                    raw_cache_mtime = _safe_mtime(raw_cache_path)
                    geo_cache_mtime = _safe_mtime(geo_cache_path)

                    coverage_polygon = DataService._normalize_coverage_polygon(item.get("coverage_polygon"))
                    try:
                        bbox = (
                            float(item.get("min_lon")),
                            float(item.get("min_lat")),
                            float(item.get("max_lon")),
                            float(item.get("max_lat")),
                        )
                    except (TypeError, ValueError):
                        bbox = None

                    source_corner_mapping = item.get("source_corner_mapping")

                    need_geo_rebuild = (
                        (not geo_cache_mtime)
                        or (geo_cache_mtime < source_mtime)
                        or ((item.get("preview_cache_version") or "") != settings.RADAR_GEO_CACHE_VERSION)
                    )
                    if not need_geo_rebuild and os.path.exists(geo_cache_path):
                        geo_status = "skipped"
                        geo_error: Optional[str] = None
                    else:
                        if not coverage_polygon:
                            geo_status = "failed"
                            geo_error = "invalid_coverage_polygon"
                        elif not bbox:
                            geo_status = "failed"
                            geo_error = "invalid_bbox"
                        else:
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
                            geo_status = "cached" if ok_geo else "failed"

                    need_raw_rebuild = (not raw_cache_mtime) or (raw_cache_mtime < source_mtime)
                    if not need_raw_rebuild:
                        raw_status = "skipped"
                    else:
                        ok_raw = await asyncio.to_thread(
                            image_service.create_radar_cached_image,
                            preview_source,
                            raw_cache_path,
                            thumb_size,
                        )
                        raw_status = "cached" if ok_raw else "failed"

                    if geo_status in {"cached", "skipped"} and os.path.exists(geo_cache_path):
                        db_values = {
                            "preview_cache_status": "READY",
                            "preview_cache_version": settings.RADAR_GEO_CACHE_VERSION,
                            "preview_cache_path": geo_cache_path,
                            "preview_cache_updated_at": datetime.utcnow(),
                            "preview_cache_error": None,
                        }
                    elif geo_status == "failed":
                        db_values = {
                            "preview_cache_status": "FAILED",
                            "preview_cache_version": settings.RADAR_GEO_CACHE_VERSION,
                            "preview_cache_path": None,
                            "preview_cache_updated_at": datetime.utcnow(),
                            "preview_cache_error": geo_error or "geo_cache_build_failed",
                        }
                    else:
                        db_values = {
                            "preview_cache_status": "NONE",
                            "preview_cache_version": settings.RADAR_GEO_CACHE_VERSION,
                            "preview_cache_path": None,
                            "preview_cache_updated_at": datetime.utcnow(),
                            "preview_cache_error": "preview_source_not_found",
                        }

                    return {
                        "id": item_id,
                        "geo_status": geo_status,
                        "raw_status": raw_status,
                        "db_values": db_values,
                    }

            processed = 0
            for chunk in _chunked(candidates, workers):
                results = await asyncio.gather(*[_cache_one(item) for item in chunk])
                for result_item in results:
                    processed += 1
                    geo_status = result_item.get("geo_status")
                    raw_status = result_item.get("raw_status")
                    if geo_status == "cached":
                        cached_previews += 1
                    elif geo_status == "skipped":
                        skipped_previews += 1
                    elif geo_status == "missing_source":
                        missing_preview_sources += 1
                    else:
                        failed_preview_cache += 1

                    if raw_status == "cached":
                        cached_raw_previews += 1
                    elif raw_status == "skipped":
                        skipped_raw_previews += 1
                    elif raw_status == "failed":
                        failed_raw_preview_cache += 1

                    db_values = result_item.get("db_values")
                    if db_values:
                        await db.execute(
                            update(RadarDataORM)
                            .where(RadarDataORM.id == result_item["id"])
                            .values(**db_values)
                        )

                    prog = 90 + int((processed / total_candidates) * 10)
                    update_progress(
                        f"正在生成源影像缓存 ({processed}/{total_candidates})...",
                        min(100, prog),
                    )

            await db.commit()

        return {
            "message": "数据扫描完成",
            "processed_scenes": processed_scenes,
            "total_orbit_files": len(orbit_files_map),
            "updated_orbits": updated_orbits,
            "pairing_dirty_scene_count": int(pairing_dirty_summary.get("dirty_scene_count") or 0),
            "pairing_cache_status": pairing_dirty_summary.get("status"),
            "cached_previews": cached_previews,
            "skipped_previews": skipped_previews,
            "missing_preview_sources": missing_preview_sources,
            "failed_preview_cache": failed_preview_cache,
            "cached_raw_previews": cached_raw_previews,
            "skipped_raw_previews": skipped_raw_previews,
            "failed_raw_preview_cache": failed_raw_preview_cache,
        }
    
    @staticmethod
    async def scan_dinsar_results(
        db: AsyncSession,
        results_dirs: List[str],
        task_id: Optional[str] = None,
        progress_base: int = 0,
        progress_span: int = 100
    ) -> Dict[str, Any]:
        """
        增量扫描 D-InSAR 结果目录，并为所有未缓存的结果创建图像缓存。
        
        Args:
            db: 数据库会话
            results_dirs: D-InSAR 结果目录列表
            task_id: 任务 ID（用于进度更新）
            
        Returns:
            扫描结果字典
        """
        def update_progress(msg: str, prog: int):
            scaled = prog
            if task_id:
                scaled = progress_base + int((prog / 100) * progress_span)
                scaled = min(progress_base + progress_span, max(progress_base, scaled))
                asyncio.create_task(task_service.update_task(task_id, message=msg, progress=scaled))
            print(f"  [扫描进度] {scaled}% - {msg}")
        
        os.makedirs(settings.DINSAR_CACHE_DIR, exist_ok=True)
        MANIFEST_PATH = DataService.get_dinsar_manifest_path()
        
        update_progress("正在检查数据库现有记录...", 5)

        # 1. 从数据库获取现有记录（按 file_path 去重）
        existing_rows_result = await db.execute(
            select(DinsarResultORM.id, DinsarResultORM.file_path)
        )
        existing_rows = existing_rows_result.all()

        path_map: Dict[str, int] = {}
        duplicate_ids: List[int] = []

        for record_id, file_path in existing_rows:
            if not file_path:
                continue

            existing_id = path_map.get(file_path)
            if existing_id is None:
                path_map[file_path] = record_id
                continue
            if record_id > existing_id:
                duplicate_ids.append(existing_id)
                path_map[file_path] = record_id
            else:
                duplicate_ids.append(record_id)

        if duplicate_ids:
            await db.execute(
                delete(DinsarResultORM).where(DinsarResultORM.id.in_(duplicate_ids))
            )
            await db.commit()
            print(f"检测到重复记录，已清理 {len(duplicate_ids)} 条 (按 file_path 去重)。")

        existing_paths = set(path_map.keys())
        print(f"数据库中已存在 {len(existing_paths)} 条记录。开始增量文件扫描...")

        new_files_metadata = []
        updated_existing = 0
        dinsar_state_updates = []

        # 2. 统计总量并遍历文件系统收集新文件 (全量遍历，仅新增入库)
        update_progress("正在统计 D-InSAR 结果总量...", 8)
        total_candidates = 0
        for results_dir in results_dirs:
            if not os.path.exists(results_dir):
                continue
            total_candidates += sum(1 for _ in _iter_dinsar_result_candidates(results_dir))

        if total_candidates == 0:
            update_progress("未找到任何可识别的 D-InSAR 结果。", 10)

        update_progress("正在遍历文件系统查找新结果...", 10)
        processed_candidate_total = 0
        scan_progress_base = 10
        scan_progress_span = 15
        for results_dir in results_dirs:
            results_dir_abs = os.path.abspath(results_dir)
            max_mtime = 0.0
            if not os.path.exists(results_dir):
                print(f"警告: Dinsar结果目录不存在，已跳过: {results_dir}")
                continue

            for candidate in _iter_dinsar_result_candidates(results_dir):
                processed_candidate_total += 1
                if processed_candidate_total % 50 == 0 or processed_candidate_total == total_candidates:
                    prog = scan_progress_base + int((processed_candidate_total / max(total_candidates, 1)) * scan_progress_span)
                    update_progress(
                        f"正在遍历文件系统查找新结果 ({processed_candidate_total}/{max(total_candidates, 1)})...",
                        min(scan_progress_base + scan_progress_span, prog),
                    )

                scan_ts = candidate["scan_ts"]
                if scan_ts > max_mtime:
                    max_mtime = scan_ts
                base_name = candidate["name"]
                data_file_path = candidate["file_path"]

                if not os.path.exists(data_file_path):
                    continue
                if data_file_path in existing_paths:
                    continue

                update_progress(f"正在解析新文件元数据: {base_name}", 25)

                try:
                    meta = image_service.extract_footprint(data_file_path)
                    poly = shape(meta["coverage_polygon"])
                    if not poly.is_valid:
                        poly = poly.buffer(0)

                    data_payload = {
                        "name": base_name,
                        "file_path": data_file_path,
                        "min_lon": meta["min_lon"],
                        "min_lat": meta["min_lat"],
                        "max_lon": meta["max_lon"],
                        "max_lat": meta["max_lat"],
                        "coverage_polygon": meta["coverage_polygon"],
                        "geom": from_shape(poly, srid=4326),
                        "is_cached": False,
                    }

                    new_files_metadata.append(data_payload)
                    existing_paths.add(data_file_path)
                except Exception as e:
                    print(f"读取新文件元数据失败: {data_file_path}, 错误: {e}")
            dinsar_state_updates.append(("dinsar", results_dir_abs, max_mtime))

        # 3. 批量插入新文件

        if new_files_metadata or updated_existing:
            print(f"发现 {len(new_files_metadata)} 个新文件，更新 {updated_existing} 条已存在记录。")
            try:
                if new_files_metadata:
                    stmt = pg_insert(DinsarResultORM).values(new_files_metadata)
                    stmt = stmt.on_conflict_do_nothing(index_elements=['file_path'])
                    await db.execute(stmt)
                await db.commit()
            except Exception as e:
                await db.rollback()
                print(f"数据库批量写入新文件失败: {e}")
        else:
            print("未发现新的Dinsar结果文件。")

        # 4. 检查需要处理的记录
        update_progress("正在检查数据库一致性与缓存状态...", 25)
        to_update_result = await db.execute(select(DinsarResultORM))
        all_records = to_update_result.scalars().all()
        
        # 5. 加载现有清单
        manifest = {}
        if os.path.exists(MANIFEST_PATH):
            with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
                try:
                    manifest = json.load(f)
                except json.JSONDecodeError:
                    print("警告: 缓存清单文件损坏，将重新创建。")

        records_to_process = []
        cached_count = 0
        updated_meta_count = 0

        for record in all_records:
            if not record.name:
                continue

            is_old_format = (
                isinstance(record.coverage_polygon, list) or
                (record.min_lon is None) or
                (record.min_lon > 180) or
                (record.coverage_polygon is None)
            )

            thumb_cache_filename = DataService.get_dinsar_cache_filename(record.id, record.name)
            thumb_cache_path = DataService.get_dinsar_cache_path(record.id, record.name)
            file_missing = not os.path.exists(thumb_cache_path)

            force_recache = is_old_format or file_missing
            needs_meta_update = force_recache or (record.coverage_polygon is None) or (record.min_lon is None) or (record.min_lon > 180)
            needs_cache_gen = force_recache or (not record.is_cached and file_missing)

            if not needs_meta_update and not needs_cache_gen:
                if not record.is_cached and not file_missing:
                    await db.execute(
                        update(DinsarResultORM)
                        .where(DinsarResultORM.id == record.id)
                        .values(is_cached=True)
                    )
                    manifest[thumb_cache_filename] = record.file_path
                    cached_count += 1
                continue

            records_to_process.append({
                "id": record.id,
                "name": record.name,
                "file_path": record.file_path,
                "force_recache": force_recache,
                "needs_meta_update": needs_meta_update,
                "needs_cache_gen": needs_cache_gen,
                "cache_filename": thumb_cache_filename,
                "cache_path": thumb_cache_path,
            })

        if not records_to_process and not new_files_metadata and cached_count == 0:
            for data_type, root_path, mtime in dinsar_state_updates:
                await DataService._upsert_scan_state(db, data_type, root_path, mtime)
            await db.commit()
            return {
                "message": "扫描完成，所有结果均已同步且已缓存。",
                "new_files_found": 0,
                "updated_existing": updated_existing,
                "cached_now": cached_count
            }

        print(f"找到 {len(records_to_process)} 个需要处理（缓存或补全元数据）的记录...")

        # 6. 处理所有待办记录 (CPU/IO in threads, DB updates sequential)
        total_to_process = len(records_to_process)
        processed_count = 0
        worker_cap = max(1, min(settings.DINSAR_CACHE_WORKERS, os.cpu_count() or 2))
        batch_size = max(1, worker_cap * 2)
        thumb_size = (settings.DINSAR_THUMBNAIL_MAX_SIZE, settings.DINSAR_THUMBNAIL_MAX_SIZE)

        def _cleanup_cache(cache_path: str, record_name: str):
            if os.path.exists(cache_path):
                try:
                    os.remove(cache_path)
                except OSError:
                    pass
            old_png = os.path.join(settings.DINSAR_CACHE_DIR, f"{record_name}.png")
            if os.path.exists(old_png):
                try:
                    os.remove(old_png)
                except OSError:
                    pass

        async def _process_record(info: Dict[str, Any]) -> Dict[str, Any]:
            result = dict(info)
            if info.get("needs_meta_update"):
                try:
                    meta = await asyncio.to_thread(image_service.extract_footprint, info["file_path"])
                    result["meta"] = meta
                except Exception as e:
                    result["meta_error"] = str(e)

            if info.get("needs_cache_gen"):
                if info.get("force_recache"):
                    await asyncio.to_thread(_cleanup_cache, info["cache_path"], info["name"])
                try:
                    ok = await asyncio.to_thread(
                        image_service.create_cached_image,
                        info["file_path"],
                        info["cache_path"],
                        thumb_size
                    )
                    result["cache_ok"] = bool(ok)
                except Exception as e:
                    result["cache_error"] = str(e)
            return result

        for chunk in _chunked(records_to_process, batch_size):
            results = await asyncio.gather(*[_process_record(info) for info in chunk])
            for result in results:
                processed_count += 1
                prog = 30 + int((processed_count / max(total_to_process, 1)) * 65)
                update_progress(
                    f"正在处理缓存与自愈 ({processed_count}/{total_to_process}): {result.get('name', '')}",
                    prog
                )

                updates = {}
                meta = result.get("meta")
                if meta:
                    try:
                        poly = shape(meta["coverage_polygon"])
                        if not poly.is_valid:
                            poly = poly.buffer(0)
                        updates.update({
                            "coverage_polygon": meta["coverage_polygon"],
                            "geom": from_shape(poly, srid=4326),
                            "min_lon": meta["min_lon"],
                            "min_lat": meta["min_lat"],
                            "max_lon": meta["max_lon"],
                            "max_lat": meta["max_lat"],
                        })
                        updated_meta_count += 1
                    except Exception as e:
                        print(f"补全旧记录多边形失败 (ID: {result.get('id')}): {e}")
                elif result.get("meta_error"):
                    print(f"读取元数据失败 (ID: {result.get('id')}): {result.get('meta_error')}")

                if result.get("cache_ok"):
                    updates["is_cached"] = True
                    manifest[result["cache_filename"]] = result["file_path"]
                    cached_count += 1
                elif result.get("needs_cache_gen") and result.get("cache_error"):
                    print(f"缓存生成失败 (ID: {result.get('id')}): {result.get('cache_error')}")

                if updates:
                    await db.execute(
                        update(DinsarResultORM)
                        .where(DinsarResultORM.id == result["id"])
                        .values(**updates)
                    )
        
        # 7. 提交并保存
        for data_type, root_path, mtime in dinsar_state_updates:
            await DataService._upsert_scan_state(db, data_type, root_path, mtime)

        if cached_count > 0 or updated_meta_count > 0:
            try:
                await db.commit()
                if cached_count > 0:
                    with open(MANIFEST_PATH, 'w', encoding='utf-8') as f:
                        json.dump(manifest, f, indent=4, ensure_ascii=False)
                print(f"成功更新了 {cached_count} 条缓存记录和 {updated_meta_count} 条元数据。")
            except Exception as e:
                await db.rollback()
                print(f"提交数据库或保存清单时出错: {e}")

        else:
            await db.commit()
        return {
            "message": "Dinsar结果扫描与自愈完成",
            "new_files_found": len(new_files_metadata),
            "updated_existing": updated_existing,
            "cached_now": cached_count,
            "meta_updated": updated_meta_count
        }
    
    @staticmethod
    async def scan_hazard_points(
        db: AsyncSession,
        shp_path: str
    ) -> Dict[str, Any]:
        """
        扫描地质灾害点 Shapefile 并存入数据库。
        
        Args:
            db: 数据库会话
            shp_path: Shapefile 路径
            
        Returns:
            扫描结果字典
        """
        if not os.path.exists(shp_path):
            raise FileNotFoundError(f"未找到地质灾害点文件: {shp_path}")

        try:
            gdf = gpd.read_file(shp_path, engine='pyogrio')
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)
            
            points_to_upsert = []
            def pick_value(row_obj, candidates):
                for key in candidates:
                    if key in row_obj and row_obj[key] not in (None, ""):
                        return row_obj[key]
                return None

            def to_float(value):
                try:
                    if value is None or value == "":
                        return None
                    return float(value)
                except (TypeError, ValueError):
                    return None

            for _, row in gdf.iterrows():
                tybh_value = pick_value(row, ["TYBH", "tybh"])
                if tybh_value is None:
                    tybh_value = pick_value(row, ["统一编", "统一编号", "UNIFIED_ID"])

                if tybh_value is None:
                    continue

                hazard_type = pick_value(row, ["灾害类型", "灾害类", "ZHLX", "hazard_type", "TYPE"])
                hazard_name = pick_value(row, ["灾害名", "ZHMC", "hazard_name", "NAME"])
                city = pick_value(row, ["市", "CITY", "city"])
                county = pick_value(row, ["县", "COUNTY", "county"])
                township = pick_value(row, ["乡", "TOWNSHIP", "township", "乡镇"])

                geom_x = getattr(row.geometry, "x", None)
                geom_y = getattr(row.geometry, "y", None)

                lon_value = to_float(geom_x)
                lat_value = to_float(geom_y)
                if lon_value is None:
                    lon_value = to_float(pick_value(row, ["经度", "LON", "longitude"]))
                if lat_value is None:
                    lat_value = to_float(pick_value(row, ["纬度", "维度", "LAT", "latitude"]))

                if lon_value is None or lat_value is None:
                    continue

                point_data = {
                    "tybh": str(tybh_value).strip(),
                    "hazard_type": hazard_type,
                    "hazard_name": hazard_name,
                    "city": city,
                    "county": county,
                    "township": township,
                    "longitude": lon_value,
                    "latitude": lat_value,
                }
                points_to_upsert.append(point_data)

            if points_to_upsert:
                for p in points_to_upsert:
                    p['geom'] = from_shape(Point(p['longitude'], p['latitude']), srid=4326)
                    
                    stmt = pg_insert(HazardPointORM).values(p)
                    update_dict = {c.name: c for c in stmt.excluded if c.name != "tybh"}
                    stmt = stmt.on_conflict_do_update(
                        index_elements=['tybh'],
                        set_=update_dict,
                    )
                    await db.execute(stmt)
                
                await db.commit()
                return {"message": "灾害点同步完成", "count": len(points_to_upsert)}
            
            return {"message": "未发现有效的灾害点数据", "count": 0}

        except Exception as e:
            await db.rollback()
            raise ValueError(f"处理灾害点 Shapefile 失败: {e}")


# 全局服务实例
data_service = DataService()
