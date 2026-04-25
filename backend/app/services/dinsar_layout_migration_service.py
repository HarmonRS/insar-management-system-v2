from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

from ..config import settings
from .dinsar_naming import RUN_META_FILENAME
from .dinsar_result_layout_service import (
    EXECUTION_MANIFEST_FILENAME,
    PACKAGE_MANIFEST_FILENAME,
    get_run_disp_asset_paths,
    get_run_native_output_dir,
    normalize_envi_run_layout,
)


_ENVI_ENGINE_CODES = {"envi", "sarscape"}
_LEGACY_DISP_RE = re.compile(r"^.+_rsp_disp$", re.IGNORECASE)


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(str(path or "").strip()))


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _iter_managed_run_dirs(root_dir: str) -> Iterable[str]:
    normalized_root = _normalize_path(root_dir)
    if not os.path.isdir(normalized_root):
        return
    for pair_name in sorted(os.listdir(normalized_root)):
        pair_dir = os.path.join(normalized_root, pair_name)
        if not os.path.isdir(pair_dir):
            continue
        runs_dir = os.path.join(pair_dir, "runs")
        if not os.path.isdir(runs_dir):
            continue
        for run_name in sorted(os.listdir(runs_dir)):
            run_dir = os.path.join(runs_dir, run_name)
            if os.path.isdir(run_dir):
                yield run_dir


def _read_run_payloads(run_dir: str) -> Dict[str, Dict[str, Any]]:
    normalized_run_dir = _normalize_path(run_dir)
    payloads: Dict[str, Dict[str, Any]] = {}

    execution_manifest = _load_json(os.path.join(normalized_run_dir, EXECUTION_MANIFEST_FILENAME))
    if execution_manifest:
        payloads["execution"] = execution_manifest

    run_meta = _load_json(os.path.join(normalized_run_dir, RUN_META_FILENAME))
    if run_meta:
        payloads["run_meta"] = run_meta

    package_manifest = _load_json(os.path.join(normalized_run_dir, PACKAGE_MANIFEST_FILENAME))
    if package_manifest:
        payloads["package"] = package_manifest

    return payloads


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _resolve_engine_code(payloads: Dict[str, Dict[str, Any]]) -> str:
    package_payload = payloads.get("package") or {}
    package_engine = package_payload.get("engine") if isinstance(package_payload.get("engine"), dict) else {}
    return (
        _first_text(
            (payloads.get("execution") or {}).get("engine_code"),
            (payloads.get("run_meta") or {}).get("engine_code"),
            package_engine.get("code"),
            package_payload.get("engine_code"),
        )
        or ""
    ).strip().lower()


def _resolve_pair_key(run_dir: str, payloads: Dict[str, Dict[str, Any]]) -> Optional[str]:
    package_payload = payloads.get("package") or {}
    package_identity = package_payload.get("identity") if isinstance(package_payload.get("identity"), dict) else {}
    return _first_text(
        (payloads.get("execution") or {}).get("pair_key"),
        (payloads.get("run_meta") or {}).get("pair_key"),
        package_identity.get("pair_key"),
        package_payload.get("pair_key"),
        os.path.basename(os.path.dirname(os.path.dirname(_normalize_path(run_dir)))),
    )


def _resolve_run_key(run_dir: str, payloads: Dict[str, Dict[str, Any]]) -> Optional[str]:
    package_payload = payloads.get("package") or {}
    package_identity = package_payload.get("identity") if isinstance(package_payload.get("identity"), dict) else {}
    return _first_text(
        (payloads.get("execution") or {}).get("run_key"),
        (payloads.get("run_meta") or {}).get("run_key"),
        package_identity.get("run_key"),
        package_payload.get("run_key"),
        os.path.basename(_normalize_path(run_dir)),
    )


def _relpath_lower(base_dir: str, path: str) -> str:
    return os.path.relpath(_normalize_path(path), _normalize_path(base_dir)).replace("/", os.sep).lower()


def _is_standard_disp_path(run_dir: str, path: str) -> bool:
    disp_paths = get_run_disp_asset_paths(run_dir)
    normalized_path = _normalize_path(path)
    return normalized_path in {
        _normalize_path(disp_paths["primary"]),
        _normalize_path(disp_paths["hdr"]),
        _normalize_path(disp_paths["sml"]),
    }


