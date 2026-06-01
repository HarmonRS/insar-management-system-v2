"""Inventory GF3 SARscape native geocoded outputs.

The production server writes ENVI/SARscape native ``*_geo`` datasets.  This
service treats those files as the source-of-truth evidence layer and produces a
small manifest that later conversion jobs can consume.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..utils import parse_gf3_l2_dirname

NATIVE_MANIFEST_NAME = "gf3_native_manifest.json"
NATIVE_MANIFEST_SCHEMA = "gf3_sarscape_native.v1"
POLARIZATION_PRIORITY = ("HH", "VV", "HV", "VH")
SKIP_DIR_NAMES = {
    ".git",
    ".gf3_extract",
    ".sarmap",
    "__pycache__",
    "temp",
    "tmp",
    "work",
    "sarscape_work",
    "GTOPO30_DIR",
    "SRTM_DEM_DIR",
    "TANDEMX_DEM_DIR",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_stat(path: Path) -> dict[str, Any] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "path": str(path),
        "size": int(stat.st_size),
        "mtime": float(stat.st_mtime),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp_path, path)


def _extract_date_from_text(value: str) -> str | None:
    match = re.search(r"(20\d{6})", value or "")
    return match.group(1) if match else None


def _extract_product_unique_id(value: str) -> str | None:
    match = re.search(r"(L\d{8,})", value or "", flags=re.IGNORECASE)
    return match.group(1).upper() if match else None


def _polarization_from_geo_name(name: str) -> str | None:
    upper_name = name.upper()
    match = re.search(r"(?:^|[_-])(HH|HV|VH|VV)[_-]GEO$", upper_name)
    if match:
        return match.group(1)
    tokens = [token for token in re.split(r"[_\-.]+", upper_name) if token]
    for token in reversed(tokens):
        if token in POLARIZATION_PRIORITY:
            return token
    return None


def _is_geo_native_data_file(path: Path) -> bool:
    return path.is_file() and path.name.lower().endswith("_geo")


def _scene_batch_name(root: Path, scene_dir: Path, scene_name: str) -> str | None:
    try:
        rel_parts = scene_dir.relative_to(root).parts
    except ValueError:
        rel_parts = ()
    if len(rel_parts) >= 2:
        return rel_parts[0]
    date = _extract_date_from_text(scene_name)
    return date


def _parse_scene_metadata(scene_name: str, assets: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = parse_gf3_l2_dirname(scene_name) or {}
    metadata: dict[str, Any] = {
        "satellite": "GF3",
        "satellite_family": "GF3",
        **parsed,
    }
    if not metadata.get("imaging_date"):
        metadata["imaging_date"] = _extract_date_from_text(scene_name)
    if not metadata.get("product_unique_id"):
        metadata["product_unique_id"] = _extract_product_unique_id(scene_name)

    polarizations = [
        str(asset.get("polarization") or "").upper()
        for asset in assets
        if asset.get("polarization") and asset.get("polarization") != "UNKNOWN"
    ]
    if polarizations:
        metadata["polarization"] = ",".join(
            pol for pol in POLARIZATION_PRIORITY if pol in set(polarizations)
        ) or ",".join(sorted(set(polarizations)))
    metadata["product_level"] = "L2"
    metadata["source_format"] = "GF3_SARSCAPE_NATIVE"
    return metadata


def _collect_native_assets(scene_dir: Path) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    try:
        entries = sorted(scene_dir.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return assets

    for path in entries:
        if not _is_geo_native_data_file(path):
            continue

        base = path
        hdr = Path(str(base) + ".hdr")
        sml = Path(str(base) + ".sml")
        aux_xml = Path(str(base) + ".aux.xml")
        ovr = Path(str(base) + ".ovr")
        kml = Path(str(base) + ".kml")
        quicklook = base.with_name(base.name + "_ql.tif")
        polarization = _polarization_from_geo_name(base.name) or "UNKNOWN"
        complete = _is_nonempty_file(base) and _is_nonempty_file(hdr) and _is_nonempty_file(sml)

        asset: dict[str, Any] = {
            "polarization": polarization,
            "role": "geo_native",
            "path": str(base),
            "hdr": str(hdr) if hdr.exists() else None,
            "sml": str(sml) if sml.exists() else None,
            "aux_xml": str(aux_xml) if aux_xml.exists() else None,
            "ovr": str(ovr) if ovr.exists() else None,
            "quicklook": str(quicklook) if quicklook.exists() else None,
            "kml": str(kml) if kml.exists() else None,
            "complete": bool(complete),
            "source": _safe_stat(base),
            "hdr_info": _safe_stat(hdr) if hdr.exists() else None,
            "sml_info": _safe_stat(sml) if sml.exists() else None,
        }
        assets.append(asset)

    return assets


def _collect_scene_manifest(root: Path, scene_dir: Path) -> dict[str, Any] | None:
    assets = _collect_native_assets(scene_dir)
    if not assets:
        return None

    scene_name = scene_dir.name
    batch_name = _scene_batch_name(root, scene_dir, scene_name)
    complete_assets = [asset for asset in assets if asset.get("complete")]
    complete_pols = [
        pol
        for pol in POLARIZATION_PRIORITY
        if any(asset.get("complete") and asset.get("polarization") == pol for asset in assets)
    ]
    other_complete_pols = sorted(
        {
            str(asset.get("polarization") or "")
            for asset in complete_assets
            if asset.get("polarization") not in POLARIZATION_PRIORITY
        }
    )
    complete_pols.extend([pol for pol in other_complete_pols if pol])

    if complete_assets and len(complete_assets) == len(assets):
        status = "NATIVE_READY"
    elif complete_assets:
        status = "PARTIAL"
    else:
        status = "FAILED"

    logs = []
    for name in ("gf3_sarscape_cli.log",):
        log_path = scene_dir / name
        if log_path.is_file():
            logs.append(str(log_path))
    try:
        logs.extend(str(path) for path in sorted(scene_dir.glob("*.log"), key=lambda item: item.name.lower()) if str(path) not in logs)
    except OSError:
        pass

    metadata = _parse_scene_metadata(scene_name, assets)
    manifest_path = scene_dir / NATIVE_MANIFEST_NAME
    return {
        "schema": NATIVE_MANIFEST_SCHEMA,
        "generated_at": _utc_now(),
        "scene_name": scene_name,
        "batch_name": batch_name,
        "native_root": str(root),
        "native_dir": str(scene_dir),
        "manifest_path": str(manifest_path),
        "source_archive": None,
        "status": status,
        "polarizations": complete_pols,
        "metadata": metadata,
        "assets": assets,
        "logs": logs,
    }


def _normalize_roots(native_dirs: list[str] | tuple[str, ...] | None) -> tuple[list[Path], list[str]]:
    roots: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw in native_dirs or []:
        text = str(raw or "").strip()
        if not text:
            continue
        path = Path(os.path.normpath(text)).resolve()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            roots.append(path)
        else:
            missing.append(str(path))
    return roots, missing


def scan_gf3_sarscape_native_roots(
    native_dirs: list[str] | tuple[str, ...] | None,
    *,
    write_manifest: bool = True,
) -> dict[str, Any]:
    """Scan configured native roots and return discovered scene manifests."""
    roots, missing_roots = _normalize_roots(native_dirs)
    scenes: list[dict[str, Any]] = []
    seen_scene_dirs: set[str] = set()
    write_errors: list[dict[str, str]] = []

    for root in roots:
        for current_dir, dir_names, _file_names in os.walk(root):
            dir_names[:] = [
                name
                for name in dir_names
                if name not in SKIP_DIR_NAMES and not name.startswith(".SARscape")
            ]
            scene_dir = Path(current_dir)
            scene_key = str(scene_dir).lower()
            if scene_key in seen_scene_dirs:
                dir_names[:] = []
                continue

            manifest = _collect_scene_manifest(root, scene_dir)
            if not manifest:
                continue

            seen_scene_dirs.add(scene_key)
            if write_manifest:
                try:
                    _write_json(Path(manifest["manifest_path"]), manifest)
                except OSError as exc:
                    write_errors.append({"scene_dir": str(scene_dir), "error": str(exc)})
            scenes.append(manifest)
            dir_names[:] = []

    native_ready = sum(1 for scene in scenes if scene.get("status") == "NATIVE_READY")
    partial = sum(1 for scene in scenes if scene.get("status") == "PARTIAL")
    failed = sum(1 for scene in scenes if scene.get("status") == "FAILED")
    complete_assets = sum(
        1
        for scene in scenes
        for asset in scene.get("assets") or []
        if asset.get("complete")
    )
    return {
        "schema": "gf3_sarscape_native_inventory.v1",
        "generated_at": _utc_now(),
        "native_roots": [str(path) for path in roots],
        "missing_roots": missing_roots,
        "scene_count": len(scenes),
        "native_ready_count": native_ready,
        "partial_count": partial,
        "failed_count": failed,
        "complete_asset_count": complete_assets,
        "write_errors": write_errors,
        "scenes": scenes,
    }
