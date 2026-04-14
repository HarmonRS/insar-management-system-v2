"""D-InSAR result extraction and task overview helpers."""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from .envi_service import _read_env, _to_local_path

_ENVI_DISP_PATTERN = re.compile(r"^out_ISARPTD_(\d{14})_rsp_disp$")
_ISCE2_DISP_PATTERN = re.compile(r"^(?P<prefix>.+)_disp\.(?:tif|tiff)$", re.IGNORECASE)
_ISCE2_SKIP_PATTERN = re.compile(r".+_disp_full\.(?:tif|tiff)$", re.IGNORECASE)


def _get_dinsar_target_dir() -> str:
    raw = _read_env("MONITOR_DINSAR_DIRS", "")
    dirs = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not dirs:
        raise ValueError("MONITOR_DINSAR_DIRS 未在 .env 中配置")
    return dirs[0]


def _normalize_directory(path: str, label: str) -> str:
    local_path = os.path.normpath(os.path.abspath(_to_local_path(path)))
    if not os.path.isdir(local_path):
        raise ValueError(f"{label}不存在: {path}")
    return local_path


def _normalize_output_directory(path: str) -> str:
    return os.path.normpath(os.path.abspath(_to_local_path(path)))


def _same_path(left: Optional[str], right: Optional[str]) -> bool:
    if not left or not right:
        return False
    try:
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))
    except OSError:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _looks_like_task_dir(path: str) -> bool:
    return (
        os.path.isdir(os.path.join(path, "master"))
        and os.path.isdir(os.path.join(path, "slave"))
    )


def _iter_task_dirs(root_dir: str) -> List[Tuple[str, str]]:
    if _looks_like_task_dir(root_dir):
        return [(os.path.basename(os.path.normpath(root_dir)), root_dir)]

    task_dirs: List[Tuple[str, str]] = []
    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                if not entry.is_dir():
                    continue
                if not entry.name.startswith("Task_"):
                    continue
                task_dirs.append((entry.name, entry.path))
    except OSError:
        return []

    task_dirs.sort(key=lambda item: item[0].lower())
    return task_dirs


def _root_has_isce2_products(root_dir: str) -> bool:
    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if _ISCE2_SKIP_PATTERN.match(entry.name):
                    continue
                if _ISCE2_DISP_PATTERN.match(entry.name):
                    return True
    except OSError:
        return False
    return False


def _find_envi_task_result(task_dir: str) -> Optional[Dict[str, Any]]:
    dinsar_results_dir = os.path.join(task_dir, "dinsar_results")
    if not os.path.isdir(dinsar_results_dir):
        return None

    candidates: List[Tuple[str, str]] = []
    try:
        for entry in os.scandir(dinsar_results_dir):
            if not entry.is_file():
                continue
            match = _ENVI_DISP_PATTERN.match(entry.name)
            if match:
                candidates.append((match.group(1), entry.name))
    except OSError:
        return None

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, best_name = candidates[0]
    base_path = os.path.join(dinsar_results_dir, best_name)
    source_files = [base_path]
    for ext in (".hdr", ".sml"):
        candidate = base_path + ext
        if os.path.isfile(candidate):
            source_files.append(candidate)

    return {
        "engine": "envi",
        "task_name": os.path.basename(os.path.normpath(task_dir)),
        "source_dir": dinsar_results_dir,
        "source_files": source_files,
    }


def _find_isce2_coherence_file(directory: str, prefix: str) -> Optional[str]:
    for name in (f"{prefix}_coh.tif", f"{prefix}_coh.tiff"):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def _build_isce2_product_index(root_dir: Optional[str]) -> Dict[str, Dict[str, Any]]:
    if not root_dir or not os.path.isdir(root_dir):
        return {}

    index: Dict[str, Dict[str, Any]] = {}
    for current_root, _, files in os.walk(root_dir):
        for file_name in files:
            if _ISCE2_SKIP_PATTERN.match(file_name):
                continue

            match = _ISCE2_DISP_PATTERN.match(file_name)
            if not match:
                continue

            prefix = match.group("prefix")
            disp_path = os.path.join(current_root, file_name)
            try:
                stat = os.stat(disp_path)
                scan_ts = max(stat.st_mtime, stat.st_ctime)
            except OSError:
                scan_ts = 0.0

            existing = index.get(prefix)
            if existing and existing.get("scan_ts", 0.0) >= scan_ts:
                continue

            coh_path = _find_isce2_coherence_file(current_root, prefix)
            source_files = [disp_path]
            if coh_path:
                source_files.append(coh_path)

            index[prefix] = {
                "engine": "isce2",
                "task_name": prefix,
                "source_dir": current_root,
                "source_files": source_files,
                "scan_ts": scan_ts,
            }

    return index


