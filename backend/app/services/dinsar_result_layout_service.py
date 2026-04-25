from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional

from .dinsar_naming import RUN_META_FILENAME
from .product_package_schema import build_canonical_descriptor, normalize_package_manifest


RUN_NATIVE_DIRNAME = "native"
RUN_ASSETS_DIRNAME = "assets"
RUN_PREVIEW_DIRNAME = "preview"
RUN_CURRENT_DIRNAME = "current"
RUN_DISP_DIRNAME = "disp"
RUN_COH_DIRNAME = "coh"
EXECUTION_MANIFEST_FILENAME = "execution_manifest.json"
PACKAGE_MANIFEST_FILENAME = "manifest.json"
STANDARD_ENVI_DISP_BASENAME = "disp"
STANDARD_ISCE2_DISP_NAME = "disp.tif"
STANDARD_ISCE2_COH_NAME = "coh.tif"

_KEEP_RUN_ROOT_NAMES = {
    RUN_NATIVE_DIRNAME,
    RUN_ASSETS_DIRNAME,
    RUN_PREVIEW_DIRNAME,
    RUN_CURRENT_DIRNAME,
    RUN_META_FILENAME,
    EXECUTION_MANIFEST_FILENAME,
    PACKAGE_MANIFEST_FILENAME,
}
_DISP_ASSET_ROLES = {"disp", "disp_header", "disp_sidecar"}
_ISCE2_ASSET_ROLES = {"disp", "coh"}


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(str(path or "").strip()))


