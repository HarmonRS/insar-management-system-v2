from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from .product_package_schema import (
    CANONICAL_PACKAGE_LAYOUT,
    CANONICAL_PACKAGE_SCHEMA,
    build_canonical_descriptor,
    normalize_package_manifest,
)


def _clean_dict(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return dict(payload or {})


def _kind_from_engine(engine_code: str) -> Optional[str]:
    normalized = str(engine_code or "").strip().lower()
    if normalized in {"envi", "sarscape", "landsar"}:
        return "windows"
    if normalized in {"isce2", "pyint", "gamma"}:
        return "wsl"
    return None


def _stable_digest(*parts: Any, length: int = 20) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:length]


def build_dinsar_package_manifest(
    *,
    product_id: str,
    display_name: str,
    task_name: Optional[str],
    engine_code: str,
    engine_version: Optional[str],
    processor_code: Optional[str],
    profile_code: Optional[str],
    runtime_id: Optional[str],
    source_primary_path: str,
    source_dir: str,
    publish_dir: str,
    identity: Dict[str, Any],
    source: Dict[str, Any],
    run: Dict[str, Any],
    temporal: Dict[str, Any],
    spatial: Dict[str, Any],
    dinsar_profile: Dict[str, Any],
    pairing_trace: Dict[str, Any],
    labels: Dict[str, Any],
    summary: Dict[str, Any],
    assets: List[Dict[str, Any]],
    issues: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    resolved_processor_code = (
        processor_code
        or (f"{engine_code}_{profile_code}" if profile_code else None)
        or engine_code
    )
    document = {
        "schema_version": CANONICAL_PACKAGE_SCHEMA,
        "package_layout": CANONICAL_PACKAGE_LAYOUT,
        "catalog_name": "dinsar",
        "product_family": "dinsar",
        "product_type": "dinsar_interferogram",
        "product_id": product_id,
        "display_name": display_name,
        "task_name": task_name or display_name,
        "pair_key": identity.get("pair_key"),
        "run_key": identity.get("run_key"),
        "identity": {
            "pair_key": identity.get("pair_key"),
            "stack_key": identity.get("stack_key"),
            "run_key": identity.get("run_key"),
            "task_alias": identity.get("task_alias"),
        },
        "engine": {
            "code": engine_code,
            "version": engine_version,
        },
        "processor": {
            "code": resolved_processor_code,
            "profile_code": profile_code,
        },
        "runtime": {
            "runtime_id": runtime_id,
            "kind": _kind_from_engine(engine_code),
        },
        "source": {
            **_clean_dict(source),
            "primary_path": source_primary_path,
            "source_dir": source_dir,
            "publish_dir": publish_dir,
            "native_output_dir": (
                _clean_dict(source).get("native_output_dir")
                or _clean_dict(source).get("output_dir")
            ),
        },
        "run": _clean_dict(run),
        "temporal": _clean_dict(temporal),
        "spatial": _clean_dict(spatial),
        "dinsar_profile": _clean_dict(dinsar_profile),
        "labels": _clean_dict(labels),
        "pairing_trace": _clean_dict(pairing_trace),
        "summary": _clean_dict(summary),
        "assets": list(assets or []),
        "canonical": build_canonical_descriptor(assets or [], product_family="dinsar"),
        "issues": list(issues or []),
        "published_at": _clean_dict(temporal).get("published_at"),
        "produced_at": _clean_dict(temporal).get("produced_at"),
        "engine_code": engine_code,
        "processor_code": resolved_processor_code,
        "runtime_id": runtime_id,
    }
    return normalize_package_manifest(document)


def upgrade_timeseries_package_manifest(
    payload: Dict[str, Any],
    *,
    run_context: Dict[str, Any],
    source_summary: Dict[str, Any],
) -> Dict[str, Any]:
    document = normalize_package_manifest(payload)
    engine_code = str(run_context.get("engine_code") or (document.get("engine") or {}).get("code") or "unknown")
    processor_code = str(
        run_context.get("processor_code")
        or document.get("processor_code")
        or ((document.get("processor") or {}).get("code"))
        or "unknown"
    )
    runtime_id = run_context.get("runtime_id") or document.get("runtime_id")

    document.update(
        {
            "schema_version": CANONICAL_PACKAGE_SCHEMA,
            "package_layout": CANONICAL_PACKAGE_LAYOUT,
            "catalog_name": "psinsar",
            "product_family": "timeseries",
            "product_type": "timeseries_bundle",
            "run_id": run_context.get("run_id"),
            "run_name": run_context.get("run_name"),
            "batch_id": run_context.get("batch_id"),
            "plan_id": run_context.get("plan_id"),
            "plan_strategy": run_context.get("plan_strategy"),
            "task_id": run_context.get("task_id"),
            "workflow_run_id": run_context.get("workflow_run_id"),
            "mode": run_context.get("mode"),
            "engine_code": engine_code,
            "processor_code": processor_code,
            "runtime_id": runtime_id,
            "stack_key": run_context.get("stack_key") or document.get("stack_key"),
            "group_key": run_context.get("group_key") or document.get("group_key"),
            "reference_date": run_context.get("reference_date") or document.get("reference_date"),
            "stack_dates": run_context.get("stack_dates") or document.get("stack_dates") or [],
            "published_at": run_context.get("published_at") or document.get("published_at"),
            "produced_at": run_context.get("produced_at") or document.get("produced_at"),
            "source_summary": {
                **_clean_dict(document.get("source_summary")),
                **_clean_dict(source_summary),
            },
        }
    )
    if not str(document.get("product_id") or "").strip():
        document["product_id"] = "psinsar_" + _stable_digest(
            run_context.get("run_id"),
            document.get("stack_key") or document.get("group_key"),
            document.get("reference_date"),
            length=20,
        )

    document["identity"] = {
        **_clean_dict(document.get("identity")),
        "stack_key": document.get("stack_key") or document.get("group_key"),
        "run_key": run_context.get("run_id") or _clean_dict(document.get("identity")).get("run_key"),
        "plan_id": run_context.get("plan_id") or _clean_dict(document.get("identity")).get("plan_id"),
    }
    document["engine"] = {
        **_clean_dict(document.get("engine")),
        "code": engine_code,
    }
    document["processor"] = {
        **_clean_dict(document.get("processor")),
        "code": processor_code,
        "profile_code": processor_code,
    }
    runtime_payload = {
        **_clean_dict(document.get("runtime")),
        **_clean_dict(run_context.get("runtime")),
    }
    runtime_payload["runtime_id"] = runtime_id
    runtime_payload["kind"] = runtime_payload.get("kind") or _kind_from_engine(engine_code)
    document["runtime"] = runtime_payload

    source_payload = {
        **_clean_dict(document.get("source")),
        "publish_dir": run_context.get("publish_dir"),
        "native_output_dir": (
            run_context.get("native_output_dir")
            or _clean_dict(source_summary).get("mintpy_work_dir_windows")
            or _clean_dict(document.get("source")).get("native_output_dir")
        ),
        "work_dir": (
            run_context.get("work_dir")
            or _clean_dict(source_summary).get("generated_stack_manifest_path_windows")
            or _clean_dict(document.get("source")).get("work_dir")
        ),
        "source_root": (
            run_context.get("source_root")
            or _clean_dict(source_summary).get("selected_manifest_path_windows")
            or _clean_dict(document.get("source")).get("source_root")
        ),
    }
    document["source"] = source_payload

    temporal_payload = {
        **_clean_dict(document.get("temporal")),
        "reference_date": document.get("reference_date"),
        "stack_dates": [
            str(item).strip()
            for item in document.get("stack_dates") or []
            if str(item).strip()
        ],
        "published_at": document.get("published_at"),
        "produced_at": document.get("produced_at"),
    }
    document["temporal"] = temporal_payload
    document["canonical"] = build_canonical_descriptor(
        document.get("assets") or [],
        product_family="timeseries",
    )
    return normalize_package_manifest(document)