def _merge_isce2_indexes(*indexes: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for index in indexes:
        for prefix, item in index.items():
            existing = merged.get(prefix)
            if not existing or item.get("scan_ts", 0.0) >= existing.get("scan_ts", 0.0):
                merged[prefix] = item
    return merged


def _extract_envi_result(
    *,
    task_name: str,
    result: Dict[str, Any],
    target_dir: str,
) -> Dict[str, Any]:
    target_base = os.path.join(target_dir, f"{task_name}_geo_disp")
    details = {
        "task": task_name,
        "engine": "envi",
        "target_name": f"{task_name}_geo_disp",
        "source_dir": result.get("source_dir", ""),
        "source_files": list(result.get("source_files", [])),
        "target_files": [],
        "status": "ok",
        "copied": 0,
        "skipped": 0,
        "overwritten": 0,
        "failed": 0,
    }

    for source_path in result.get("source_files", []):
        suffix = source_path[len(result["source_files"][0]):]
        target_path = target_base + suffix
        details["target_files"].append(target_path)
        try:
            if os.path.isfile(target_path):
                if os.path.getsize(source_path) == os.path.getsize(target_path):
                    details["skipped"] += 1
                    continue
                shutil.copy2(source_path, target_path)
                details["overwritten"] += 1
            else:
                shutil.copy2(source_path, target_path)
                details["copied"] += 1
        except OSError as exc:
            details["failed"] += 1
            details["status"] = f"error: {exc}"

    return details


def _extract_isce2_result(
    *,
    task_name: str,
    result: Dict[str, Any],
    target_dir: str,
) -> Dict[str, Any]:
    details = {
        "task": task_name,
        "engine": "isce2",
        "target_name": task_name,
        "source_dir": result.get("source_dir", ""),
        "source_files": list(result.get("source_files", [])),
        "target_files": [],
        "status": "ok",
        "copied": 0,
        "skipped": 0,
        "overwritten": 0,
        "failed": 0,
    }

    for source_path in result.get("source_files", []):
        target_path = os.path.join(target_dir, os.path.basename(source_path))
        details["target_files"].append(target_path)
        try:
            if os.path.isfile(target_path):
                if os.path.getsize(source_path) == os.path.getsize(target_path):
                    details["skipped"] += 1
                    continue
                shutil.copy2(source_path, target_path)
                details["overwritten"] += 1
            else:
                shutil.copy2(source_path, target_path)
                details["copied"] += 1
        except OSError as exc:
            details["failed"] += 1
            details["status"] = f"error: {exc}"

    return details


def _collect_extracted_paths(target_dir: Optional[str], task_name: str) -> List[str]:
    if not target_dir or not os.path.isdir(target_dir):
        return []

    candidates = [
        os.path.join(target_dir, f"{task_name}_geo_disp"),
        os.path.join(target_dir, f"{task_name}_disp.tif"),
        os.path.join(target_dir, f"{task_name}_disp.tiff"),
        os.path.join(target_dir, f"{task_name}_coh.tif"),
        os.path.join(target_dir, f"{task_name}_coh.tiff"),
    ]
    return [path for path in candidates if os.path.isfile(path)]


def _build_task_status(
    *,
    task_name: str,
    task_path: str,
    target_dir: Optional[str],
    isce2_index: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    has_master = os.path.isdir(os.path.join(task_path, "master"))
    has_slave = os.path.isdir(os.path.join(task_path, "slave"))
    has_structure = has_master and has_slave

    imported = False
    for subdir in ("master", "slave"):
        local_dir = os.path.join(task_path, subdir)
        if not os.path.isdir(local_dir):
            continue
        try:
            if any(file_name.endswith(".sml") for file_name in os.listdir(local_dir)):
                imported = True
                break
        except OSError:
            continue

    envi_result = _find_envi_task_result(task_path)
    isce2_result = isce2_index.get(task_name)

    result_engines: List[str] = []
    result_paths: List[str] = []
    if envi_result:
        result_engines.append("envi")
        result_paths.extend(envi_result.get("source_files", []))
    if isce2_result:
        result_engines.append("isce2")
        result_paths.extend(isce2_result.get("source_files", []))

    extracted_paths = _collect_extracted_paths(target_dir, task_name)

    try:
        mtime = os.path.getmtime(task_path)
        last_modified = datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        last_modified = "-"

    return {
        "task_name": task_name,
        "has_structure": has_structure,
        "imported": imported,
        "dinsar_done": bool(result_engines),
        "extracted": bool(extracted_paths),
        "result_engines": result_engines,
        "result_paths": result_paths,
        "extracted_paths": extracted_paths,
        "last_modified": last_modified,
    }


def extract_disp_results(root_dir: str, dest_dir: Optional[str] = None) -> Dict[str, Any]:
    """Extract ENVI and ISCE2 displacement results into the monitor directory."""
    source_root = _normalize_directory(root_dir, "结果根目录")
    if dest_dir:
        target_dir = _normalize_output_directory(dest_dir)
    else:
        target_dir = _normalize_output_directory(_get_dinsar_target_dir())
    os.makedirs(target_dir, exist_ok=True)

    task_dirs = _iter_task_dirs(source_root)

    configured_isce2_root = ""
    if settings.ISCE2_OUTPUT_ROOT:
        candidate_root = os.path.normpath(os.path.abspath(settings.ISCE2_OUTPUT_ROOT))
        if os.path.isdir(candidate_root):
            configured_isce2_root = candidate_root

    scan_source_root_for_isce2 = (not task_dirs) or _root_has_isce2_products(source_root)
    direct_isce2_index = _build_isce2_product_index(source_root) if scan_source_root_for_isce2 else {}
    configured_isce2_index = {}
    if configured_isce2_root and not _same_path(configured_isce2_root, source_root):
        configured_isce2_index = _build_isce2_product_index(configured_isce2_root)

    isce2_index = _merge_isce2_indexes(direct_isce2_index, configured_isce2_index)

    details: List[Dict[str, Any]] = []
    copied = 0
    skipped = 0
    overwritten = 0
    failed = 0

    if task_dirs:
        for task_name, task_path in task_dirs:
            envi_result = _find_envi_task_result(task_path)
            if envi_result:
                detail = _extract_envi_result(task_name=task_name, result=envi_result, target_dir=target_dir)
                details.append(detail)

            isce2_result = isce2_index.get(task_name)
            if isce2_result:
                detail = _extract_isce2_result(task_name=task_name, result=isce2_result, target_dir=target_dir)
                details.append(detail)
    else:
        for task_name in sorted(isce2_index):
            detail = _extract_isce2_result(task_name=task_name, result=isce2_index[task_name], target_dir=target_dir)
            details.append(detail)

    for item in details:
        copied += int(item.get("copied", 0))
        skipped += int(item.get("skipped", 0))
        overwritten += int(item.get("overwritten", 0))
        failed += int(item.get("failed", 0))

    return {
        "processed": len(details),
        "copied": copied,
        "skipped": skipped,
        "overwritten": overwritten,
        "failed": failed,
        "target_dir": target_dir,
        "source_root": source_root,
        "details": details,
    }


def get_task_overview(root_dir: str) -> Dict[str, Any]:
    """Return task overview for both ENVI and ISCE2 D-InSAR products."""
    source_root = _normalize_directory(root_dir, "任务根目录")
    task_dirs = _iter_task_dirs(source_root)

    try:
        target_dir = _normalize_output_directory(_get_dinsar_target_dir())
    except ValueError:
        target_dir = None

    configured_isce2_root = ""
    if settings.ISCE2_OUTPUT_ROOT:
        candidate_root = os.path.normpath(os.path.abspath(settings.ISCE2_OUTPUT_ROOT))
        if os.path.isdir(candidate_root):
            configured_isce2_root = candidate_root

    direct_isce2_index = _build_isce2_product_index(source_root) if _root_has_isce2_products(source_root) else {}
    configured_isce2_index = {}
    if configured_isce2_root and not _same_path(configured_isce2_root, source_root):
        configured_isce2_index = _build_isce2_product_index(configured_isce2_root)
    isce2_index = _merge_isce2_indexes(direct_isce2_index, configured_isce2_index)

    tasks: List[Dict[str, Any]] = []
    summary = {
        "total": 0,
        "imported": 0,
        "dinsar_done": 0,
        "extracted": 0,
        "envi_done": 0,
        "isce2_done": 0,
        "envi_extracted": 0,
        "isce2_extracted": 0,
    }

    for task_name, task_path in task_dirs:
        task_status = _build_task_status(
            task_name=task_name,
            task_path=task_path,
            target_dir=target_dir,
            isce2_index=isce2_index,
        )
        tasks.append(task_status)

        summary["total"] += 1
        if task_status["imported"]:
            summary["imported"] += 1
        if task_status["dinsar_done"]:
            summary["dinsar_done"] += 1
        if task_status["extracted"]:
            summary["extracted"] += 1
        if "envi" in task_status["result_engines"]:
            summary["envi_done"] += 1
        if "isce2" in task_status["result_engines"]:
            summary["isce2_done"] += 1

        extracted_paths = task_status.get("extracted_paths", [])
        if any(path.endswith("_geo_disp") for path in extracted_paths):
            summary["envi_extracted"] += 1
        if any(_ISCE2_DISP_PATTERN.match(os.path.basename(path)) for path in extracted_paths):
            summary["isce2_extracted"] += 1

    return {
        "tasks": tasks,
        "summary": summary,
        "target_dir": target_dir or "",
        "source_root": source_root,
        "isce2_output_root": configured_isce2_root,
    }
