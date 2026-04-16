"""D-InSAR 结果导出服务：将结果文件复制到用户指定目录。"""
from __future__ import annotations

import logging
import os
import re
import shutil
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 允许的文件扩展名（主文件 + 附属文件）
_ASSOCIATED_EXTENSIONS = ["", ".hdr", ".sml", ".xml", ".aux.xml", ".prj"]
_INVALID_SEGMENT_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def _legacy_export_dinsar_results_flat(
    file_paths: List[str],
    target_dir: str,
) -> Dict[str, Any]:
    """
    将指定的 D-InSAR 结果文件复制到目标目录。

    支持本地路径和 UNC 路径（如 \\\\server\\share\\path）。
    对每个主文件，同时复制同名的附属文件（.hdr, .sml 等）。

    返回复制统计信息。
    """
    # 规范化目标路径
    target_dir = target_dir.strip()
    if not target_dir:
        raise ValueError("目标路径不能为空")

    # 创建目标目录
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as e:
        raise ValueError(f"无法创建目标目录 '{target_dir}': {e}")

    copied = 0
    skipped = 0
    failed = 0
    details: List[Dict[str, Any]] = []

    for file_path in file_paths:
        file_path = file_path.strip()
        if not file_path:
            continue

        if not os.path.isfile(file_path):
            failed += 1
            details.append({
                "source": file_path,
                "status": "error",
                "message": "源文件不存在",
            })
            continue

        base_name = os.path.basename(file_path)
        src_dir = os.path.dirname(file_path)

        item_copied = 0
        item_skipped = 0
        item_failed = 0

        for ext in _ASSOCIATED_EXTENSIONS:
            src = os.path.join(src_dir, base_name + ext) if ext else file_path
            if not os.path.isfile(src):
                continue

            dst = os.path.join(target_dir, base_name + ext)
            try:
                if os.path.isfile(dst):
                    # 大小相同则跳过
                    if os.path.getsize(src) == os.path.getsize(dst):
                        item_skipped += 1
                        skipped += 1
                        continue
                shutil.copy2(src, dst)
                item_copied += 1
                copied += 1
            except OSError as e:
                item_failed += 1
                failed += 1
                logger.warning("复制文件失败 %s -> %s: %s", src, dst, e)

        status = "ok" if item_failed == 0 else "partial"
        details.append({
            "source": file_path,
            "name": base_name,
            "status": status,
            "copied": item_copied,
            "skipped": item_skipped,
            "failed": item_failed,
        })

    return {
        "total_files": len(file_paths),
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "target_dir": target_dir,
        "details": details,
    }


def _derive_folder_hint(file_path: str) -> str:
    base_name = os.path.basename(str(file_path or "").strip())
    stem, _ = os.path.splitext(base_name)
    for suffix in ("_geo_disp", "_disp", "_coh"):
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)] or stem
    return stem


def _sanitize_path_segment(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    text = _INVALID_SEGMENT_RE.sub("_", text).strip(" .")
    if not text:
        text = default
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        text = f"_{text}"
    return text[:120]


def _normalize_export_item(raw_item: Any, index: int) -> Dict[str, Any]:
    if isinstance(raw_item, dict):
        source_path = str(
            raw_item.get("source")
            or raw_item.get("file_path")
            or raw_item.get("path")
            or ""
        ).strip()
        folder_hint = (
            raw_item.get("task_alias")
            or raw_item.get("task_name")
            or raw_item.get("display_name")
            or raw_item.get("name")
            or ""
        )
    else:
        source_path = str(raw_item or "").strip()
        folder_hint = ""

    base_name = os.path.basename(source_path) if source_path else ""
    folder_name = _sanitize_path_segment(
        folder_hint or _derive_folder_hint(source_path),
        default=f"result_{index}",
    )
    return {
        "source_path": source_path,
        "base_name": base_name,
        "folder_name": folder_name,
    }


def _same_file_size(left: str, right: str) -> bool:
    try:
        return os.path.getsize(left) == os.path.getsize(right)
    except OSError:
        return False


def _reserve_target_dir(
    *,
    target_root: str,
    folder_name: str,
    base_name: str,
    source_path: str,
    planned_primary_paths: Dict[str, str],
) -> str:
    suffix = 1
    while True:
        candidate_name = folder_name if suffix == 1 else f"{folder_name}__{suffix}"
        candidate_dir = os.path.join(target_root, candidate_name)
        candidate_primary = os.path.join(candidate_dir, base_name)
        normalized_primary = os.path.normcase(os.path.abspath(candidate_primary))
        planned_source = planned_primary_paths.get(normalized_primary)
        if planned_source and os.path.normcase(planned_source) != os.path.normcase(source_path):
            suffix += 1
            continue
        if os.path.isfile(candidate_primary) and not _same_file_size(source_path, candidate_primary):
            suffix += 1
            continue
        planned_primary_paths[normalized_primary] = source_path
        return candidate_dir


def export_dinsar_results(
    items: List[Any],
    target_dir: str,
) -> Dict[str, Any]:
    """Copy selected D-InSAR results into task-based subdirectories."""
    target_dir = target_dir.strip()
    if not target_dir:
        raise ValueError("目标路径不能为空")

    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"无法创建目标目录 '{target_dir}': {exc}") from exc

    copied = 0
    skipped = 0
    failed = 0
    details: List[Dict[str, Any]] = []
    planned_primary_paths: Dict[str, str] = {}

    for index, raw_item in enumerate(items, start=1):
        item = _normalize_export_item(raw_item, index)
        source_path = item["source_path"]
        base_name = item["base_name"]
        folder_name = item["folder_name"]

        if not source_path:
            continue

        if not os.path.isfile(source_path):
            failed += 1
            details.append(
                {
                    "source": source_path,
                    "folder_name": folder_name,
                    "status": "error",
                    "message": "源文件不存在",
                }
            )
            continue

        target_item_dir = _reserve_target_dir(
            target_root=target_dir,
            folder_name=folder_name,
            base_name=base_name,
            source_path=source_path,
            planned_primary_paths=planned_primary_paths,
        )
        src_dir = os.path.dirname(source_path)
        item_copied = 0
        item_skipped = 0
        item_failed = 0
        target_files: List[str] = []

        for ext in _ASSOCIATED_EXTENSIONS:
            src = os.path.join(src_dir, base_name + ext) if ext else source_path
            if not os.path.isfile(src):
                continue

            dst = os.path.join(target_item_dir, base_name + ext)
            target_files.append(dst)
            try:
                os.makedirs(target_item_dir, exist_ok=True)
                if os.path.isfile(dst) and _same_file_size(src, dst):
                    item_skipped += 1
                    skipped += 1
                    continue
                shutil.copy2(src, dst)
                item_copied += 1
                copied += 1
            except OSError as exc:
                item_failed += 1
                failed += 1
                logger.warning("Failed to copy D-InSAR result %s -> %s: %s", src, dst, exc)

        status = "ok" if item_failed == 0 else "partial"
        details.append(
            {
                "source": source_path,
                "name": base_name,
                "folder_name": os.path.basename(target_item_dir),
                "target_dir": target_item_dir,
                "target_files": target_files,
                "status": status,
                "copied": item_copied,
                "skipped": item_skipped,
                "failed": item_failed,
            }
        )

    return {
        "total_files": len(items),
        "copied": copied,
        "skipped": skipped,
        "failed": failed,
        "target_dir": target_dir,
        "details": details,
    }
