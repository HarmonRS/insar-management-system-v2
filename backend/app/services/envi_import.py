"""Import workflow functions extracted from envi_service.py."""
from __future__ import annotations

import os
from glob import glob
from typing import Any, Dict, List

from .envi_service import (
    DEFAULT_TIMEOUT,
    _collect_task_folders,
    _find_meta_files,
    _has_sml,
    _to_local_path,
    execute_envi_task,
)


def _import_single_dir(
    display_name: str,
    data_dir: str,
    log_lines: List[str],
) -> tuple:
    """Import all .meta.xml in one directory. Returns (processed, failed, skipped)."""
    import time
    if _has_sml(data_dir):
        log_lines.append(f"[skip] {display_name}: .sml already exists")
        return 0, 0, 1

    meta_files = _find_meta_files(data_dir)
    if not meta_files:
        log_lines.append(f"[skip] {display_name}: no *.meta.xml")
        return 0, 0, 0

    processed = 0
    failed = 0
    for meta_file in meta_files:
        start = time.time()
        try:
            execute_envi_task(
                "SARsImportLuTan1",
                {
                    "INPUT_FILE_LIST": [meta_file],
                    "ROOT_URI_FOR_OUTPUT": data_dir,
                },
            )
            elapsed = round(time.time() - start, 1)
            processed += 1
            log_lines.append(
                f"[ok] import {display_name}: "
                f"{os.path.basename(meta_file)} ({elapsed}s)"
            )
        except Exception as exc:
            elapsed = round(time.time() - start, 1)
            failed += 1
            log_lines.append(
                f"[err] import {display_name}: "
                f"{os.path.basename(meta_file)} failed ({elapsed}s): {exc}"
            )
    return processed, failed, 0


def run_import_workflow(
    root_dir: str,
    num_to_process: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Scan root_dir for raw SAR data and batch-import via SARsImportLuTan1."""
    root_dir = _to_local_path(root_dir)
    if not root_dir or not os.path.isdir(root_dir):
        raise ValueError(f"Import root directory does not exist: {root_dir}")

    task_folders = _collect_task_folders(root_dir)
    scan_mode = "task_folder"
    if not task_folders:
        task_folders = sorted(
            p for p in glob(os.path.join(root_dir, "*")) if os.path.isdir(p)
        )
        scan_mode = "flat_folder"

    log_lines: List[str] = [
        f"[envi] import task=SARsImportLuTan1",
        f"[envi] root_dir={root_dir}",
        f"[envi] scan_mode={scan_mode}",
        f"[envi] found folders={len(task_folders)}",
    ]
    if not task_folders:
        return {
            "summary": {
                "scan_mode": scan_mode,
                "task_folders": 0,
                "processed": 0,
                "failed": 0,
                "skipped": 0,
            },
            "log_lines": log_lines,
        }

    total_processed = 0
    total_failed = 0
    total_skipped = 0

    for folder in task_folders:
        if num_to_process > 0 and total_processed >= num_to_process:
            log_lines.append(f"[envi] reached limit={num_to_process}")
            break
        name = os.path.basename(folder)

        if scan_mode == "flat_folder":
            candidates = [(name, folder)]
        else:
            candidates = []
            for side in ("master", "slave"):
                d = os.path.join(folder, side)
                if os.path.isdir(d):
                    candidates.append((f"{name}/{side}", d))
                else:
                    log_lines.append(f"[warn] {name}/{side} missing, skip")

        for display, data_dir in candidates:
            if num_to_process > 0 and total_processed >= num_to_process:
                break
            p, f, s = _import_single_dir(display, data_dir, log_lines)
            total_processed += p
            total_failed += f
            total_skipped += s

    if total_failed > 0 and total_processed == 0:
        detail = "\n".join(log_lines[-20:])
        raise RuntimeError(
            f"All import tasks failed. failed={total_failed}.\n{detail}"
        )

    return {
        "summary": {
            "scan_mode": scan_mode,
            "task_folders": len(task_folders),
            "processed": total_processed,
            "failed": total_failed,
            "skipped": total_skipped,
        },
        "log_lines": log_lines,
    }


def inspect_import(root_dir: str) -> Dict[str, Any]:
    """Pre-check Import readiness for a root directory."""
    root_dir = _to_local_path(root_dir)
    result: Dict[str, Any] = {
        "workflow": "import",
        "root_dir": root_dir,
        "exists": bool(root_dir and os.path.isdir(root_dir)),
        "ready": False,
        "summary": {},
        "warnings": [],
    }
    if not result["exists"]:
        result["warnings"].append("root_dir does not exist.")
        return result

    task_folders = _collect_task_folders(root_dir)
    scan_mode = "task_folder"
    scan_targets = task_folders
    if not task_folders:
        scan_mode = "flat_folder"
        scan_targets = sorted(
            p for p in glob(os.path.join(root_dir, "*")) if os.path.isdir(p)
        )

    meta_count = 0
    sml_count = 0
    candidate_count = 0
    for folder in scan_targets:
        if scan_mode == "flat_folder":
            candidates = [folder]
        else:
            candidates = [
                os.path.join(folder, "master"),
                os.path.join(folder, "slave"),
            ]
        for candidate in candidates:
            if not os.path.isdir(candidate):
                continue
            metas = glob(os.path.join(candidate, "*.meta.xml"))
            smls = glob(os.path.join(candidate, "*.sml"))
            meta_count += len(metas)
            sml_count += len(smls)
            if metas and not smls:
                candidate_count += len(metas)

    result["summary"] = {
        "scan_mode": scan_mode,
        "folder_count": len(scan_targets),
        "meta_file_count": meta_count,
        "existing_sml_count": sml_count,
        "import_candidate_count": candidate_count,
    }
    result["ready"] = candidate_count > 0
    if candidate_count == 0:
        result["warnings"].append(
            "No import candidates (*.meta.xml without *.sml)."
        )
    return result