def _load_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _write_json(path: str, payload: Dict[str, Any]) -> str:
    target = _normalize_path(path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return target


def get_run_native_output_dir(run_dir: str) -> str:
    return os.path.join(_normalize_path(run_dir), RUN_NATIVE_DIRNAME)


def get_run_disp_asset_base(run_dir: str) -> str:
    return os.path.join(
        _normalize_path(run_dir),
        RUN_ASSETS_DIRNAME,
        RUN_DISP_DIRNAME,
        STANDARD_ENVI_DISP_BASENAME,
    )


def get_run_disp_asset_paths(run_dir: str) -> Dict[str, str]:
    base = get_run_disp_asset_base(run_dir)
    return {
        "primary": base,
        "hdr": base + ".hdr",
        "sml": base + ".sml",
    }


def get_run_isce2_disp_asset_path(run_dir: str) -> str:
    return os.path.join(
        _normalize_path(run_dir),
        RUN_ASSETS_DIRNAME,
        RUN_DISP_DIRNAME,
        STANDARD_ISCE2_DISP_NAME,
    )


def get_run_isce2_coh_asset_path(run_dir: str) -> str:
    return os.path.join(
        _normalize_path(run_dir),
        RUN_ASSETS_DIRNAME,
        RUN_COH_DIRNAME,
        STANDARD_ISCE2_COH_NAME,
    )


def is_standard_envi_disp_file(run_root: str, file_path: str) -> bool:
    run_root = _normalize_path(run_root)
    file_path = _normalize_path(file_path)
    rel_parts = [
        part.lower()
        for part in os.path.relpath(file_path, run_root).replace("/", os.sep).split(os.sep)
        if part
    ]
    if len(rel_parts) < 3:
        return False
    tail = rel_parts[-3:]
    return tuple(tail) in {
        ("assets", "disp", "disp"),
        ("assets", "disp", "disp.hdr"),
        ("assets", "disp", "disp.sml"),
    }


def is_standard_isce2_disp_file(run_root: str, file_path: str) -> bool:
    run_root = _normalize_path(run_root)
    file_path = _normalize_path(file_path)
    rel_parts = [
        part.lower()
        for part in os.path.relpath(file_path, run_root).replace("/", os.sep).split(os.sep)
        if part
    ]
    if len(rel_parts) < 3:
        return False
    tail = rel_parts[-3:]
    return tuple(tail) in {
        ("assets", "disp", "disp.tif"),
        ("assets", "disp", "disp.tiff"),
    }


def is_path_within_native_dir(run_root: str, path: str) -> bool:
    run_root = _normalize_path(run_root)
    path = _normalize_path(path)
    try:
        rel_path = os.path.relpath(path, run_root)
    except ValueError:
        return False
    parts = [part.lower() for part in rel_path.split(os.sep) if part]
    return RUN_NATIVE_DIRNAME in parts


def _move_file(src: str, dst: str) -> bool:
    src_path = _normalize_path(src)
    dst_path = _normalize_path(dst)
    if src_path == dst_path or not os.path.isfile(src_path):
        return False
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(dst_path):
        raise FileExistsError(f"Target already exists: {dst_path}")
    os.replace(src_path, dst_path)
    return True


def _move_entry(src: str, dst: str) -> bool:
    src_path = _normalize_path(src)
    dst_path = _normalize_path(dst)
    if src_path == dst_path or not os.path.exists(src_path):
        return False
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    if os.path.exists(dst_path):
        raise FileExistsError(f"Target already exists: {dst_path}")
    os.replace(src_path, dst_path)
    return True


def _copy_file(src: str, dst: str) -> bool:
    src_path = _normalize_path(src)
    dst_path = _normalize_path(dst)
    if src_path == dst_path or not os.path.isfile(src_path):
        return False
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    shutil.copy2(src_path, dst_path)
    return True


def _rewrite_run_metadata(run_dir: str, native_output_dir: str) -> None:
    meta_path = os.path.join(_normalize_path(run_dir), RUN_META_FILENAME)
    payload = _load_json(meta_path)
    if not payload:
        return
    payload["output_dir"] = _normalize_path(run_dir)
    payload["native_output_dir"] = _normalize_path(native_output_dir)
    _write_json(meta_path, payload)


def _rewrite_execution_manifest(
    run_dir: str,
    *,
    native_output_dir: str,
    primary_file: str,
    source_files: List[str],
) -> None:
    manifest_path = os.path.join(_normalize_path(run_dir), EXECUTION_MANIFEST_FILENAME)
    payload = _load_json(manifest_path)
    if not payload:
        return
    payload["output_dir"] = _normalize_path(run_dir)
    payload["native_output_dir"] = _normalize_path(native_output_dir)
    payload["primary_file"] = _normalize_path(primary_file)
    payload["source_files"] = [_normalize_path(path) for path in source_files]
    _write_json(manifest_path, payload)


def _rewrite_current_pointers(
    run_dir: str,
    *,
    native_output_dir: str,
    primary_file: str,
    source_files: List[str],
) -> None:
    normalized_run_dir = _normalize_path(run_dir)
    runs_dir = os.path.dirname(normalized_run_dir)
    if os.path.basename(runs_dir).lower() != "runs":
        return
    pair_root = os.path.dirname(runs_dir)
    current_dir = os.path.join(pair_root, RUN_CURRENT_DIRNAME)
    if not os.path.isdir(current_dir):
        return

    execution_manifest_path = os.path.join(normalized_run_dir, EXECUTION_MANIFEST_FILENAME)
    execution_manifest = _load_json(execution_manifest_path) or {}
    run_key = str(execution_manifest.get("run_key") or "").strip()
    if not run_key:
        return

    for name in os.listdir(current_dir):
        if not name.lower().endswith(".json"):
            continue
        pointer_path = os.path.join(current_dir, name)
        payload = _load_json(pointer_path)
        if not payload:
            continue
        if str(payload.get("run_key") or "").strip() != run_key:
            continue
        payload["output_dir"] = normalized_run_dir
        payload["native_output_dir"] = _normalize_path(native_output_dir)
        payload["manifest_path"] = execution_manifest_path
        payload["primary_file"] = _normalize_path(primary_file)
        payload["source_files"] = [_normalize_path(path) for path in source_files]
        _write_json(pointer_path, payload)


def _rewrite_package_manifest(
    run_dir: str,
    *,
    native_output_dir: str,
    primary_file: str,
    source_files: List[str],
) -> None:
    manifest_path = os.path.join(_normalize_path(run_dir), PACKAGE_MANIFEST_FILENAME)
    payload = _load_json(manifest_path)
    if not payload:
        return

    normalized_run_dir = _normalize_path(run_dir)
    normalized_primary = _normalize_path(primary_file)
    normalized_sources = [_normalize_path(path) for path in source_files]
    primary_relative = os.path.relpath(normalized_primary, normalized_run_dir)

    source_payload = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_payload["output_dir"] = normalized_run_dir
    source_payload["native_output_dir"] = _normalize_path(native_output_dir)
    source_payload["primary_path"] = normalized_primary
    source_payload["source_dir"] = normalized_run_dir
    source_payload["publish_dir"] = normalized_run_dir
    payload["source"] = source_payload

    summary_payload = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    summary_payload["primary_asset_relative"] = primary_relative
    payload["summary"] = summary_payload

    preview_relative = str(summary_payload.get("preview_relative") or "").strip()

    existing_assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    preserved_assets = [
        item
        for item in existing_assets
        if str((item or {}).get("role") or "").strip() not in _DISP_ASSET_ROLES
    ]
    disp_assets: List[Dict[str, Any]] = [
        {
            "role": "disp",
            "asset_name": os.path.basename(normalized_primary) or STANDARD_ENVI_DISP_BASENAME,
            "relative_path": primary_relative,
            "format": "envi",
            "media_type": "application/octet-stream",
            "is_required": True,
            "is_primary": True,
        }
    ]
    if len(normalized_sources) > 1 and os.path.isfile(normalized_sources[1]):
        disp_assets.append(
            {
                "role": "disp_header",
                "asset_name": os.path.basename(normalized_sources[1]),
                "relative_path": os.path.relpath(normalized_sources[1], normalized_run_dir),
                "format": "hdr",
                "media_type": "text/plain",
                "is_required": True,
                "is_primary": False,
            }
        )
    if len(normalized_sources) > 2 and os.path.isfile(normalized_sources[2]):
        disp_assets.append(
            {
                "role": "disp_sidecar",
                "asset_name": os.path.basename(normalized_sources[2]),
                "relative_path": os.path.relpath(normalized_sources[2], normalized_run_dir),
                "format": "sml",
                "media_type": "text/plain",
                "is_required": False,
                "is_primary": False,
            }
        )

    payload["assets"] = disp_assets + preserved_assets
    payload["native_output_dir"] = _normalize_path(native_output_dir)

    canonical = build_canonical_descriptor(
        payload["assets"],
        product_family=str(payload.get("product_family") or "dinsar"),
    )
    if preview_relative:
        canonical["preview_asset_relative"] = preview_relative
    payload["canonical"] = canonical
    normalized_payload = normalize_package_manifest(payload)
    _write_json(manifest_path, normalized_payload)


def _rewrite_isce2_package_manifest(
    run_dir: str,
    *,
    native_output_dir: str,
    primary_file: str,
    source_files: List[str],
) -> None:
    manifest_path = os.path.join(_normalize_path(run_dir), PACKAGE_MANIFEST_FILENAME)
    payload = _load_json(manifest_path)
    if not payload:
        return

    normalized_run_dir = _normalize_path(run_dir)
    normalized_primary = _normalize_path(primary_file)
    normalized_sources = [_normalize_path(path) for path in source_files]
    primary_relative = os.path.relpath(normalized_primary, normalized_run_dir)

    source_payload = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_payload["output_dir"] = normalized_run_dir
    source_payload["native_output_dir"] = _normalize_path(native_output_dir)
    source_payload["primary_path"] = normalized_primary
    source_payload["source_dir"] = normalized_run_dir
    source_payload["publish_dir"] = normalized_run_dir
    payload["source"] = source_payload

    summary_payload = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    summary_payload["primary_asset_relative"] = primary_relative
    payload["summary"] = summary_payload

    preview_relative = str(summary_payload.get("preview_relative") or "").strip()

    existing_assets = payload.get("assets") if isinstance(payload.get("assets"), list) else []
    preserved_assets = [
        item
        for item in existing_assets
        if str((item or {}).get("role") or "").strip() not in _ISCE2_ASSET_ROLES
    ]
    isce2_assets: List[Dict[str, Any]] = [
        {
            "role": "disp",
            "asset_name": os.path.basename(normalized_primary) or STANDARD_ISCE2_DISP_NAME,
            "relative_path": primary_relative,
            "format": "geotiff",
            "media_type": "image/tiff",
            "is_required": True,
            "is_primary": True,
        }
    ]
    if len(normalized_sources) > 1 and os.path.isfile(normalized_sources[1]):
        isce2_assets.append(
            {
                "role": "coh",
                "asset_name": os.path.basename(normalized_sources[1]) or STANDARD_ISCE2_COH_NAME,
                "relative_path": os.path.relpath(normalized_sources[1], normalized_run_dir),
                "format": "geotiff",
                "media_type": "image/tiff",
                "is_required": False,
                "is_primary": False,
            }
        )

    payload["assets"] = isce2_assets + preserved_assets
    payload["native_output_dir"] = _normalize_path(native_output_dir)

    canonical = build_canonical_descriptor(
        payload["assets"],
        product_family=str(payload.get("product_family") or "dinsar"),
    )
    if preview_relative:
        canonical["preview_asset_relative"] = preview_relative
    payload["canonical"] = canonical
    normalized_payload = normalize_package_manifest(payload)
    _write_json(manifest_path, normalized_payload)


def normalize_envi_run_layout(
    run_dir: str,
    *,
    primary_file: str,
    source_files: List[str],
    rewrite_metadata: bool = True,
) -> Dict[str, Any]:
    normalized_run_dir = _normalize_path(run_dir)
    if not os.path.isdir(normalized_run_dir):
        raise FileNotFoundError(f"Run directory not found: {normalized_run_dir}")

    native_output_dir = get_run_native_output_dir(normalized_run_dir)
    disp_paths = get_run_disp_asset_paths(normalized_run_dir)
    normalized_primary = _normalize_path(primary_file)
    normalized_sources = [_normalize_path(path) for path in source_files if str(path or "").strip()]
    if normalized_primary and normalized_primary not in normalized_sources:
        normalized_sources.insert(0, normalized_primary)

    promoted_files: List[str] = []
    if os.path.isfile(normalized_primary) and normalized_primary != disp_paths["primary"]:
        if _move_file(normalized_primary, disp_paths["primary"]):
            promoted_files.append(disp_paths["primary"])

    for path in normalized_sources:
        lower = path.lower()
        if lower.endswith(".hdr"):
            if _move_file(path, disp_paths["hdr"]):
                promoted_files.append(disp_paths["hdr"])
        elif lower.endswith(".sml"):
            if _move_file(path, disp_paths["sml"]):
                promoted_files.append(disp_paths["sml"])

    os.makedirs(native_output_dir, exist_ok=True)
    moved_entries: List[str] = []
    for name in os.listdir(normalized_run_dir):
        if name in _KEEP_RUN_ROOT_NAMES:
            continue
        src_path = os.path.join(normalized_run_dir, name)
        dst_path = os.path.join(native_output_dir, name)
        if _move_entry(src_path, dst_path):
            moved_entries.append(dst_path)

    final_sources = [disp_paths["primary"]]
    if os.path.isfile(disp_paths["hdr"]):
        final_sources.append(disp_paths["hdr"])
    if os.path.isfile(disp_paths["sml"]):
        final_sources.append(disp_paths["sml"])

    if rewrite_metadata:
        _rewrite_run_metadata(normalized_run_dir, native_output_dir)
        _rewrite_execution_manifest(
            normalized_run_dir,
            native_output_dir=native_output_dir,
            primary_file=disp_paths["primary"],
            source_files=final_sources,
        )
        _rewrite_current_pointers(
            normalized_run_dir,
            native_output_dir=native_output_dir,
            primary_file=disp_paths["primary"],
            source_files=final_sources,
        )
        _rewrite_package_manifest(
            normalized_run_dir,
            native_output_dir=native_output_dir,
            primary_file=disp_paths["primary"],
            source_files=final_sources,
        )

    return {
        "run_dir": normalized_run_dir,
        "native_output_dir": native_output_dir,
        "primary_file": disp_paths["primary"],
        "source_files": final_sources,
        "promoted_files": promoted_files,
        "moved_entries": moved_entries,
    }


def normalize_isce2_run_layout(
    run_dir: str,
    *,
    primary_file: str,
    source_files: List[str],
    rewrite_metadata: bool = True,
) -> Dict[str, Any]:
    normalized_run_dir = _normalize_path(run_dir)
    if not os.path.isdir(normalized_run_dir):
        raise FileNotFoundError(f"Run directory not found: {normalized_run_dir}")

    native_output_dir = get_run_native_output_dir(normalized_run_dir)
    disp_asset_path = get_run_isce2_disp_asset_path(normalized_run_dir)
    coh_asset_path = get_run_isce2_coh_asset_path(normalized_run_dir)
    normalized_primary = _normalize_path(primary_file)
    normalized_sources = [_normalize_path(path) for path in source_files if str(path or "").strip()]
    if normalized_primary and normalized_primary not in normalized_sources:
        normalized_sources.insert(0, normalized_primary)

    copied_files: List[str] = []
    if os.path.isfile(normalized_primary) and _copy_file(normalized_primary, disp_asset_path):
        copied_files.append(disp_asset_path)

    coh_source = ""
    for path in normalized_sources[1:]:
        if os.path.isfile(path):
            coh_source = path
            break
    if coh_source and _copy_file(coh_source, coh_asset_path):
        copied_files.append(coh_asset_path)

    if not os.path.isfile(disp_asset_path):
        raise FileNotFoundError(f"ISCE2 displacement asset not found: {disp_asset_path}")

    os.makedirs(native_output_dir, exist_ok=True)
    moved_entries: List[str] = []
    for name in os.listdir(normalized_run_dir):
        if name in _KEEP_RUN_ROOT_NAMES:
            continue
        src_path = os.path.join(normalized_run_dir, name)
        dst_path = os.path.join(native_output_dir, name)
        if _move_entry(src_path, dst_path):
            moved_entries.append(dst_path)

    final_sources = [disp_asset_path]
    if os.path.isfile(coh_asset_path):
        final_sources.append(coh_asset_path)

    if rewrite_metadata:
        _rewrite_run_metadata(normalized_run_dir, native_output_dir)
        _rewrite_execution_manifest(
            normalized_run_dir,
            native_output_dir=native_output_dir,
            primary_file=disp_asset_path,
            source_files=final_sources,
        )
        _rewrite_current_pointers(
            normalized_run_dir,
            native_output_dir=native_output_dir,
            primary_file=disp_asset_path,
            source_files=final_sources,
        )
        _rewrite_isce2_package_manifest(
            normalized_run_dir,
            native_output_dir=native_output_dir,
            primary_file=disp_asset_path,
            source_files=final_sources,
        )

    return {
        "run_dir": normalized_run_dir,
        "native_output_dir": native_output_dir,
        "primary_file": disp_asset_path,
        "source_files": final_sources,
        "copied_files": copied_files,
        "moved_entries": moved_entries,
    }