def _looks_like_legacy_primary(path: str) -> bool:
    return bool(_LEGACY_DISP_RE.match(os.path.basename(str(path or "").strip())))


def _looks_like_envi_primary(run_dir: str, path: str) -> bool:
    normalized_path = _normalize_path(path)
    if not os.path.isfile(normalized_path):
        return False
    if _is_standard_disp_path(run_dir, normalized_path):
        return True
    return _looks_like_legacy_primary(normalized_path)


def _build_source_files(primary_file: str, source_files: Optional[List[str]] = None) -> List[str]:
    normalized_primary = _normalize_path(primary_file)
    candidates: List[str] = [normalized_primary]
    for raw_path in source_files or []:
        normalized = _normalize_path(raw_path)
        if normalized and normalized not in candidates and os.path.isfile(normalized):
            candidates.append(normalized)

    for ext in (".hdr", ".sml"):
        sidecar = normalized_primary + ext
        if os.path.isfile(sidecar) and sidecar not in candidates:
            candidates.append(sidecar)
    return candidates


def _find_standardized_run_files(run_dir: str) -> Optional[Dict[str, Any]]:
    disp_paths = get_run_disp_asset_paths(run_dir)
    primary_file = disp_paths["primary"]
    if not os.path.isfile(primary_file):
        return None
    return {
        "primary_file": _normalize_path(primary_file),
        "source_files": _build_source_files(primary_file),
        "discovery": "standard_asset",
    }


