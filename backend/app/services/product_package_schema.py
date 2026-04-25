from __future__ import annotations

import copy
import os
from typing import Any, Dict, Iterable, List, Optional


CANONICAL_PACKAGE_SCHEMA = "insar.product-package/v1"
CANONICAL_PACKAGE_LAYOUT = "canonical.v1"
LEGACY_DINSAR_PACKAGE_SCHEMA = "dinsar-product/v1"
LEGACY_TIMESERIES_PACKAGE_SCHEMA = "psinsar.publish.v1"

TIMESERIES_ARTIFACT_ROLE_MAP = {
    "timeseries_cube": "timeseries_cube",
    "velocity_map": "velocity_map",
    "velocity_geotiff": "velocity_geotiff",
    "temporal_coherence": "temporal_coherence",
    "temporal_coherence_geotiff": "temporal_coherence_geotiff",
    "quality_mask": "quality_mask",
    "quality_mask_geotiff": "quality_mask_geotiff",
    "preview_png": "preview_png",
    "diagnostic_png": "diagnostic_png",
}

_DINSAR_PRIMARY_ROLES = {"disp"}
_DINSAR_PREVIEW_ROLES = {"thumb"}
_TIMESERIES_PRIMARY_ROLES = {"velocity_geotiff", "timeseries_cube", "velocity_map"}
_TIMESERIES_PREVIEW_ROLES = {"preview_png"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _copy_dict(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return copy.deepcopy(payload) if isinstance(payload, dict) else {}


def _copy_list(payload: Optional[Iterable[Any]]) -> List[Any]:
    return copy.deepcopy(list(payload or []))


def infer_asset_format(path: str) -> Optional[str]:
    ext = os.path.splitext(str(path or "").lower())[1]
    return {
        ".h5": "hdf5",
        ".hdr": "hdr",
        ".json": "json",
        ".png": "png",
        ".sml": "sml",
        ".tif": "geotiff",
        ".tiff": "geotiff",
        ".webp": "webp",
        ".xml": "xml",
        "": None,
    }.get(ext, ext.lstrip(".") or None)


def infer_asset_media_type(path: str) -> Optional[str]:
    ext = os.path.splitext(str(path or "").lower())[1]
    return {
        ".h5": "application/x-hdf5",
        ".hdr": "text/plain",
        ".json": "application/json",
        ".png": "image/png",
        ".sml": "text/plain",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
        ".xml": "application/xml",
    }.get(ext)


def build_asset_entry(
    *,
    role: str,
    relative_path: str,
    asset_name: Optional[str] = None,
    format: Optional[str] = None,
    media_type: Optional[str] = None,
    is_required: bool = False,
    is_primary: bool = False,
    origin_role: Optional[str] = None,
    native_path: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    relative = _text(relative_path)
    if not relative:
        raise ValueError("relative_path is required")
    payload: Dict[str, Any] = {
        "role": _text(role) or "asset",
        "asset_name": _text(asset_name) or os.path.basename(relative) or (_text(role) or "asset"),
        "relative_path": relative,
        "format": format or infer_asset_format(relative),
        "media_type": media_type or infer_asset_media_type(relative),
        "is_required": bool(is_required),
        "is_primary": bool(is_primary),
    }
    if _text(origin_role):
        payload["origin_role"] = _text(origin_role)
    if _text(native_path):
        payload["native_path"] = _text(native_path)
    for key, value in (extra or {}).items():
        if value is not None:
            payload[key] = value
    return payload


def canonicalize_timeseries_artifacts(artifacts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    assets: List[Dict[str, Any]] = []
    for artifact in artifacts or []:
        relative_path = _text((artifact or {}).get("path"))
        if not relative_path:
            continue
        product_type = _text((artifact or {}).get("product_type")) or "asset"
        asset_role = TIMESERIES_ARTIFACT_ROLE_MAP.get(product_type, product_type or "asset")
        assets.append(
            build_asset_entry(
                role=asset_role,
                relative_path=relative_path,
                asset_name=os.path.basename(relative_path),
                format=infer_asset_format(relative_path),
                media_type=infer_asset_media_type(relative_path),
                is_required=asset_role in _TIMESERIES_PRIMARY_ROLES or asset_role in _TIMESERIES_PREVIEW_ROLES,
                is_primary=asset_role in _TIMESERIES_PRIMARY_ROLES,
                origin_role=product_type,
            )
        )
    return assets


def _primary_roles_for_family(product_family: str) -> set[str]:
    family = _text(product_family).lower()
    if family == "timeseries":
        return set(_TIMESERIES_PRIMARY_ROLES)
    return set(_DINSAR_PRIMARY_ROLES)


def _preview_roles_for_family(product_family: str) -> set[str]:
    family = _text(product_family).lower()
    if family == "timeseries":
        return set(_TIMESERIES_PREVIEW_ROLES)
    return set(_DINSAR_PREVIEW_ROLES)


def build_canonical_descriptor(
    assets: Iterable[Dict[str, Any]],
    *,
    product_family: str,
) -> Dict[str, Any]:
    asset_items = list(assets or [])
    available_roles = [
        _text(asset.get("role"))
        for asset in asset_items
        if _text(asset.get("role"))
    ]
    primary_role = None
    preview_role = None
    preferred_primary = _primary_roles_for_family(product_family)
    preferred_preview = _preview_roles_for_family(product_family)

    for asset in asset_items:
        role = _text(asset.get("role"))
        if not primary_role and (bool(asset.get("is_primary")) or role in preferred_primary):
            primary_role = role
        if not preview_role and role in preferred_preview:
            preview_role = role

    primary_relative = None
    preview_relative = None
    for asset in asset_items:
        role = _text(asset.get("role"))
        if primary_relative is None and role == primary_role:
            primary_relative = _text(asset.get("relative_path"))
        if preview_relative is None and role == preview_role:
            preview_relative = _text(asset.get("relative_path"))

    return {
        "primary_asset_role": primary_role,
        "preview_asset_role": preview_role,
        "primary_asset_relative": primary_relative,
        "preview_asset_relative": preview_relative,
        "available_asset_roles": sorted({role for role in available_roles if role}),
    }


def _normalize_canonical_manifest(document: Dict[str, Any]) -> Dict[str, Any]:
    document["schema_version"] = CANONICAL_PACKAGE_SCHEMA
    document["package_layout"] = _text(document.get("package_layout")) or CANONICAL_PACKAGE_LAYOUT
    document["source_schema_version"] = (
        _text(document.get("source_schema_version")) or CANONICAL_PACKAGE_SCHEMA
    )

    identity = _copy_dict(document.get("identity"))
    if not _text(identity.get("pair_key")) and _text(document.get("pair_key")):
        identity["pair_key"] = _text(document.get("pair_key"))
    if not _text(identity.get("stack_key")) and _text(document.get("stack_key")):
        identity["stack_key"] = _text(document.get("stack_key"))
    if not _text(identity.get("run_key")):
        identity["run_key"] = _text(document.get("run_key")) or _text(document.get("run_id"))
    document["identity"] = identity
    document["pair_key"] = _text(identity.get("pair_key")) or None
    document["stack_key"] = _text(identity.get("stack_key")) or None
    document["run_key"] = _text(identity.get("run_key")) or None

    engine = _copy_dict(document.get("engine"))
    if not _text(engine.get("code")):
        engine["code"] = _text(document.get("engine_code")) or "unknown"
    if not _text(engine.get("version")) and _text(document.get("engine_version")):
        engine["version"] = _text(document.get("engine_version"))
    document["engine"] = engine

    processor = _copy_dict(document.get("processor"))
    if not _text(processor.get("code")):
        processor["code"] = _text(document.get("processor_code")) or _text(engine.get("code")) or "unknown"
    if not _text(processor.get("profile_code")) and _text(document.get("profile_code")):
        processor["profile_code"] = _text(document.get("profile_code"))
    document["processor"] = processor
    document["processor_code"] = _text(processor.get("code")) or None

    runtime = _copy_dict(document.get("runtime"))
    if not _text(runtime.get("runtime_id")) and _text(document.get("runtime_id")):
        runtime["runtime_id"] = _text(document.get("runtime_id"))
    document["runtime"] = runtime
    document["runtime_id"] = _text(runtime.get("runtime_id")) or None

    source = _copy_dict(document.get("source"))
    if not _text(source.get("primary_path")) and _text(document.get("source_primary_path")):
        source["primary_path"] = _text(document.get("source_primary_path"))
    if not _text(source.get("publish_dir")) and _text(document.get("publish_dir")):
        source["publish_dir"] = _text(document.get("publish_dir"))
    if not _text(source.get("native_output_dir")):
        source["native_output_dir"] = (
            _text(source.get("output_dir"))
            or _text(document.get("native_output_dir"))
            or None
        )
    document["source"] = source
    document["native_output_dir"] = _text(source.get("native_output_dir")) or None

    temporal = _copy_dict(document.get("temporal"))
    if not _text(temporal.get("reference_date")) and _text(document.get("reference_date")):
        temporal["reference_date"] = _text(document.get("reference_date"))
    if not temporal.get("stack_dates") and isinstance(document.get("stack_dates"), list):
        temporal["stack_dates"] = [str(item).strip() for item in document.get("stack_dates") or [] if str(item).strip()]
    if not _text(temporal.get("produced_at")) and _text(document.get("produced_at")):
        temporal["produced_at"] = _text(document.get("produced_at"))
    if not _text(temporal.get("published_at")) and _text(document.get("published_at")):
        temporal["published_at"] = _text(document.get("published_at"))
    document["temporal"] = temporal
    document["produced_at"] = _text(temporal.get("produced_at")) or None
    document["published_at"] = _text(temporal.get("published_at")) or None

    spatial = _copy_dict(document.get("spatial"))
    if not spatial and any(document.get(key) is not None for key in ("min_lon", "min_lat", "max_lon", "max_lat")):
        spatial = {
            "min_lon": document.get("min_lon"),
            "min_lat": document.get("min_lat"),
            "max_lon": document.get("max_lon"),
            "max_lat": document.get("max_lat"),
            "coverage_polygon": document.get("coverage_polygon"),
        }
    document["spatial"] = spatial

    assets = _copy_list(document.get("assets"))
    if not assets and isinstance(document.get("artifacts"), list):
        assets = canonicalize_timeseries_artifacts(document.get("artifacts") or [])
    document["assets"] = assets

    product_family = _text(document.get("product_family")) or "dinsar"
    canonical = _copy_dict(document.get("canonical"))
    defaults = build_canonical_descriptor(assets, product_family=product_family)
    for key, value in defaults.items():
        if canonical.get(key) in (None, "", []):
            canonical[key] = value
    document["canonical"] = canonical
    document["package_schema"] = CANONICAL_PACKAGE_SCHEMA

    if not _text(document.get("catalog_name")):
        document["catalog_name"] = "dinsar" if product_family == "dinsar" else "psinsar"
    if not _text(document.get("product_type")):
        document["product_type"] = "dinsar_interferogram" if product_family == "dinsar" else "timeseries_bundle"
    if not _text(document.get("display_name")) and _text(document.get("product_id")):
        document["display_name"] = _text(document.get("product_id"))
    return document


def _normalize_legacy_dinsar_manifest(payload: Dict[str, Any]) -> Dict[str, Any]:
    document = copy.deepcopy(payload)
    run_payload = _copy_dict(document.get("run"))
    source_payload = _copy_dict(document.get("source"))
    engine_payload = _copy_dict(document.get("engine"))
    document["source_schema_version"] = LEGACY_DINSAR_PACKAGE_SCHEMA
    document["product_family"] = _text(document.get("product_family")) or "dinsar"
    document["product_type"] = "dinsar_interferogram"
    document["engine_code"] = _text(engine_payload.get("code")) or _text(document.get("engine_code")) or "unknown"
    document["processor"] = {
        "code": (
            _text(document.get("processor_code"))
            or _text(run_payload.get("profile_code"))
            or _text(engine_payload.get("code"))
            or "unknown"
        ),
        "profile_code": _text(run_payload.get("profile_code")) or None,
    }
    runtime = _copy_dict(document.get("runtime"))
    if not _text(runtime.get("kind")):
        runtime["kind"] = "windows" if document["engine_code"] in {"envi", "sarscape"} else None
    document["runtime"] = runtime
    document["source"] = {
        **source_payload,
        "native_output_dir": (
            _text(source_payload.get("native_output_dir"))
            or _text(source_payload.get("output_dir"))
            or None
        ),
    }
    document["canonical"] = build_canonical_descriptor(
        document.get("assets") or [],
        product_family="dinsar",
    )
    return _normalize_canonical_manifest(document)


def _normalize_legacy_timeseries_manifest(payload: Dict[str, Any]) -> Dict[str, Any]:
    document = copy.deepcopy(payload)
    runtime_payload = _copy_dict(document.get("runtime"))
    source_summary = _copy_dict(document.get("source_summary"))
    document["source_schema_version"] = LEGACY_TIMESERIES_PACKAGE_SCHEMA
    document["product_family"] = _text(document.get("product_family")) or "timeseries"
    document["product_type"] = "timeseries_bundle"
    document["identity"] = {
        **_copy_dict(document.get("identity")),
        "stack_key": (
            _text(_copy_dict(document.get("identity")).get("stack_key"))
            or _text(document.get("stack_key"))
            or _text(document.get("group_key"))
            or None
        ),
        "run_key": (
            _text(_copy_dict(document.get("identity")).get("run_key"))
            or _text(document.get("run_key"))
            or _text(document.get("run_id"))
            or None
        ),
    }
    document["engine"] = {
        **_copy_dict(document.get("engine")),
        "code": _text(document.get("engine_code")) or _text(_copy_dict(document.get("engine")).get("code")) or "unknown",
    }
    document["processor"] = {
        "code": _text(document.get("processor_code")) or "unknown",
        "profile_code": _text(document.get("processor_code")) or None,
    }
    if not _text(runtime_payload.get("kind")):
        runtime_payload["kind"] = "wsl"
    document["runtime"] = runtime_payload
    document["source"] = {
        **_copy_dict(document.get("source")),
        "publish_dir": (
            _text(_copy_dict(document.get("source")).get("publish_dir"))
            or _text(source_summary.get("publish_dir_windows"))
            or None
        ),
        "native_output_dir": (
            _text(_copy_dict(document.get("source")).get("native_output_dir"))
            or _text(source_summary.get("mintpy_work_dir_windows"))
            or None
        ),
        "work_dir": _text(source_summary.get("generated_stack_manifest_path_windows")) or None,
        "source_root": _text(source_summary.get("selected_manifest_path_windows")) or None,
    }
    document["temporal"] = {
        **_copy_dict(document.get("temporal")),
        "reference_date": _text(document.get("reference_date")) or None,
        "stack_dates": [
            str(item).strip()
            for item in document.get("stack_dates") or []
            if str(item).strip()
        ],
        "published_at": _text(document.get("published_at")) or None,
        "produced_at": _text(document.get("produced_at")) or None,
    }
    document["assets"] = canonicalize_timeseries_artifacts(document.get("artifacts") or [])
    document["canonical"] = build_canonical_descriptor(
        document.get("assets") or [],
        product_family="timeseries",
    )
    return _normalize_canonical_manifest(document)


def normalize_package_manifest(payload: Dict[str, Any]) -> Dict[str, Any]:
    schema_version = _text((payload or {}).get("schema_version")).lower()
    if schema_version == CANONICAL_PACKAGE_SCHEMA:
        return _normalize_canonical_manifest(copy.deepcopy(payload))
    if schema_version == LEGACY_DINSAR_PACKAGE_SCHEMA:
        return _normalize_legacy_dinsar_manifest(payload)
    if schema_version == LEGACY_TIMESERIES_PACKAGE_SCHEMA:
        return _normalize_legacy_timeseries_manifest(payload)
    raise ValueError(f"Unsupported package schema_version: {schema_version or '<empty>'}")
