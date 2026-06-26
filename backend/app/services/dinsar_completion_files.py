from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..config import settings
from .dinsar_naming import RUN_META_FILENAME


CURRENT_POINTER_DIRNAME = "current"
EXECUTION_MANIFEST_FILENAME = "execution_manifest.json"
EXECUTION_STATUS_COMPLETED = "COMPLETED"
_SAFE_POINTER_RE = re.compile(r"[^0-9A-Za-z._-]+")


def _normalize_path(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text))


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


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _utc_text(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _sanitize_pointer_fragment(value: str, default: str) -> str:
    text = _SAFE_POINTER_RE.sub("_", str(value or "").strip()).strip("._")
    return text or default


def _current_pointer_path(results_root_dir: str, *, engine_code: str, profile_code: str) -> str:
    pointer_name = (
        f"{_sanitize_pointer_fragment(engine_code or 'engine', 'engine')}__"
        f"{_sanitize_pointer_fragment(profile_code or 'profile', 'profile')}.json"
    )
    return os.path.join(_normalize_path(results_root_dir), CURRENT_POINTER_DIRNAME, pointer_name)


def _runtime_id_for_engine(engine_code: str) -> Optional[str]:
    normalized = str(engine_code or "").strip().lower()
    if normalized == "isce2":
        return getattr(settings, "ISCE2_RUNTIME_ID", "") or None
    if normalized == "landsar":
        return getattr(settings, "LANDSAR_RUNTIME_ID", "") or None
    if normalized in {"pyint", "gamma"}:
        return getattr(settings, "PYINT_RUNTIME_ID", "") or None
    return None


def _normalize_source_files(primary_file: str, source_files: List[str] | None) -> List[str]:
    normalized_primary = _normalize_path(primary_file)
    normalized: List[str] = []
    seen: set[str] = set()
    for raw_path in [normalized_primary, *(source_files or [])]:
        path = _normalize_path(raw_path)
        if not path or path in seen or not os.path.isfile(path):
            continue
        seen.add(path)
        normalized.append(path)
    return normalized


def _infer_results_root_dir(run_dir: str, pair_key: str) -> str:
    normalized_run_dir = _normalize_path(run_dir)
    runs_dir = os.path.dirname(normalized_run_dir)
    if os.path.basename(runs_dir).lower() == "runs":
        return os.path.dirname(runs_dir)
    if pair_key:
        return os.path.join(os.path.dirname(normalized_run_dir), pair_key)
    return os.path.dirname(normalized_run_dir)


def repair_managed_completion_files(
    run_dir: str,
    *,
    primary_file: str,
    source_files: List[str] | None = None,
    run_meta: Optional[Dict[str, Any]] = None,
    update_run_metadata: bool = True,
) -> Dict[str, Any]:
    normalized_run_dir = _normalize_path(run_dir)
    if not os.path.isdir(normalized_run_dir):
        raise FileNotFoundError(f"Run directory not found: {normalized_run_dir}")

    run_meta_path = os.path.join(normalized_run_dir, RUN_META_FILENAME)
    payload = dict(run_meta or _load_json(run_meta_path) or {})
    if not payload:
        raise FileNotFoundError(f"Run metadata not found: {run_meta_path}")

    normalized_primary = _normalize_path(primary_file or payload.get("primary_file"))
    normalized_sources = _normalize_source_files(
        normalized_primary,
        source_files or payload.get("source_files"),
    )
    if not normalized_primary or not os.path.isfile(normalized_primary):
        raise FileNotFoundError(f"Primary output file not found: {normalized_primary or '<empty>'}")
    if not normalized_sources:
        normalized_sources = [normalized_primary]

    run_key = _first_text(payload.get("run_key"), os.path.basename(normalized_run_dir))
    pair_key = _first_text(
        payload.get("pair_key"),
        os.path.basename(os.path.dirname(os.path.dirname(normalized_run_dir))),
    )
    engine_code = _first_text(payload.get("engine_code"), "isce2")
    profile_code = _first_text(payload.get("profile_code"), "unknown")
    task_name = _first_text(payload.get("task_name"), payload.get("task_alias"), run_key)
    task_alias = _first_text(payload.get("task_alias"), payload.get("task_name"), task_name)
    output_dir = _normalize_path(payload.get("output_dir") or normalized_run_dir)
    if normalized_primary.startswith(normalized_run_dir + os.sep):
        output_dir = normalized_run_dir
    native_output_dir = _normalize_path(
        payload.get("native_output_dir") or os.path.join(normalized_run_dir, "native")
    )
    if not native_output_dir.startswith(normalized_run_dir + os.sep):
        local_native_dir = os.path.join(normalized_run_dir, "native")
        if os.path.isdir(local_native_dir):
            native_output_dir = _normalize_path(local_native_dir)
        else:
            native_output_dir = output_dir
    results_root_dir = _infer_results_root_dir(normalized_run_dir, pair_key)
    publish_root_dir = _normalize_path(os.path.dirname(results_root_dir))

    metrics: Dict[str, Any] = {}
    acceptance = payload.get("acceptance") if isinstance(payload.get("acceptance"), dict) else {}
    acceptance_metrics = acceptance.get("metrics") if isinstance(acceptance.get("metrics"), dict) else {}
    run_metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    if acceptance_metrics:
        metrics["acceptance"] = acceptance_metrics
    if run_metrics:
        metrics["run_metrics"] = run_metrics
    if payload.get("manual_recovery") or payload.get("recovery_mode"):
        metrics["recovery"] = {
            "manual_recovery": bool(payload.get("manual_recovery")),
            "recovery_mode": _first_text(payload.get("recovery_mode")),
            "recovered_at": _first_text(payload.get("recovered_at")),
        }

    execution_payload = {
        "format_version": 1,
        "run_id": _first_text(payload.get("production_run_id"), payload.get("run_id")),
        "product_family": "dinsar",
        "run_key": run_key,
        "task_id": _first_text(payload.get("task_id")),
        "engine_code": engine_code,
        "profile_code": profile_code,
        "runtime_id": _first_text(payload.get("runtime_id")) or _runtime_id_for_engine(engine_code),
        "mode": _first_text(payload.get("mode"), "managed"),
        "task_name": task_name,
        "task_alias": task_alias,
        "pair_key": pair_key,
        "pair_uid": _first_text(payload.get("pair_uid"), payload.get("scene_pair_uid")),
        "network_run_id": _first_text(payload.get("network_run_id")),
        "network_edge_id": payload.get("network_edge_id"),
        "policy_version": _first_text(payload.get("policy_version")),
        "selection_strategy": _first_text(payload.get("selection_strategy")),
        "source_root": _first_text(payload.get("source_root")),
        "source_task_dir": _first_text(payload.get("task_dir")),
        "results_root_dir": results_root_dir,
        "publish_root_dir": publish_root_dir,
        "output_dir": output_dir,
        "native_output_dir": native_output_dir,
        "primary_file": normalized_primary,
        "source_files": normalized_sources,
        "status": EXECUTION_STATUS_COMPLETED,
        "metrics": metrics,
        "created_at": _utc_text(payload.get("started_at")),
        "finished_at": _utc_text(payload.get("finished_at") or payload.get("recovered_at")),
        "recovered_from_run_meta": True,
    }
    execution_manifest_path = _write_json(
        os.path.join(normalized_run_dir, EXECUTION_MANIFEST_FILENAME),
        execution_payload,
    )

    pointer_payload = {
        "format_version": 1,
        "product_family": "dinsar",
        "engine_code": engine_code,
        "profile_code": profile_code,
        "runtime_id": execution_payload.get("runtime_id"),
        "run_key": run_key,
        "execution_id": None,
        "status": EXECUTION_STATUS_COMPLETED,
        "output_dir": output_dir,
        "native_output_dir": native_output_dir,
        "manifest_path": execution_manifest_path,
        "primary_file": normalized_primary,
        "source_files": normalized_sources,
        "updated_at": _utc_text(payload.get("finished_at") or payload.get("recovered_at")),
        "recovered_from_run_meta": True,
    }
    pointer_path = _write_json(
        _current_pointer_path(
            results_root_dir,
            engine_code=engine_code,
            profile_code=profile_code,
        ),
        pointer_payload,
    )

    if update_run_metadata:
        payload["execution_manifest_path"] = execution_manifest_path
        payload["current_pointer_path"] = pointer_path
        payload["output_dir"] = output_dir
        payload["native_output_dir"] = native_output_dir
        payload["primary_file"] = normalized_primary
        payload["source_files"] = normalized_sources
        _write_json(run_meta_path, payload)

    return {
        "run_dir": normalized_run_dir,
        "results_root_dir": results_root_dir,
        "execution_manifest_path": execution_manifest_path,
        "current_pointer_path": pointer_path,
        "engine_code": engine_code,
        "profile_code": profile_code,
        "run_key": run_key,
        "pair_key": pair_key,
    }
