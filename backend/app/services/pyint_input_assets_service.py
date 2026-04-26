"""PyINT input-asset resolution and materialization helpers."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List

from ..config import settings
from .orbit_converter import get_source_orbit_inventory
from .pyint_service import (
    discover_lt1_archives,
    infer_scene_date_from_archives,
    infer_task_identity,
    validate_pyint_root_dir,
)


VALID_DEM_MODES = {"local_fabdem", "opentopo", "prepared_file"}
VALID_ORBIT_POLICIES = {"validate_only", "require_txt", "stage_txt"}
VALID_PRECISE_ORBIT_MODES = {"replace", "replace_and_validate"}


def _utc_now_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _normalize_path(path: Any) -> str:
    text = str(path or "").strip().strip('"').strip("'")
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text))


def _copy_json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _normalize_lt1_satellite(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "").replace("_", "")
    if "LT1A" in text:
        return "LT1A"
    if "LT1B" in text:
        return "LT1B"
    if text in {"A", "LTA"}:
        return "LT1A"
    if text in {"B", "LTB"}:
        return "LT1B"
    return ""


def _infer_satellite_from_archives(paths: List[str]) -> str:
    satellites = {
        satellite
        for path in paths
        for satellite in [_normalize_lt1_satellite(os.path.basename(path))]
        if satellite
    }
    if len(satellites) == 1:
        return next(iter(satellites))
    return ""


def _get_dem_mode() -> str:
    raw_mode = str(getattr(settings, "PYINT_DEM_MODE", "local_fabdem") or "local_fabdem").strip().lower()
    if raw_mode not in VALID_DEM_MODES:
        return "local_fabdem"
    return raw_mode


def _get_orbit_policy() -> str:
    raw_policy = str(getattr(settings, "PYINT_ORBIT_POLICY", "require_txt") or "require_txt").strip().lower()
    if raw_policy not in VALID_ORBIT_POLICIES:
        return "require_txt"
    return raw_policy


def _get_orbit_pool_root() -> str:
    explicit = _normalize_path(getattr(settings, "PYINT_ORBIT_POOL_TXT", ""))
    if explicit:
        return explicit
    return _normalize_path(settings.ORBIT_POOL_ENVI)


def get_pyint_precise_orbit_bridge_summary() -> Dict[str, Any]:
    mode = str(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_MODE", "replace") or "replace").strip().lower()
    if mode not in VALID_PRECISE_ORBIT_MODES:
        mode = "replace"
    return {
        "enabled": bool(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_ENABLED", True)),
        "mode": mode,
        "strict": bool(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_STRICT", True)),
        "validate_with_orb_filt": bool(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT", False)),
        "backup": bool(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_BACKUP", True)),
        "orb_filt_degree": max(1, int(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_ORB_FILT_DEGREE", 5) or 5)),
    }


def _prepared_dem_variants(value: Any) -> List[str]:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return []

    normalized = _normalize_path(text)
    if not normalized:
        return []

    root, ext = os.path.splitext(normalized)
    if ext.lower() == ".wgs84":
        candidates = [normalized, root]
    elif not ext:
        candidates = [normalized + ".wgs84", normalized]
    else:
        candidates = [normalized]

    unique: List[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        item = _normalize_path(candidate)
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _resolve_prepared_dem_path() -> Dict[str, str]:
    explicit_value = getattr(settings, "PYINT_PREPARED_DEM_PATH", "")
    explicit_candidates = _prepared_dem_variants(explicit_value)
    if str(explicit_value or "").strip():
        for candidate in explicit_candidates:
            if os.path.isfile(candidate):
                return {
                    "path": candidate,
                    "resolved_from": "explicit",
                }
        return {
            "path": "",
            "resolved_from": "explicit",
        }

    sources = [
        ("isce2_dem_path", getattr(settings, "ISCE2_DEM_PATH", "")),
        ("idl_dinsar_dem_base_file", getattr(settings, "IDL_DINSAR_DEM_BASE_FILE", "")),
    ]
    for source_name, raw_value in sources:
        for candidate in _prepared_dem_variants(raw_value):
            if os.path.isfile(candidate):
                return {
                    "path": candidate,
                    "resolved_from": source_name,
                }
    return {
        "path": "",
        "resolved_from": "",
    }


def _inspect_prepared_dem_path(path: Any) -> Dict[str, Any]:
    normalized = _normalize_path(path)
    if not normalized:
        return {
            "path": "",
            "exists": False,
            "kind": "",
            "gamma_par_path": "",
            "gamma_par_exists": False,
            "xml_path": "",
            "xml_exists": False,
            "hdr_path": "",
            "hdr_exists": False,
            "vrt_path": "",
            "vrt_exists": False,
            "open_path": "",
        }

    gamma_par_path = normalized + ".par"
    xml_path = normalized + ".xml"
    hdr_path = normalized + ".hdr"
    vrt_path = normalized + ".vrt"

    path_exists = os.path.isfile(normalized)
    gamma_par_exists = os.path.isfile(gamma_par_path)
    xml_exists = os.path.isfile(xml_path)
    hdr_exists = os.path.isfile(hdr_path)
    vrt_exists = os.path.isfile(vrt_path)

    kind = ""
    open_path = ""
    if path_exists and gamma_par_exists:
        kind = "gamma_ready"
        open_path = normalized
    elif path_exists and (xml_exists or hdr_exists or vrt_exists):
        kind = "source_dem"
        open_path = vrt_path if vrt_exists else normalized

    return {
        "path": normalized,
        "exists": path_exists,
        "kind": kind,
        "gamma_par_path": gamma_par_path,
        "gamma_par_exists": gamma_par_exists,
        "xml_path": xml_path,
        "xml_exists": xml_exists,
        "hdr_path": hdr_path,
        "hdr_exists": hdr_exists,
        "vrt_path": vrt_path,
        "vrt_exists": vrt_exists,
        "open_path": open_path,
    }


def get_pyint_dem_summary() -> Dict[str, Any]:
    mode = _get_dem_mode()
    strict = bool(getattr(settings, "PYINT_DEM_STRICT", True))
    cache_root = _normalize_path(settings.PYINT_DEM_ROOT)
    fabdem_root = _normalize_path(getattr(settings, "PYINT_FABDEM_ROOT", ""))
    prepared_dem_resolution = _resolve_prepared_dem_path()
    prepared_dem_info = _inspect_prepared_dem_path(prepared_dem_resolution.get("path"))
    opentopo_dem_type = str(getattr(settings, "PYINT_OPENTOPO_DEM_TYPE", "SRTMGL1") or "SRTMGL1").strip() or "SRTMGL1"
    opentopo_api_key = str(getattr(settings, "PYINT_OPENTOPO_API_KEY", "") or "").strip()

    warnings: List[str] = []
    blockers: List[str] = []

    source_root = ""
    source_exists = False
    if mode == "local_fabdem":
        source_root = fabdem_root
        source_exists = bool(source_root and os.path.isdir(source_root))
    elif mode == "prepared_file":
        source_root = str(prepared_dem_info.get("path") or "")
        source_exists = bool(prepared_dem_info.get("exists"))
    cache_root_exists = bool(cache_root and os.path.isdir(cache_root))

    if mode == "local_fabdem":
        if not fabdem_root:
            message = "未配置 PYINT_FABDEM_ROOT。"
            if strict:
                blockers.append(message)
            else:
                warnings.append(message)
        elif not os.path.isdir(fabdem_root):
            message = f"本地 FABDEM 根目录不存在: {fabdem_root}"
            if strict:
                blockers.append(message)
            else:
                warnings.append(message)
    elif mode == "opentopo":
        if not opentopo_api_key:
            message = "DEM 策略为 OpenTopography，但未配置 PYINT_OPENTOPO_API_KEY。"
            if strict:
                blockers.append(message)
            else:
                warnings.append(message)
    elif mode == "prepared_file":
        if not prepared_dem_info.get("path"):
            if prepared_dem_resolution.get("resolved_from") == "explicit":
                message = "PYINT_PREPARED_DEM_PATH 已配置，但目标文件不存在。"
            else:
                message = (
                    "未配置 PYINT_PREPARED_DEM_PATH，且未能从 ISCE2_DEM_PATH / "
                    "IDL_DINSAR_DEM_BASE_FILE 解析现有 DEM。"
                )
            if strict:
                blockers.append(message)
            else:
                warnings.append(message)
        elif prepared_dem_info.get("kind") not in {"gamma_ready", "source_dem"}:
            message = (
                "现有 DEM 缺少可识别 sidecar，至少需要同名 .par，或 .xml/.hdr/.vrt 中的一个: "
                + str(prepared_dem_info.get("path") or "")
            )
            if strict:
                blockers.append(message)
            else:
                warnings.append(message)

    if not cache_root:
        blockers.append("未配置 PYINT_DEM_ROOT。")
    elif not cache_root_exists:
        warnings.append(f"DEM 缓存目录当前不存在，运行时将尝试创建: {cache_root}")

    status = "ok"
    if blockers:
        status = "blocked"
    elif warnings:
        status = "warning"

    if mode == "local_fabdem":
        detail = "使用本地 FABDEM 瓦片目录，由 PyINT 在 DEMDIR 中生成运行期 DEM。"
    elif mode == "prepared_file":
        if prepared_dem_info.get("kind") == "gamma_ready":
            detail = "使用现有 Gamma DEM，运行时将直接注入到 PyINT 模板。"
        else:
            detail = "使用现有系统 DEM，运行时将按任务覆盖区裁剪并转换为本次任务的 Gamma DEM。"
    else:
        detail = f"使用 OpenTopography 在线 DEM 源，DEM 类型为 {opentopo_dem_type}。"

    return {
        "mode": mode,
        "strict": strict,
        "source_root": source_root,
        "source_exists": source_exists,
        "cache_root": cache_root,
        "cache_root_exists": cache_root_exists,
        "fabdem_root": fabdem_root,
        "prepared_dem_path": str(prepared_dem_info.get("path") or ""),
        "prepared_dem_resolved_from": str(prepared_dem_resolution.get("resolved_from") or ""),
        "prepared_dem_kind": str(prepared_dem_info.get("kind") or ""),
        "prepared_dem_open_path": str(prepared_dem_info.get("open_path") or ""),
        "prepared_dem_support": {
            "gamma_par_exists": bool(prepared_dem_info.get("gamma_par_exists")),
            "xml_exists": bool(prepared_dem_info.get("xml_exists")),
            "hdr_exists": bool(prepared_dem_info.get("hdr_exists")),
            "vrt_exists": bool(prepared_dem_info.get("vrt_exists")),
        },
        "opentopo_dem_type": opentopo_dem_type,
        "opentopo_api_key_configured": bool(opentopo_api_key),
        "status": status,
        "detail": detail,
        "warnings": warnings,
        "blockers": blockers,
        "allow_submit": not blockers,
    }


def _load_orbit_inventory() -> Dict[str, Any]:
    pool_root = _get_orbit_pool_root()
    if not pool_root:
        return {
            "pool_root": "",
            "pool_exists": False,
            "files": {},
            "warnings": ["未配置 PYINT_ORBIT_POOL_TXT，且 ORBIT_POOL_ENVI 为空。"],
        }
    if not os.path.isdir(pool_root):
        return {
            "pool_root": pool_root,
            "pool_exists": False,
            "files": {},
            "warnings": [f"轨道池目录不存在: {pool_root}"],
        }

    inventory = get_source_orbit_inventory(pool_root, recursive=True)
    return {
        "pool_root": pool_root,
        "pool_exists": True,
        "files": inventory.get("files", {}),
        "warnings": list(inventory.get("errors", []) or []),
        "duplicate_count": int(inventory.get("duplicate_count", 0) or 0),
    }


def get_pyint_orbit_context() -> Dict[str, Any]:
    return _load_orbit_inventory()


def _resolve_orbit_file(
    *,
    role: str,
    satellite: str,
    date_text: str,
    pool_root: str,
    orbit_files: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    satellite_text = _normalize_lt1_satellite(satellite)
    normalized_date = str(date_text or "").strip()
    expected_name = (
        f"{satellite_text}_GpsData_GAS_C_{normalized_date}.txt"
        if satellite_text and normalized_date
        else ""
    )
    result: Dict[str, Any] = {
        "role": role,
        "satellite": satellite_text,
        "date": normalized_date,
        "expected_name": expected_name,
        "pool_root": pool_root,
        "resolved": False,
        "path": "",
        "resolution_method": "",
        "staged_path": "",
    }
    if not satellite_text:
        result["error"] = f"{role} 场景未能识别 LT-1 卫星型号。"
        return result
    if not normalized_date:
        result["error"] = f"{role} 场景未能识别成像日期。"
        return result

    stem = os.path.splitext(expected_name)[0]
    item = orbit_files.get(stem)
    if item and os.path.isfile(item.get("path", "")):
        result.update(
            {
                "resolved": True,
                "path": _normalize_path(item["path"]),
                "resolution_method": "indexed_pool_scan",
            }
        )
        return result

    direct_candidate = os.path.join(pool_root, satellite_text, expected_name)
    if os.path.isfile(direct_candidate):
        result.update(
            {
                "resolved": True,
                "path": _normalize_path(direct_candidate),
                "resolution_method": "direct_satellite_subdir",
            }
        )
        return result

    flat_candidate = os.path.join(pool_root, expected_name)
    if os.path.isfile(flat_candidate):
        result.update(
            {
                "resolved": True,
                "path": _normalize_path(flat_candidate),
                "resolution_method": "direct_pool_root",
            }
        )
        return result

    result["error"] = f"轨道池中缺少 {expected_name}"
    return result


def resolve_pyint_task_input_assets(
    task_dir: str,
    *,
    dem_summary: Dict[str, Any] | None = None,
    orbit_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    task_dir = _normalize_path(task_dir)
    task_identity = infer_task_identity(task_dir)
    pair_meta = task_identity["pair_meta"]
    archives = discover_lt1_archives(task_dir)
    master_archives = list(archives.get("master", []) or [])
    slave_archives = list(archives.get("slave", []) or [])

    warnings: List[str] = []
    blockers: List[str] = []

    master_date = task_identity["master_date"] or infer_scene_date_from_archives(master_archives)
    slave_date = task_identity["slave_date"] or infer_scene_date_from_archives(slave_archives)

    master_satellite = _normalize_lt1_satellite(pair_meta.get("master_satellite")) or _infer_satellite_from_archives(master_archives)
    slave_satellite = _normalize_lt1_satellite(pair_meta.get("slave_satellite")) or _infer_satellite_from_archives(slave_archives)

    if not master_archives:
        blockers.append("master/ 下未发现 LT-1 原始输入（LT1*.tar.gz 或 LT1*.tiff）。")
    if not slave_archives:
        blockers.append("slave/ 下未发现 LT-1 原始输入（LT1*.tar.gz 或 LT1*.tiff）。")
    if not master_date:
        blockers.append("未能识别主影像日期。")
    if not slave_date:
        blockers.append("未能识别从影像日期。")

    orbit_policy = _get_orbit_policy()
    orbit_context = orbit_context or get_pyint_orbit_context()
    orbit_pool_root = orbit_context.get("pool_root", "")
    orbit_pool_exists = bool(orbit_context.get("pool_exists"))
    orbit_files = orbit_context.get("files", {}) or {}

    orbit_warnings: List[str] = []
    if orbit_context.get("warnings"):
        orbit_warnings.extend(str(item) for item in orbit_context["warnings"] if item)

    master_orbit = _resolve_orbit_file(
        role="master",
        satellite=master_satellite,
        date_text=master_date,
        pool_root=orbit_pool_root,
        orbit_files=orbit_files,
    )
    slave_orbit = _resolve_orbit_file(
        role="slave",
        satellite=slave_satellite,
        date_text=slave_date,
        pool_root=orbit_pool_root,
        orbit_files=orbit_files,
    )

    for orbit_item in (master_orbit, slave_orbit):
        if orbit_item.get("resolved"):
            continue
        message = str(orbit_item.get("error") or f"{orbit_item.get('role')} 轨道缺失").strip()
        if orbit_policy == "validate_only":
            orbit_warnings.append(message)
        else:
            blockers.append(message)

    if not orbit_pool_root:
        if orbit_policy == "validate_only":
            orbit_warnings.append("轨道池未配置，当前仅记录警告。")
        else:
            blockers.append("轨道池未配置。")
    elif not orbit_pool_exists:
        if orbit_policy == "validate_only":
            orbit_warnings.append(f"轨道池目录不可用: {orbit_pool_root}")
        else:
            blockers.append(f"轨道池目录不可用: {orbit_pool_root}")

    warnings.extend(orbit_warnings)

    task_source = {
        "task_dir": task_dir,
        "task_name": task_identity["task_name"],
        "task_alias": task_identity["task_alias"],
        "pair_key": task_identity["pair_key"],
        "master_date": master_date,
        "slave_date": slave_date,
        "master_satellite": master_satellite,
        "slave_satellite": slave_satellite,
        "archives": {
            "master": master_archives,
            "slave": slave_archives,
        },
    }

    precise_orbit_bridge = get_pyint_precise_orbit_bridge_summary()
    orbits_summary = {
        "policy": orbit_policy,
        "pool_root": orbit_pool_root,
        "pool_exists": orbit_pool_exists,
        "master": master_orbit,
        "slave": slave_orbit,
        "resolved_count": int(bool(master_orbit.get("resolved"))) + int(bool(slave_orbit.get("resolved"))),
        "missing_count": int(not master_orbit.get("resolved")) + int(not slave_orbit.get("resolved")),
        "warnings": orbit_warnings,
        "stage_mode": "copy" if orbit_policy == "stage_txt" or precise_orbit_bridge.get("enabled") else "none",
        "precise_orbit_bridge": precise_orbit_bridge,
    }

    dem_payload = _copy_json_safe(dem_summary or get_pyint_dem_summary())
    allow_submit = not blockers and bool(dem_payload.get("allow_submit", True))

    return {
        "task_name": task_identity["task_name"],
        "task_alias": task_identity["task_alias"],
        "pair_key": task_identity["pair_key"],
        "task_dir": task_dir,
        "master_date": master_date,
        "slave_date": slave_date,
        "master_satellite": master_satellite,
        "slave_satellite": slave_satellite,
        "archive_counts": {
            "master": len(master_archives),
            "slave": len(slave_archives),
        },
        "warnings": warnings,
        "blockers": blockers,
        "allow_submit": allow_submit,
        "task_source": task_source,
        "dem": dem_payload,
        "orbit_resolution": {
            "master": master_orbit,
            "slave": slave_orbit,
        },
        "input_assets": {
            "task_source": task_source,
            "dem": dem_payload,
            "orbits": orbits_summary,
        },
    }


def build_pyint_input_preview(root_dir: str, num_to_process: int = 0) -> Dict[str, Any]:
    validation = validate_pyint_root_dir(root_dir, num_to_process)
    dem_summary = get_pyint_dem_summary()
    orbit_context = get_pyint_orbit_context()

    warnings: List[str] = list(dem_summary.get("warnings") or [])
    blockers: List[str] = list(dem_summary.get("blockers") or [])
    task_summaries: List[Dict[str, Any]] = []
    resolved_task_count = 0
    missing_task_count = 0

    for task_dir in validation.get("task_dirs", []) or []:
        task_summary = resolve_pyint_task_input_assets(
            task_dir,
            dem_summary=dem_summary,
            orbit_context=orbit_context,
        )
        task_summaries.append(task_summary)
        if task_summary.get("warnings"):
            warnings.extend(
                f"{task_summary['task_alias']}: {item}"
                for item in task_summary["warnings"]
            )
        if task_summary.get("blockers"):
            blockers.extend(
                f"{task_summary['task_alias']}: {item}"
                for item in task_summary["blockers"]
            )
        if task_summary["input_assets"]["orbits"]["missing_count"] == 0:
            resolved_task_count += 1
        else:
            missing_task_count += 1

    allow_submit = not blockers
    precise_orbit_bridge = get_pyint_precise_orbit_bridge_summary()
    return {
        "root_dir": validation["root_dir"],
        "mode": validation["mode"],
        "task_count": len(task_summaries),
        "selected_task_count": len(task_summaries),
        "allow_submit": allow_submit,
        "warnings": warnings,
        "blockers": blockers,
        "invalid_candidates": validation.get("invalid_candidates", []),
        "dem": dem_summary,
        "orbits": {
            "policy": _get_orbit_policy(),
            "pool_root": orbit_context.get("pool_root", ""),
            "pool_exists": bool(orbit_context.get("pool_exists")),
            "resolved_task_count": resolved_task_count,
            "missing_task_count": missing_task_count,
            "duplicate_count": int(orbit_context.get("duplicate_count", 0) or 0),
            "warnings": list(orbit_context.get("warnings") or []),
        },
        "precise_orbit_bridge": precise_orbit_bridge,
        "tasks": task_summaries,
    }


def summarize_preview_blockers(preview: Dict[str, Any], limit: int = 8) -> str:
    blockers = [str(item).strip() for item in (preview.get("blockers") or []) if str(item).strip()]
    if not blockers:
        return ""
    if len(blockers) <= limit:
        return "; ".join(blockers)
    return "; ".join(blockers[:limit]) + f"; 其余 {len(blockers) - limit} 项已省略"


def materialize_pyint_input_assets(
    *,
    task_summary: Dict[str, Any],
    input_assets_dir: str,
    project_name: str = "",
) -> Dict[str, Any]:
    input_assets_dir = _normalize_path(input_assets_dir)
    os.makedirs(input_assets_dir, exist_ok=True)

    record_enabled = bool(getattr(settings, "PYINT_RECORD_INPUT_ASSETS", True))
    orbits_dir = os.path.join(input_assets_dir, "orbits")
    dem_dir = os.path.join(input_assets_dir, "dem")
    if record_enabled:
        os.makedirs(orbits_dir, exist_ok=True)
        os.makedirs(dem_dir, exist_ok=True)

    manifest = _copy_json_safe(task_summary.get("input_assets") or {})
    manifest["generated_at"] = _utc_now_text()
    manifest["task_name"] = task_summary.get("task_name")
    manifest["task_alias"] = task_summary.get("task_alias")
    manifest["pair_key"] = task_summary.get("pair_key")
    manifest["task_dir"] = task_summary.get("task_dir")
    manifest["allow_submit"] = bool(task_summary.get("allow_submit"))
    manifest["warnings"] = list(task_summary.get("warnings") or [])
    manifest["blockers"] = list(task_summary.get("blockers") or [])

    dem_summary = manifest.get("dem") or {}
    if project_name:
        dem_summary["resolved_output_dir"] = os.path.join(_normalize_path(settings.PYINT_DEM_ROOT), project_name)
    manifest["dem"] = dem_summary

    orbits_summary = manifest.get("orbits") or {}
    staged_count = 0
    precise_orbit_bridge = get_pyint_precise_orbit_bridge_summary()
    should_stage_orbits = record_enabled and (
        str(orbits_summary.get("policy") or "").strip().lower() == "stage_txt"
        or precise_orbit_bridge.get("enabled")
    )
    if should_stage_orbits:
        for role in ("master", "slave"):
            orbit_item = orbits_summary.get(role) or {}
            orbit_path = _normalize_path(orbit_item.get("path"))
            expected_name = str(orbit_item.get("expected_name") or "").strip()
            if not orbit_item.get("resolved") or not orbit_path or not expected_name:
                continue
            target_path = os.path.join(orbits_dir, expected_name)
            if not os.path.exists(target_path):
                shutil.copy2(orbit_path, target_path)
            orbit_item["staged_path"] = target_path
            orbit_item["stage_operation"] = "copied"
            orbit_item["stage_reason"] = "precise_orbit_bridge" if precise_orbit_bridge.get("enabled") else "stage_txt_policy"
            staged_count += 1
            orbits_summary[role] = orbit_item
    manifest["orbits"] = orbits_summary

    materialized = {
        "input_assets_dir": input_assets_dir,
        "record_enabled": record_enabled,
        "orbits_dir": orbits_dir if record_enabled else "",
        "dem_dir": dem_dir if record_enabled else "",
        "orbits_staged_count": staged_count,
        "task_manifest_path": "",
        "dem_summary_path": "",
        "orbit_summary_path": "",
        "input_assets": manifest,
    }

    if not record_enabled:
        return materialized

    task_manifest_path = os.path.join(input_assets_dir, "task_manifest.json")
    dem_summary_path = os.path.join(dem_dir, "dem_summary.json")
    orbit_summary_path = os.path.join(orbits_dir, "orbit_summary.json")

    with open(task_manifest_path, "w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    with open(dem_summary_path, "w", encoding="utf-8") as fp:
        json.dump(dem_summary, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    with open(orbit_summary_path, "w", encoding="utf-8") as fp:
        json.dump(orbits_summary, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    materialized.update(
        {
            "task_manifest_path": task_manifest_path,
            "dem_summary_path": dem_summary_path,
            "orbit_summary_path": orbit_summary_path,
        }
    )
    return materialized
