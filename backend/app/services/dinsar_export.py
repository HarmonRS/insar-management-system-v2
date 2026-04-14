"""D-InSAR 结果导出服务：将结果文件复制到用户指定目录。"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 允许的文件扩展名（主文件 + 附属文件）
_ASSOCIATED_EXTENSIONS = ["", ".hdr", ".sml", ".xml", ".aux.xml", ".prj"]


def export_dinsar_results(
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