def _iter_metadata_candidates(run_dir: str, payloads: Dict[str, Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    execution_payload = payloads.get("execution") or {}
    if execution_payload:
        primary_file = execution_payload.get("primary_file")
        source_files = execution_payload.get("source_files") if isinstance(execution_payload.get("source_files"), list) else None
        if primary_file:
            yield {
                "primary_file": primary_file,
                "source_files": source_files,
                "discovery": "execution_manifest",
            }

    package_payload = payloads.get("package") or {}
    source_payload = package_payload.get("source") if isinstance(package_payload.get("source"), dict) else {}
    if source_payload:
        primary_file = source_payload.get("primary_path")
        if primary_file:
            yield {
                "primary_file": primary_file,
                "source_files": None,
                "discovery": "package_manifest",
            }


def _find_latest_legacy_primary(search_dir: str) -> Optional[str]:
    normalized_search_dir = _normalize_path(search_dir)
    if not os.path.isdir(normalized_search_dir):
        return None
    candidates: List[str] = []
    try:
        with os.scandir(normalized_search_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False) and _looks_like_legacy_primary(entry.name):
                        candidates.append(entry.path)
                except OSError:
                    continue
    except OSError:
        return None

    if not candidates:
        return None
    candidates.sort(key=lambda item: os.path.getmtime(item), reverse=True)
    return _normalize_path(candidates[0])


def _discover_envi_run_files(run_dir: str, payloads: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    standardized = _find_standardized_run_files(run_dir)
    if standardized:
        return standardized

    for candidate in _iter_metadata_candidates(run_dir, payloads):
        primary_file = _normalize_path(str(candidate.get("primary_file") or "").strip())
        if not _looks_like_envi_primary(run_dir, primary_file):
            continue
        return {
            "primary_file": primary_file,
            "source_files": _build_source_files(primary_file, candidate.get("source_files")),
            "discovery": candidate["discovery"],
        }

    search_dirs = [
        _normalize_path(run_dir),
        get_run_native_output_dir(run_dir),
    ]
    for search_dir in search_dirs:
        primary_file = _find_latest_legacy_primary(search_dir)
        if primary_file:
            return {
                "primary_file": primary_file,
                "source_files": _build_source_files(primary_file),
                "discovery": "filesystem_scan",
            }
    return None


def validate_envi_run_layout(run_dir: str) -> Dict[str, Any]:
    normalized_run_dir = _normalize_path(run_dir)
    native_output_dir = get_run_native_output_dir(normalized_run_dir)
    disp_paths = get_run_disp_asset_paths(normalized_run_dir)
    issues: List[str] = []

    if not os.path.isdir(normalized_run_dir):
        issues.append("run_dir_missing")
    if not os.path.isdir(native_output_dir):
        issues.append("native_dir_missing")
    if not os.path.isfile(disp_paths["primary"]):
        issues.append("standard_disp_missing")

    execution_payload = _load_json(os.path.join(normalized_run_dir, EXECUTION_MANIFEST_FILENAME)) or {}
    if execution_payload:
        if _normalize_path(str(execution_payload.get("primary_file") or "")) != _normalize_path(disp_paths["primary"]):
            issues.append("execution_manifest_primary_mismatch")
        if _normalize_path(str(execution_payload.get("native_output_dir") or "")) != _normalize_path(native_output_dir):
            issues.append("execution_manifest_native_mismatch")

    package_payload = _load_json(os.path.join(normalized_run_dir, PACKAGE_MANIFEST_FILENAME)) or {}
    if package_payload:
        source_payload = package_payload.get("source") if isinstance(package_payload.get("source"), dict) else {}
        if _normalize_path(str(source_payload.get("primary_path") or "")) != _normalize_path(disp_paths["primary"]):
            issues.append("package_manifest_primary_mismatch")
        if _normalize_path(str(source_payload.get("native_output_dir") or "")) != _normalize_path(native_output_dir):
            issues.append("package_manifest_native_mismatch")
        assets_payload = package_payload.get("assets") if isinstance(package_payload.get("assets"), list) else []
        disp_asset = next(
            (item for item in assets_payload if str((item or {}).get("role") or "").strip() == "disp"),
            None,
        )
        expected_relative = _relpath_lower(normalized_run_dir, disp_paths["primary"])
        actual_relative = _relpath_lower(
            normalized_run_dir,
            os.path.join(normalized_run_dir, str((disp_asset or {}).get("relative_path") or "")),
        ) if disp_asset else ""
        if not disp_asset or actual_relative != expected_relative:
            issues.append("package_manifest_disp_asset_mismatch")

    run_key = _resolve_run_key(normalized_run_dir, _read_run_payloads(normalized_run_dir))
    current_dir = os.path.join(os.path.dirname(os.path.dirname(normalized_run_dir)), "current")
    matched_pointer_count = 0
    if run_key and os.path.isdir(current_dir):
        for name in os.listdir(current_dir):
            if not name.lower().endswith(".json"):
                continue
            pointer_payload = _load_json(os.path.join(current_dir, name))
            if not pointer_payload:
                continue
            if str(pointer_payload.get("run_key") or "").strip() != run_key:
                continue
            matched_pointer_count += 1
            if _normalize_path(str(pointer_payload.get("primary_file") or "")) != _normalize_path(disp_paths["primary"]):
                issues.append(f"current_pointer_primary_mismatch:{name}")
            if _normalize_path(str(pointer_payload.get("native_output_dir") or "")) != _normalize_path(native_output_dir):
                issues.append(f"current_pointer_native_mismatch:{name}")
    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "matched_current_pointer_count": matched_pointer_count,
    }


def inspect_envi_run_layout(run_dir: str) -> Dict[str, Any]:
    normalized_run_dir = _normalize_path(run_dir)
    payloads = _read_run_payloads(normalized_run_dir)
    engine_code = _resolve_engine_code(payloads)
    files = _discover_envi_run_files(normalized_run_dir, payloads)
    disp_paths = get_run_disp_asset_paths(normalized_run_dir)
    standardized = bool(os.path.isfile(disp_paths["primary"]))
    native_exists = bool(os.path.isdir(get_run_native_output_dir(normalized_run_dir)))

    is_envi_run = bool(files) or engine_code in _ENVI_ENGINE_CODES
    layout_state = "unknown"
    if not is_envi_run:
        layout_state = "not_envi"
    elif standardized and native_exists:
        layout_state = "normalized"
    elif files and _looks_like_legacy_primary(files["primary_file"]):
        layout_state = "legacy"
    elif standardized:
        layout_state = "partial_normalized"

    validation = validate_envi_run_layout(normalized_run_dir) if is_envi_run else {"ok": True, "issues": [], "matched_current_pointer_count": 0}
    needs_migration = bool(
        is_envi_run
        and (
            layout_state in {"legacy", "partial_normalized"}
            or (layout_state == "normalized" and not validation["ok"])
        )
    )
    return {
        "run_dir": normalized_run_dir,
        "pair_key": _resolve_pair_key(normalized_run_dir, payloads),
        "run_key": _resolve_run_key(normalized_run_dir, payloads),
        "engine_code": engine_code,
        "is_envi_run": is_envi_run,
        "layout_state": layout_state,
        "needs_migration": needs_migration,
        "primary_file": (files or {}).get("primary_file"),
        "source_files": (files or {}).get("source_files") or [],
        "discovery": (files or {}).get("discovery"),
        "standard_disp_exists": standardized,
        "native_dir_exists": native_exists,
        "validation": validation,
    }


class DinsarLayoutMigrationService:
    def inspect_managed_envi_runs(
        self,
        *,
        root_dir: Optional[str] = None,
        pair_keys: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_root = _normalize_path(root_dir or settings.DINSAR_PRODUCT_DIR)
        filter_pair_keys = {
            str(item or "").strip()
            for item in pair_keys or []
            if str(item or "").strip()
        }

        details: List[Dict[str, Any]] = []
        inspected = 0
        envi_run_count = 0
        legacy_count = 0
        normalized_count = 0
        pending_count = 0

        for run_dir in _iter_managed_run_dirs(normalized_root):
            inspection = inspect_envi_run_layout(run_dir)
            if filter_pair_keys and inspection.get("pair_key") not in filter_pair_keys:
                continue
            inspected += 1
            if inspection["is_envi_run"]:
                envi_run_count += 1
                if inspection["layout_state"] == "legacy":
                    legacy_count += 1
                if inspection["layout_state"] == "normalized":
                    normalized_count += 1
                if inspection["needs_migration"]:
                    pending_count += 1
            details.append(inspection)
            if limit is not None and len(details) >= int(limit):
                break

        return {
            "root_dir": normalized_root,
            "inspected_run_count": inspected,
            "envi_run_count": envi_run_count,
            "legacy_run_count": legacy_count,
            "normalized_run_count": normalized_count,
            "pending_migration_count": pending_count,
            "details": details,
        }

    def migrate_managed_envi_runs(
        self,
        *,
        root_dir: Optional[str] = None,
        pair_keys: Optional[List[str]] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        inspection = self.inspect_managed_envi_runs(
            root_dir=root_dir,
            pair_keys=pair_keys,
            limit=limit,
        )
        details: List[Dict[str, Any]] = []
        migrated_count = 0
        rewritten_count = 0
        skipped_count = 0
        failed_count = 0

        for item in inspection["details"]:
            if not item["is_envi_run"]:
                skipped_count += 1
                details.append(
                    {
                        **item,
                        "status": "skipped_not_envi",
                    }
                )
                continue

            if not item["primary_file"]:
                failed_count += 1
                details.append(
                    {
                        **item,
                        "status": "failed_no_primary",
                        "error": "Unable to resolve ENVI primary displacement file.",
                    }
                )
                continue

            if not item["needs_migration"]:
                skipped_count += 1
                details.append(
                    {
                        **item,
                        "status": "skipped_already_normalized",
                    }
                )
                continue

            if dry_run:
                details.append(
                    {
                        **item,
                        "status": "would_migrate",
                    }
                )
                continue

            try:
                result = normalize_envi_run_layout(
                    item["run_dir"],
                    primary_file=item["primary_file"],
                    source_files=item["source_files"],
                    rewrite_metadata=True,
                )
                validation = validate_envi_run_layout(item["run_dir"])
                status = "migrated"
                if not result["promoted_files"] and not result["moved_entries"]:
                    status = "rewritten"
                if validation["ok"]:
                    if status == "rewritten":
                        rewritten_count += 1
                    else:
                        migrated_count += 1
                else:
                    status = "failed_validation"
                    failed_count += 1
                details.append(
                    {
                        **item,
                        **result,
                        "status": status,
                        "post_validation": validation,
                    }
                )
            except Exception as exc:
                failed_count += 1
                details.append(
                    {
                        **item,
                        "status": "failed",
                        "error": str(exc),
                    }
                )

        return {
            "root_dir": inspection["root_dir"],
            "dry_run": bool(dry_run),
            "inspected_run_count": inspection["inspected_run_count"],
            "envi_run_count": inspection["envi_run_count"],
            "pending_migration_count": inspection["pending_migration_count"],
            "migrated_count": migrated_count,
            "rewritten_count": rewritten_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "details": details,
        }


dinsar_layout_migration_service = DinsarLayoutMigrationService()
