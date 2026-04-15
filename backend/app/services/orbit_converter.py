"""轨道文件管理与格式转换服务。

目录约定：
  MONITOR_ORBIT_DIR/   源精轨目录（可为 UNC），平铺 .txt
  ORBIT_POOL_ENVI/     ENVI 本地精轨池
    LT1A/              LT-1A 精轨 .txt
    LT1B/              LT-1B 精轨 .txt
  ORBIT_POOL_ISCE2/    ISCE2 本地精轨池，平铺 .xml（全轨道，无裁剪）
  ORBIT_POOL_LANDSAR/  LANDSAR 本地精轨池（预留）

公开接口：
  sync_orbit_pools(source_dir, envi_pool, isce2_pool, landsar_pool)
                                          从源目录同步到各引擎本地池
  organize_orbit_dir(orbit_root)          整理根目录散落 .txt → 子目录
  get_converted(orbit_root, satellite,    按需转换并归档，返回 Windows 路径
                date_yyyymmdd, fmt, ...)
  scan_orbit_dir(orbit_root)              扫描目录状态，返回统计信息
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 文件名解析
# ---------------------------------------------------------------------------

# 匹配 LT1A_GpsData_GAS_C_YYYYMMDD.txt 或 LT1B_...
_ORBIT_FILENAME_RE = re.compile(
    r"^(LT1[AB])_GpsData_GAS_C_(\d{8})\.txt$", re.IGNORECASE
)


def parse_orbit_filename(filename: str) -> Optional[Tuple[str, str]]:
    """解析轨道文件名，返回 (satellite, date_yyyymmdd) 或 None。"""
    m = _ORBIT_FILENAME_RE.match(os.path.basename(filename))
    if not m:
        return None
    return m.group(1).upper(), m.group(2)


def _make_orbit_stem(satellite: str, date_yyyymmdd: str) -> str:
    return f"{satellite.upper()}_GpsData_GAS_C_{date_yyyymmdd}"


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return -1.0


def _safe_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def _needs_copy_refresh(source_path: str, target_path: str) -> bool:
    if not os.path.exists(target_path):
        return True

    target_size = _safe_size(target_path)
    if target_size <= 0:
        return True

    source_size = _safe_size(source_path)
    if source_size >= 0 and source_size != target_size:
        return True

    return _safe_mtime(source_path) > _safe_mtime(target_path)


def _needs_generated_refresh(source_path: str, target_path: str) -> bool:
    if not os.path.exists(target_path):
        return True
    if _safe_size(target_path) <= 0:
        return True
    return _safe_mtime(source_path) > _safe_mtime(target_path)


def _pick_preferred_path(existing_path: str, candidate_path: str) -> str:
    existing_mtime = _safe_mtime(existing_path)
    candidate_mtime = _safe_mtime(candidate_path)
    if candidate_mtime > existing_mtime:
        return candidate_path
    if candidate_mtime < existing_mtime:
        return existing_path
    return min(existing_path, candidate_path, key=lambda item: (len(item), item.lower()))


def _safe_relpath(path: str, root_dir: str) -> str:
    if not root_dir:
        return os.path.basename(path)
    try:
        rel_path = os.path.relpath(path, root_dir)
    except ValueError:
        return os.path.basename(path)
    if rel_path.startswith(".."):
        return os.path.basename(path)
    return rel_path


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 1
    while True:
        candidate = f"{base}.{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def _default_quarantine_root(source_dir: str, quarantine_root: str = "") -> str:
    if quarantine_root:
        return quarantine_root
    if source_dir:
        return os.path.join(source_dir, "_quarantine")
    return os.path.join(os.getcwd(), "_orbit_quarantine")


def _default_source_scan_skip_dir_names() -> List[str]:
    return [
        "_quarantine",
        ".orbit_stage",
        "converted",
    ]


def _pick_stage_parent(txt_path: str, scratch_root: str = "") -> str:
    candidates = [
        scratch_root,
        os.path.dirname(txt_path),
        os.getcwd(),
    ]
    for root in candidates:
        if not root:
            continue
        stage_parent = os.path.join(root, ".orbit_stage")
        try:
            os.makedirs(stage_parent, exist_ok=True)
            return stage_parent
        except OSError:
            continue
    raise PermissionError(f"无法创建轨道转换临时目录：{txt_path}")


def _inspect_orbit_txt_health(path: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "file_size": _safe_size(path),
        "contains_nul_bytes": False,
        "nul_byte_count": 0,
        "tail_has_nul_bytes": False,
        "first_nul_offset": -1,
    }
    try:
        nul_byte_count = 0
        tail = b""
        offset = 0
        first_nul_offset = -1
        with open(path, "rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                if first_nul_offset < 0:
                    chunk_nul_index = chunk.find(b"\x00")
                    if chunk_nul_index >= 0:
                        first_nul_offset = offset + chunk_nul_index
                nul_byte_count += chunk.count(b"\x00")
                tail = (tail + chunk)[-4096:]
                offset += len(chunk)
        info["nul_byte_count"] = nul_byte_count
        info["contains_nul_bytes"] = nul_byte_count > 0
        info["tail_has_nul_bytes"] = b"\x00" in tail
        info["first_nul_offset"] = first_nul_offset
    except OSError as exc:
        info["read_error"] = str(exc)
    return info


def _truncate_message(text: str, limit: int = 1200) -> str:
    value = str(text or "").replace("\x00", "[NUL]").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _compact_error_message(text: str, limit: int = 1200) -> str:
    value = str(text or "").replace("\x00", "[NUL]").strip()
    if not value:
        return ""

    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) > 1:
        for line in reversed(lines):
            if re.match(r"^[A-Za-z_][\w.]*:\s", line):
                return _truncate_message(line, limit=limit)

    return _truncate_message(value, limit=limit)


def _convert_to_isce2_xml_file(txt_path: str, xml_path: str) -> None:
    if "isce2" not in _CONVERTERS:
        raise KeyError("未注册 isce2 轨道转换器")
    _CONVERTERS["isce2"](txt_path, xml_path)
    if not os.path.isfile(xml_path) or _safe_size(xml_path) <= 0:
        raise RuntimeError(f"转换完成但 XML 输出无效：{xml_path}")


def _stage_isce2_xml(txt_path: str, stem: str, scratch_root: str = "") -> Tuple[str, str]:
    temp_dir = tempfile.mkdtemp(prefix="orbit_stage_", dir=_pick_stage_parent(txt_path, scratch_root))
    temp_xml = os.path.join(temp_dir, stem + ".xml")
    try:
        _convert_to_isce2_xml_file(txt_path, temp_xml)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return temp_dir, temp_xml


def _build_invalid_source_record(
    stem: str,
    txt_path: str,
    error: Exception | str,
    envi_path: str = "",
    isce2_path: str = "",
) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "name": stem,
        "source": txt_path,
        "error": _compact_error_message(error),
    }
    if envi_path:
        record["envi_path"] = envi_path
    if isce2_path:
        record["isce2_path"] = isce2_path
    record.update(_inspect_orbit_txt_health(txt_path))
    record["has_corruption_signal"] = _has_orbit_corruption_signal(record)
    return record


def _has_orbit_corruption_signal(item: Dict[str, Any]) -> bool:
    first_nul_offset = item.get("first_nul_offset", -1)
    return bool(
        item.get("read_error")
        or item.get("contains_nul_bytes")
        or item.get("tail_has_nul_bytes")
        or int(item.get("nul_byte_count") or 0) > 0
        or (
            isinstance(first_nul_offset, (int, float))
            and int(first_nul_offset) >= 0
        )
    )


def _build_source_gap_sample(
    stem: str,
    source_entry: Optional[Dict[str, Any]] = None,
    envi_entry: Optional[Dict[str, Any]] = None,
    isce2_entry: Optional[Dict[str, Any]] = None,
    inspect_source: bool = False,
) -> Dict[str, Any]:
    source_path = (source_entry or {}).get("path", "")
    sample: Dict[str, Any] = {
        "name": stem,
        "source": source_path,
        "source_path": source_path,
        "envi_path": (envi_entry or {}).get("path", ""),
        "isce2_path": (isce2_entry or {}).get("path", ""),
        "has_corruption_signal": False,
    }
    if inspect_source and source_path:
        sample.update(_inspect_orbit_txt_health(source_path))
        sample["has_corruption_signal"] = _has_orbit_corruption_signal(sample)
        if sample.get("read_error"):
            sample["error"] = f"Source health scan failed: {sample['read_error']}"
        elif sample["has_corruption_signal"]:
            sample["error"] = "Source TXT contains NUL bytes and appears corrupted"
    return sample


def validate_orbit_source(txt_path: str, stem: str = "", scratch_root: str = "") -> Dict[str, Any]:
    name = stem or os.path.splitext(os.path.basename(txt_path))[0]
    try:
        temp_dir, temp_xml = _stage_isce2_xml(txt_path, name, scratch_root=scratch_root)
        try:
            return {
                "name": name,
                "source": txt_path,
                "ok": True,
                "generated_xml_size": _safe_size(temp_xml),
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception as exc:
        result = _build_invalid_source_record(name, txt_path, exc)
        result["ok"] = False
        return result


def summarize_source_orbit_gaps(
    source_dir: str,
    envi_pool: str = "",
    isce2_pool: str = "",
    quarantine_root: str = "",
) -> Dict[str, Any]:
    source_inventory = get_source_orbit_inventory(source_dir, recursive=True)
    pool_inventory = get_orbit_pool_inventory(envi_pool, isce2_pool, recursive=True)

    source_files: Dict[str, Dict[str, str]] = source_inventory["files"]
    envi_files: Dict[str, Dict[str, str]] = pool_inventory["envi"]["files"]
    isce2_files: Dict[str, Dict[str, str]] = pool_inventory["isce2"]["files"]

    source_stems = set(source_files)
    envi_stems = set(envi_files)
    isce2_stems = set(isce2_files)

    source_without_isce2 = sorted(source_stems - isce2_stems)
    source_without_envi = sorted(source_stems - envi_stems)
    envi_without_source = sorted(envi_stems - source_stems)
    isce2_without_source = sorted(isce2_stems - source_stems)
    sample_limit = 20

    def _make_samples(
        stems: List[str],
        inspect_source: bool = False,
    ) -> List[Dict[str, Any]]:
        samples: List[Dict[str, Any]] = []
        for stem in stems[:sample_limit]:
            samples.append(
                _build_source_gap_sample(
                    stem,
                    source_files.get(stem),
                    envi_files.get(stem),
                    isce2_files.get(stem),
                    inspect_source=inspect_source,
                )
            )
        return samples

    suspect_bad_samples = _make_samples(source_without_isce2, inspect_source=True)
    bad_source_samples = [
        item for item in suspect_bad_samples
        if item.get("has_corruption_signal")
    ]

    return {
        "sample_limit": sample_limit,
        "quarantine_path": _default_quarantine_root(source_dir, quarantine_root),
        "source_without_isce2_count": len(source_without_isce2),
        "source_without_envi_count": len(source_without_envi),
        "envi_without_source_count": len(envi_without_source),
        "isce2_without_source_count": len(isce2_without_source),
        "suspect_bad_count": len(source_without_isce2),
        "suspect_bad_samples": suspect_bad_samples,
        "bad_source_sample_count": len(bad_source_samples),
        "bad_source_samples": bad_source_samples,
        "source_without_envi_samples": _make_samples(source_without_envi),
        "envi_without_source_samples": _make_samples(envi_without_source),
        "isce2_without_source_samples": _make_samples(isce2_without_source),
    }


def _index_orbit_txt_files(
    root_dir: str,
    recursive: bool = True,
    skip_dir_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    files: Dict[str, Dict[str, str]] = {}
    duplicates: List[Dict[str, str]] = []
    errors: List[str] = []
    normalized_skip_dir_names = {
        str(name).strip().lower()
        for name in (skip_dir_names or [])
        if str(name).strip()
    }

    if not root_dir:
        return {"files": files, "duplicates": duplicates, "errors": errors}
    if not os.path.isdir(root_dir):
        return {
            "files": files,
            "duplicates": duplicates,
            "errors": [f"目录不存在或不可访问：{root_dir}"],
        }

    if recursive:
        def _onerror(exc: OSError) -> None:
            errors.append(str(exc))

        walker = os.walk(root_dir, onerror=_onerror)
        for current_root, dir_names, names in walker:
            if normalized_skip_dir_names:
                dir_names[:] = [
                    dir_name
                    for dir_name in dir_names
                    if dir_name.lower() not in normalized_skip_dir_names
                ]
            for fname in names:
                parsed = parse_orbit_filename(fname)
                if not parsed:
                    continue
                satellite, date_yyyymmdd = parsed
                stem = _make_orbit_stem(satellite, date_yyyymmdd)
                path = os.path.join(current_root, fname)
                existing = files.get(stem)
                if existing:
                    preferred = _pick_preferred_path(existing["path"], path)
                    skipped = path if preferred == existing["path"] else existing["path"]
                    duplicates.append({"name": stem, "preferred": preferred, "skipped": skipped})
                    if preferred != existing["path"]:
                        files[stem] = {
                            "name": stem,
                            "satellite": satellite,
                            "date": date_yyyymmdd,
                            "path": preferred,
                        }
                    continue
                files[stem] = {
                    "name": stem,
                    "satellite": satellite,
                    "date": date_yyyymmdd,
                    "path": path,
                }
    else:
        try:
            names = os.listdir(root_dir)
        except OSError as exc:
            errors.append(str(exc))
            return {"files": files, "duplicates": duplicates, "errors": errors}

        for fname in names:
            parsed = parse_orbit_filename(fname)
            if not parsed:
                continue
            path = os.path.join(root_dir, fname)
            if not os.path.isfile(path):
                continue
            satellite, date_yyyymmdd = parsed
            stem = _make_orbit_stem(satellite, date_yyyymmdd)
            files[stem] = {
                "name": stem,
                "satellite": satellite,
                "date": date_yyyymmdd,
                "path": path,
            }

    return {"files": files, "duplicates": duplicates, "errors": errors}


def _index_orbit_xml_files(root_dir: str, recursive: bool = True) -> Dict[str, Any]:
    files: Dict[str, Dict[str, str]] = {}
    duplicates: List[Dict[str, str]] = []
    errors: List[str] = []

    if not root_dir:
        return {"files": files, "duplicates": duplicates, "errors": errors}
    if not os.path.isdir(root_dir):
        return {
            "files": files,
            "duplicates": duplicates,
            "errors": [f"目录不存在或不可访问：{root_dir}"],
        }

    def _register(current_root: str, fname: str) -> None:
        if not fname.lower().endswith(".xml"):
            return
        parsed = parse_orbit_filename(fname[:-4] + ".txt")
        if not parsed:
            return
        satellite, date_yyyymmdd = parsed
        stem = _make_orbit_stem(satellite, date_yyyymmdd)
        path = os.path.join(current_root, fname)
        existing = files.get(stem)
        if existing:
            preferred = _pick_preferred_path(existing["path"], path)
            skipped = path if preferred == existing["path"] else existing["path"]
            duplicates.append({"name": stem, "preferred": preferred, "skipped": skipped})
            if preferred != existing["path"]:
                files[stem] = {
                    "name": stem,
                    "satellite": satellite,
                    "date": date_yyyymmdd,
                    "path": preferred,
                }
            return
        files[stem] = {
            "name": stem,
            "satellite": satellite,
            "date": date_yyyymmdd,
            "path": path,
        }

    if recursive:
        def _onerror(exc: OSError) -> None:
            errors.append(str(exc))

        walker = os.walk(root_dir, onerror=_onerror)
        for current_root, _, names in walker:
            for fname in names:
                _register(current_root, fname)
    else:
        try:
            names = os.listdir(root_dir)
        except OSError as exc:
            errors.append(str(exc))
            return {"files": files, "duplicates": duplicates, "errors": errors}
        for fname in names:
            _register(root_dir, fname)

    return {"files": files, "duplicates": duplicates, "errors": errors}


def get_source_orbit_inventory(source_dir: str, recursive: bool = True) -> Dict[str, Any]:
    indexed = _index_orbit_txt_files(
        source_dir,
        recursive=recursive,
        skip_dir_names=_default_source_scan_skip_dir_names(),
    )
    by_satellite: Dict[str, int] = {}
    for item in indexed["files"].values():
        satellite = item["satellite"]
        by_satellite[satellite] = by_satellite.get(satellite, 0) + 1
    return {
        "path": source_dir,
        "total": len(indexed["files"]),
        "by_satellite": by_satellite,
        "files": indexed["files"],
        "duplicate_count": len(indexed["duplicates"]),
        "duplicates": indexed["duplicates"],
        "errors": indexed["errors"],
    }


def get_orbit_pool_inventory(
    envi_pool: str = "",
    isce2_pool: str = "",
    recursive: bool = True,
) -> Dict[str, Any]:
    envi_index = _index_orbit_txt_files(envi_pool, recursive=recursive)
    isce2_index = _index_orbit_xml_files(isce2_pool, recursive=recursive)

    by_satellite: Dict[str, int] = {}
    for item in envi_index["files"].values():
        satellite = item["satellite"]
        by_satellite[satellite] = by_satellite.get(satellite, 0) + 1

    return {
        "envi": {
            "path": envi_pool,
            "total": len(envi_index["files"]),
            "by_satellite": by_satellite,
            "files": envi_index["files"],
            "duplicate_count": len(envi_index["duplicates"]),
            "duplicates": envi_index["duplicates"],
            "errors": envi_index["errors"],
        },
        "isce2": {
            "path": isce2_pool,
            "total": len(isce2_index["files"]),
            "files": isce2_index["files"],
            "duplicate_count": len(isce2_index["duplicates"]),
            "duplicates": isce2_index["duplicates"],
            "errors": isce2_index["errors"],
        },
    }


# ---------------------------------------------------------------------------
# 转换器注册表
# ---------------------------------------------------------------------------

@dataclass
class ConvertResult:
    ok: bool
    output_path: str = ""
    error: str = ""


_CONVERTERS: Dict[str, Callable] = {}


def register_converter(fmt: str):
    """装饰器：注册格式转换函数。

    被装饰函数签名：
        fn(txt_path: str, output_path: str, annotation_xml: str = "") -> None
    """
    def decorator(fn: Callable) -> Callable:
        _CONVERTERS[fmt] = fn
        return fn
    return decorator


# ---------------------------------------------------------------------------
# ISCE2 XML converter
# ---------------------------------------------------------------------------


def _default_isce2_convert_script() -> str:
    return str(
        Path(__file__).resolve().parent.parent
        / "isce2_pipeline"
        / "convert_lt1_orbit_to_isce_xml.py"
    )

@register_converter("isce2")
def _convert_to_isce2_xml(
    txt_path: str,
    output_path: str,
    annotation_xml: str = "",
) -> None:
    """Convert an LT-1 text orbit file into an ISCE2 XML orbit file."""

    script = os.environ.get(
        "ISCE2_CONVERT_SCRIPT",
        _default_isce2_convert_script(),
    )
    if not os.path.isfile(script):
        raise FileNotFoundError(f"Orbit convert script not found: {script}")

    cmd = [sys.executable, script, txt_path, output_path]
    if annotation_xml and os.path.isfile(annotation_xml):
        cmd += ["--annotation-xml", annotation_xml]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        detail = result.stderr or result.stdout or "Orbit conversion failed without output"
        raise RuntimeError(_compact_error_message(detail))


# ---------------------------------------------------------------------------
# 核心操作
# ---------------------------------------------------------------------------

def _subdir_for_satellite(satellite: str) -> str:
    """返回卫星对应的子目录名（LT1A / LT1B）。"""
    return satellite.upper()


def organize_orbit_dir(orbit_root: str) -> Dict:
    """扫描 orbit_root 根目录下散落的 .txt，按卫星名移入子目录。

    Returns:
        {"moved": [...], "skipped": [...], "errors": [...]}
    """
    moved, skipped, errors = [], [], []

    if not os.path.isdir(orbit_root):
        return {"moved": moved, "skipped": skipped,
                "errors": [f"目录不存在：{orbit_root}"]}

    for fname in os.listdir(orbit_root):
        fpath = os.path.join(orbit_root, fname)
        if not os.path.isfile(fpath):
            continue
        parsed = parse_orbit_filename(fname)
        if not parsed:
            continue  # 不是轨道文件，忽略

        satellite, _ = parsed
        target_dir = os.path.join(orbit_root, _subdir_for_satellite(satellite))
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, fname)

        if os.path.exists(target_path):
            skipped.append({"file": fname, "reason": "目标已存在"})
            continue

        try:
            shutil.move(fpath, target_path)
            moved.append({"file": fname, "to": target_dir})
        except Exception as exc:
            errors.append({"file": fname, "error": str(exc)})

    return {"moved": moved, "skipped": skipped, "errors": errors}


def get_converted(
    orbit_root: str,
    satellite: str,
    date_yyyymmdd: str,
    target_format: str,
    annotation_xml: str = "",
) -> str:
    """按需转换精轨文件并归档，返回转换后文件的 Windows 路径。

    查找顺序：
      1. converted/<format>/ 下已有归档 → 直接返回
      2. <satellite>/ 子目录下找原始 .txt → 转换 → 归档 → 返回
      3. 根目录下找原始 .txt（兼容未整理状态）→ 同上

    Raises:
        FileNotFoundError: 找不到原始 .txt
        KeyError: 不支持的 target_format
        RuntimeError: 转换失败
    """
    if target_format not in _CONVERTERS:
        raise KeyError(f"不支持的轨道格式：{target_format}，"
                       f"已注册：{list(_CONVERTERS)}")

    satellite = satellite.upper()
    expected_stem = f"{satellite}_GpsData_GAS_C_{date_yyyymmdd}"

    # 1. 检查归档
    archive_dir = os.path.join(orbit_root, "converted", target_format)
    archive_path = os.path.join(archive_dir, expected_stem + ".xml")
    if os.path.isfile(archive_path):
        return archive_path

    # 2. 找原始 .txt（先找子目录，再找根目录）
    candidates = [
        os.path.join(orbit_root, satellite, expected_stem + ".txt"),
        os.path.join(orbit_root, expected_stem + ".txt"),
    ]
    txt_path = next((p for p in candidates if os.path.isfile(p)), None)
    if not txt_path:
        raise FileNotFoundError(
            f"未找到 {satellite} {date_yyyymmdd} 的精轨文件，"
            f"已查找：{candidates}"
        )

    # 3. 转换并归档
    os.makedirs(archive_dir, exist_ok=True)
    _CONVERTERS[target_format](txt_path, archive_path, annotation_xml)

    if not os.path.isfile(archive_path):
        raise RuntimeError(f"转换完成但输出文件不存在：{archive_path}")

    return archive_path


# ---------------------------------------------------------------------------
# 目录状态扫描
# ---------------------------------------------------------------------------

@dataclass
class OrbitDirStats:
    orbit_root: str
    root_loose: int = 0          # 根目录散落 .txt 数量
    by_satellite: Dict[str, int] = field(default_factory=dict)  # 子目录各卫星数量
    converted: Dict[str, int] = field(default_factory=dict)     # 各格式已转换数量
    total_source: int = 0
    supported_formats: List[str] = field(default_factory=list)
    duplicate_count: int = 0
    errors: List[str] = field(default_factory=list)


def scan_orbit_dir(orbit_root: str) -> OrbitDirStats:
    """扫描轨道目录，返回统计信息。"""
    stats = OrbitDirStats(
        orbit_root=orbit_root,
        supported_formats=list(_CONVERTERS.keys()),
    )

    if not os.path.isdir(orbit_root):
        return stats

    # 根目录散落文件
    for fname in os.listdir(orbit_root):
        fpath = os.path.join(orbit_root, fname)
        if os.path.isfile(fpath) and parse_orbit_filename(fname):
            stats.root_loose += 1

    source_inventory = get_source_orbit_inventory(orbit_root, recursive=True)
    stats.by_satellite = dict(source_inventory.get("by_satellite", {}))
    stats.total_source = int(source_inventory.get("total", 0))
    stats.duplicate_count = int(source_inventory.get("duplicate_count", 0))
    stats.errors.extend(source_inventory.get("errors", []))

    # converted 子目录
    converted_root = os.path.join(orbit_root, "converted")
    if os.path.isdir(converted_root):
        for fmt in os.listdir(converted_root):
            fmt_dir = os.path.join(converted_root, fmt)
            if os.path.isdir(fmt_dir):
                stats.converted[fmt] = sum(
                    1 for f in os.listdir(fmt_dir)
                    if f.endswith(".xml")
                )

    return stats


# ---------------------------------------------------------------------------
# 精轨池同步
# ---------------------------------------------------------------------------

def sync_orbit_pools(
    source_dir: str,
    envi_pool: str = "",
    isce2_pool: str = "",
    landsar_pool: str = "",
) -> Dict:
    """从 source_dir 扫描平铺 .txt 精轨，同步到各引擎本地池。

    - ENVI pool  : 按卫星名组织到 LT1A/ LT1B/ 子目录，复制 .txt
    - ISCE2 pool : 平铺 .xml（全轨道，无裁剪），pipeline 可直接用
    - LANDSAR pool: 预留，暂不处理

    Returns:
        {
            "total_source": int,
            "usable_source_count": int,
            "envi":  {"copied": [...], "updated": [...], "skipped": [...], "errors": [...]},
            "isce2": {"converted": [...], "reconverted": [...], "skipped": [...], "errors": [...]},
            "invalid_sources": [{"name": str, "source": str, "error": str, ...}],
        }
    """
    result: Dict = {
        "total_source": 0,
        "usable_source_count": 0,
        "source": {"errors": [], "duplicate_count": 0},
        "envi":  {"copied": [], "updated": [], "skipped": [], "errors": []},
        "isce2": {"converted": [], "reconverted": [], "skipped": [], "errors": []},
        "invalid_sources": [],
    }

    source_inventory = get_source_orbit_inventory(source_dir, recursive=True)
    result["total_source"] = int(source_inventory.get("total", 0))
    result["source"]["errors"] = list(source_inventory.get("errors", []))
    result["source"]["duplicate_count"] = int(source_inventory.get("duplicate_count", 0))

    if result["source"]["errors"] and result["total_source"] == 0:
        result["error"] = result["source"]["errors"][0]
        return result

    for stem in sorted(source_inventory["files"]):
        entry = source_inventory["files"][stem]
        fname = stem + ".txt"
        fpath = entry["path"]
        satellite = entry["satellite"]
        envi_dst = ""
        if envi_pool:
            sat_dir = os.path.join(envi_pool, satellite)
            envi_dst = os.path.join(sat_dir, fname)

        xml_name = stem + ".xml"
        xml_path = os.path.join(isce2_pool, xml_name) if isce2_pool else ""
        xml_existed = bool(xml_path and os.path.exists(xml_path))

        envi_needs_refresh = bool(envi_pool) and (
            not os.path.exists(envi_dst) or _needs_copy_refresh(fpath, envi_dst)
        )
        xml_needs_refresh = bool(isce2_pool) and (
            not os.path.exists(xml_path) or _needs_generated_refresh(fpath, xml_path)
        )
        requires_validation = xml_needs_refresh or (not isce2_pool and envi_needs_refresh)

        staged_dir = ""
        staged_xml = ""
        try:
            if requires_validation:
                staged_dir, staged_xml = _stage_isce2_xml(
                    fpath,
                    stem,
                    scratch_root=isce2_pool or envi_pool or os.path.dirname(fpath),
                )

            if isce2_pool:
                os.makedirs(isce2_pool, exist_ok=True)
                if xml_needs_refresh:
                    shutil.copy2(staged_xml, xml_path)
                    if xml_existed:
                        result["isce2"]["reconverted"].append(fname)
                    else:
                        result["isce2"]["converted"].append(fname)
                else:
                    result["isce2"]["skipped"].append(fname)

            if envi_pool:
                sat_dir = os.path.join(envi_pool, satellite)
                os.makedirs(sat_dir, exist_ok=True)
                if not os.path.exists(envi_dst):
                    shutil.copy2(fpath, envi_dst)
                    result["envi"]["copied"].append(fname)
                elif envi_needs_refresh:
                    shutil.copy2(fpath, envi_dst)
                    result["envi"]["updated"].append(fname)
                else:
                    result["envi"]["skipped"].append(fname)

            result["usable_source_count"] += 1
        except Exception as exc:
            result["invalid_sources"].append(
                _build_invalid_source_record(
                    stem,
                    fpath,
                    exc,
                    envi_path=envi_dst,
                    isce2_path=xml_path,
                )
            )
        finally:
            if staged_dir:
                shutil.rmtree(staged_dir, ignore_errors=True)

        # LANDSAR pool：预留，暂不处理

    return result


def repair_orbit_pools(
    source_dir: str,
    envi_pool: str = "",
    isce2_pool: str = "",
    landsar_pool: str = "",
) -> Dict[str, Any]:
    before = check_orbit_consistency(envi_pool, isce2_pool)
    sync_result = sync_orbit_pools(source_dir, envi_pool, isce2_pool, landsar_pool)

    repaired_from_envi: List[str] = []
    repair_errors: List[Dict[str, str]] = []

    pool_inventory = get_orbit_pool_inventory(envi_pool, isce2_pool, recursive=True)
    envi_files: Dict[str, Dict[str, str]] = pool_inventory["envi"]["files"]
    isce2_files: Dict[str, Dict[str, str]] = pool_inventory["isce2"]["files"]

    if isce2_pool:
        os.makedirs(isce2_pool, exist_ok=True)
        for stem in sorted(set(envi_files) - set(isce2_files)):
            txt_path = envi_files[stem]["path"]
            xml_path = os.path.join(isce2_pool, stem + ".xml")
            try:
                _convert_to_isce2_xml_file(txt_path, xml_path)
                repaired_from_envi.append(stem)
            except Exception as exc:
                item = _build_invalid_source_record(stem, txt_path, exc, envi_path=txt_path, isce2_path=xml_path)
                item["target"] = xml_path
                repair_errors.append(item)

    after = check_orbit_consistency(envi_pool, isce2_pool)
    return {
        "before": before,
        "after": after,
        "sync_result": sync_result,
        "repaired_from_envi": repaired_from_envi,
        "repair_error_count": len(repair_errors),
        "repair_errors": repair_errors,
        "healthy": after["healthy"],
        "mismatches": after["mismatches"],
        "envi": after["envi"],
        "isce2": after["isce2"],
    }


def quarantine_bad_orbits(
    source_dir: str,
    envi_pool: str = "",
    isce2_pool: str = "",
    quarantine_root: str = "",
) -> Dict[str, Any]:
    source_inventory = get_source_orbit_inventory(source_dir, recursive=True)
    pool_inventory = get_orbit_pool_inventory(envi_pool, isce2_pool, recursive=True)

    source_files: Dict[str, Dict[str, str]] = source_inventory["files"]
    envi_files: Dict[str, Dict[str, str]] = pool_inventory["envi"]["files"]
    isce2_files: Dict[str, Dict[str, str]] = pool_inventory["isce2"]["files"]

    quarantine_dir = _default_quarantine_root(source_dir, quarantine_root)
    candidate_stems = sorted(set(source_files) - set(isce2_files))

    result: Dict[str, Any] = {
        "quarantine_root": quarantine_dir,
        "candidate_count": len(candidate_stems),
        "validated_count": 0,
        "confirmed_bad_count": 0,
        "confirmed_bad": [],
        "skipped_valid": [],
        "errors": [],
    }

    for stem in candidate_stems:
        source_path = source_files[stem]["path"]
        envi_path = envi_files.get(stem, {}).get("path", "")
        isce2_path = isce2_files.get(stem, {}).get("path", "")

        validation = validate_orbit_source(
            source_path,
            stem,
            scratch_root=quarantine_dir or envi_pool or isce2_pool or os.path.dirname(source_path),
        )
        result["validated_count"] += 1
        if validation.get("ok"):
            result["skipped_valid"].append(
                {
                    "name": stem,
                    "source": source_path,
                    "reason": "源 TXT 仍可成功转换为 XML，未执行隔离",
                }
            )
            continue

        item = dict(validation)
        try:
            source_target = _unique_path(
                os.path.join(
                    quarantine_dir,
                    "source",
                    _safe_relpath(source_path, source_dir),
                )
            )
            os.makedirs(os.path.dirname(source_target), exist_ok=True)
            shutil.move(source_path, source_target)
            item["quarantined_source"] = source_target
        except Exception as exc:
            result["errors"].append({"name": stem, "scope": "source", "error": str(exc)})

        if envi_path and os.path.exists(envi_path):
            try:
                envi_target = _unique_path(
                    os.path.join(
                        quarantine_dir,
                        "envi",
                        _safe_relpath(envi_path, envi_pool),
                    )
                )
                os.makedirs(os.path.dirname(envi_target), exist_ok=True)
                shutil.move(envi_path, envi_target)
                item["quarantined_envi"] = envi_target
            except Exception as exc:
                result["errors"].append({"name": stem, "scope": "envi", "error": str(exc)})

        if isce2_path and os.path.exists(isce2_path):
            try:
                isce2_target = _unique_path(
                    os.path.join(
                        quarantine_dir,
                        "isce2",
                        _safe_relpath(isce2_path, isce2_pool),
                    )
                )
                os.makedirs(os.path.dirname(isce2_target), exist_ok=True)
                shutil.move(isce2_path, isce2_target)
                item["quarantined_isce2"] = isce2_target
            except Exception as exc:
                result["errors"].append({"name": stem, "scope": "isce2", "error": str(exc)})

        result["confirmed_bad"].append(item)

    result["confirmed_bad_count"] = len(result["confirmed_bad"])
    result["after"] = check_orbit_consistency(envi_pool, isce2_pool)
    result["source_gaps_after"] = summarize_source_orbit_gaps(source_dir, envi_pool, isce2_pool, quarantine_dir)
    return result


# ---------------------------------------------------------------------------
# 精轨一致性检查
# ---------------------------------------------------------------------------

def check_orbit_consistency(
    envi_pool: str = "",
    isce2_pool: str = "",
) -> Dict:
    """对比本地引擎池中的实际文件，返回一致性报告。

    扫描 ENVI 池（LT1A/ LT1B/ 子目录）和 ISCE2 池（平铺 .xml），
    统计各池实际文件数量，并检查两池之间的对应关系是否一致。

    Returns:
        {
            "envi":  {"total": int, "by_satellite": {"LT1A": int, "LT1B": int}, "files": [...]},
            "isce2": {"total": int, "files": [...]},
            "mismatches": [{"name": str, "issue": str}],  # ENVI 有但 ISCE2 无，或反之
            "healthy": bool,
        }
    """
    inventory = get_orbit_pool_inventory(envi_pool, isce2_pool, recursive=True)
    envi_files: Dict[str, Dict[str, str]] = inventory["envi"]["files"]
    isce2_files: Dict[str, Dict[str, str]] = inventory["isce2"]["files"]
    by_satellite: Dict[str, int] = inventory["envi"]["by_satellite"]

    # 对比
    envi_stems = set(envi_files)
    isce2_stems = set(isce2_files)
    mismatches = []
    for stem in envi_stems - isce2_stems:
        mismatches.append(
            {
                "name": stem,
                "issue_code": "MISSING_ISCE2_XML",
                "issue": "ENVI 有但 ISCE2 池缺少对应 XML",
                "envi_path": envi_files[stem]["path"],
            }
        )
    for stem in isce2_stems - envi_stems:
        mismatches.append(
            {
                "name": stem,
                "issue_code": "MISSING_ENVI_TXT",
                "issue": "ISCE2 有但 ENVI 池缺少对应 TXT",
                "isce2_path": isce2_files[stem]["path"],
            }
        )

    mismatches.sort(key=lambda item: item["name"])

    return {
        "envi": {
            "path": envi_pool,
            "total": len(envi_files),
            "by_satellite": by_satellite,
        },
        "isce2": {
            "path": isce2_pool,
            "total": len(isce2_files),
        },
        "error_count": len(inventory["envi"]["errors"]) + len(inventory["isce2"]["errors"]),
        "errors": list(inventory["envi"]["errors"]) + list(inventory["isce2"]["errors"]),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "healthy": len(mismatches) == 0,
    }
