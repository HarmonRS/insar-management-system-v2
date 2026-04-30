"""Validate the SARscape SBAS parameter template without executing SBAS.

The validation scope is intentionally limited to the template contract:

- load the checked-in SARscape SBAS template
- inspect the native wf_sbas task parameters through taskengine
- resolve template macros against a selected stack manifest
- verify required inputs, parameter names, basic types, and source paths

It does not call task.execute() and does not run SARscape processing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate SARscape SBAS template parameters without executing SARscape."
    )
    parser.add_argument(
        "--stack-manifest",
        required=True,
        help="Path to selected_stack_manifest.json.",
    )
    parser.add_argument(
        "--template",
        default="",
        help="Optional SARscape SBAS template path. Defaults to configured template.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output JSON report path.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Task inspection timeout in seconds.",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip live wf_sbas parameter inspection.",
    )
    return parser.parse_args()


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _path_exists(path_text: str) -> bool:
    text = str(path_text or "").strip()
    return bool(text) and os.path.exists(os.path.normpath(text))


def _dem_exists(dem: Dict[str, Any]) -> Dict[str, Any]:
    url = str(dem.get("url") or "").replace("/", os.sep)
    aux = [str(item or "").replace("/", os.sep) for item in dem.get("auxiliary_url") or []]
    return {
        "url": dem.get("url"),
        "url_exists": _path_exists(url),
        "auxiliary_url": dem.get("auxiliary_url") or [],
        "auxiliary_exists": [_path_exists(item) for item in aux],
    }


def _scene_path_report(scenes: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for index, scene in enumerate(scenes):
        meta_path = str(scene.get("meta_path") or "").strip()
        tiff_path = str(scene.get("tiff_path") or "").strip()
        folder_path = str(scene.get("folder_path") or "").strip()
        rows.append(
            {
                "index": index,
                "imaging_date": scene.get("imaging_date"),
                "meta_path": meta_path,
                "meta_exists": _path_exists(meta_path),
                "tiff_path": tiff_path,
                "tiff_exists": _path_exists(tiff_path),
                "folder_path": folder_path,
                "folder_exists": _path_exists(folder_path),
            }
        )
    return {
        "scene_count": len(scenes),
        "missing_meta_count": sum(1 for item in rows if not item["meta_exists"]),
        "missing_tiff_count": sum(1 for item in rows if not item["tiff_exists"]),
        "missing_folder_count": sum(1 for item in rows if not item["folder_exists"]),
        "scenes": rows,
    }


def _validate_resolved_parameters(
    parameters: Dict[str, Any],
    live_input_names: List[str],
    live_required_inputs: List[str],
    live_choice_lists: Dict[str, List[Any]],
) -> List[str]:
    issues: List[str] = []
    live_input_set = set(live_input_names)
    for key in parameters:
        if live_input_set and key not in live_input_set:
            issues.append(f"Template parameter is not a live wf_sbas input: {key}")

    for key in live_required_inputs:
        value = parameters.get(key)
        if value is None or value == "" or value == []:
            issues.append(f"Required live wf_sbas input is missing or empty: {key}")

    input_files = parameters.get("INPUT_FILE_LIST")
    if not isinstance(input_files, list) or not input_files:
        issues.append("INPUT_FILE_LIST must resolve to a non-empty list.")
    elif any(not isinstance(item, str) or not item.strip() for item in input_files):
        issues.append("INPUT_FILE_LIST contains an empty or non-string item.")

    output_folder = parameters.get("OUTPUT_FOLDER")
    if output_folder is not None and not isinstance(output_folder, str):
        issues.append("OUTPUT_FOLDER must resolve to a string path.")

    dem = parameters.get("DEM_SARSCAPEDATA")
    if dem is not None:
        if not isinstance(dem, dict):
            issues.append("DEM_SARSCAPEDATA must resolve to a SARSCAPEDATA object.")
        elif dem.get("factory") != "ENVISARscapedata":
            issues.append("DEM_SARSCAPEDATA.factory must be ENVISARscapedata.")

    if "GEOCODE_RG_GRID_SIZE" in parameters and not isinstance(
        parameters.get("GEOCODE_RG_GRID_SIZE"),
        (int, float),
    ):
        issues.append("GEOCODE_RG_GRID_SIZE must be numeric.")

    if "ESTIMATE_RESIDUAL_HEIGHT" in parameters and not isinstance(
        parameters.get("ESTIMATE_RESIDUAL_HEIGHT"),
        bool,
    ):
        issues.append("ESTIMATE_RESIDUAL_HEIGHT must be boolean.")

    for key, choices in live_choice_lists.items():
        if key in parameters and choices and parameters[key] not in choices:
            issues.append(f"{key} is not in live choice list: {parameters[key]}")

    return issues


def main() -> int:
    repo_root = _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from backend.app.config import ensure_project_env_loaded
    from backend.app.services import envi_service
    from backend.app.services.sarscape_sbas_service import (
        NATIVE_WORKFLOW_TASK,
        _resolve_template_value,
        _scene_input_uris,
        default_parameter_template_path,
        load_parameter_template,
    )

    ensure_project_env_loaded()
    args = _parse_args()

    manifest_path = Path(args.stack_manifest).resolve()
    template_path = Path(args.template).resolve() if args.template else Path(default_parameter_template_path()).resolve()
    stack_manifest = _read_json(manifest_path)
    template_status = load_parameter_template(str(template_path))
    template = template_status.get("template") if isinstance(template_status.get("template"), dict) else {}
    native_workflow = template.get("native_workflow") if isinstance(template.get("native_workflow"), dict) else {}
    task_name = str(native_workflow.get("task_name") or NATIVE_WORKFLOW_TASK).strip()

    live_report: Dict[str, Any] = {"skipped": True}
    live_task: Dict[str, Any] = {}
    if not args.skip_live:
        live_report = envi_service.inspect_sarscape_sbas_tasks_subprocess(
            [task_name],
            include_parameters=True,
            timeout_seconds=max(10, int(args.timeout or 120)),
        )
        live_task = next(
            (
                item
                for item in live_report.get("tasks") or []
                if str(item.get("name") or "") == task_name
            ),
            {},
        )

    scenes = stack_manifest.get("scenes") if isinstance(stack_manifest.get("scenes"), list) else []
    work_root = Path(stack_manifest.get("proposed_scratch_windows") or manifest_path.parents[1]).resolve()
    output_root = work_root / "sarscape_sbas_template_validation_output"
    network_edges_path = output_root / "selected_network_edges.json"
    context = {
        "${work_root}": str(work_root),
        "${output_root}": str(output_root),
        "${selected_stack_manifest}": str(manifest_path),
        "${selected_network_edges}": str(network_edges_path),
        "${scene_meta_paths}": [
            str(item.get("meta_path"))
            for item in scenes
            if str(item.get("meta_path") or "").strip()
        ],
        "${scene_input_uris}": _scene_input_uris(scenes),
        "${scene_folder_paths}": [
            str(item.get("folder_path"))
            for item in scenes
            if str(item.get("folder_path") or "").strip()
        ],
        "${selection_params}": stack_manifest.get("selection_params") or {},
        "${dem_sarscapedata}": envi_service._build_sarscapedata(envi_service.DEM_BASE_FILE),  # noqa: SLF001
    }
    resolved_parameters = _resolve_template_value(native_workflow.get("parameters") or {}, context)

    live_parameters = live_task.get("parameters") if isinstance(live_task.get("parameters"), list) else []
    live_choice_lists = {
        str(item.get("name")): list(item.get("choice_list") or [])
        for item in live_parameters
        if isinstance(item, dict) and isinstance(item.get("choice_list"), list)
    }
    issues: List[str] = []
    execution_gate_issues: List[str] = []
    for item in template_status.get("errors") or []:
        text = str(item)
        if text == "Template is not marked validated=true.":
            execution_gate_issues.append(text)
        else:
            issues.append(text)
    if not bool(template_status.get("readable")):
        issues.append("Template is not readable.")
    if not args.skip_live:
        if not bool(live_report.get("ok")):
            issues.append("Live wf_sbas parameter inspection failed.")
        if not bool(live_task.get("available")):
            issues.append("Live wf_sbas task is not available to taskengine.")
    issues.extend(
        _validate_resolved_parameters(
            resolved_parameters,
            list(live_task.get("input_names") or []),
            list(live_task.get("required_input_names") or []),
            live_choice_lists,
        )
    )

    scene_report = _scene_path_report(scenes)
    if scene_report["missing_meta_count"]:
        issues.append("One or more scene meta_path files are missing.")
    dem_report = _dem_exists(resolved_parameters.get("DEM_SARSCAPEDATA") or {})
    if not dem_report["url_exists"] and not all(dem_report["auxiliary_exists"]):
        issues.append("DEM SARSCAPEDATA path or auxiliary files are missing.")

    report = {
        "schema": "insar.sarscape-sbas-template-validation/v1",
        "created_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "ok": not issues,
        "validation_scope": "template_contract_only_no_task_execute",
        "issues": issues,
        "execution_gate_issues": execution_gate_issues,
        "template": {
            "path": str(template_path),
            "validated_flag": bool(template_status.get("validated")),
            "execution_strategy": template_status.get("execution_strategy"),
            "native_workflow_task": task_name,
            "errors": template_status.get("errors") or [],
        },
        "live_task": {
            "skipped": bool(args.skip_live),
            "ok": bool(live_report.get("ok")) if not args.skip_live else None,
            "available": bool(live_task.get("available")) if live_task else None,
            "parameter_count": live_task.get("parameter_count"),
            "input_names": live_task.get("input_names") or [],
            "required_input_names": live_task.get("required_input_names") or [],
            "output_names": live_task.get("output_names") or [],
            "error": live_task.get("error"),
        },
        "environment": {
            "runner_cwd": envi_service.get_envi_runner_cwd(),
            "envi_custom_code": envi_service.get_envi_runner_env().get("ENVI_CUSTOM_CODE"),
            "dem_base_file": envi_service.DEM_BASE_FILE,
        },
        "stack_manifest": {
            "path": str(manifest_path),
            "scene_count": len(scenes),
            "network_edge_count": len(stack_manifest.get("network_edges") or []),
            "reference_date": stack_manifest.get("reference_date"),
            "processor_code": stack_manifest.get("processor_code"),
        },
        "resolved_parameters": {
            "keys": sorted(resolved_parameters.keys()),
            "INPUT_FILE_LIST_count": len(resolved_parameters.get("INPUT_FILE_LIST") or []),
            "OUTPUT_FOLDER": resolved_parameters.get("OUTPUT_FOLDER"),
            "GEOCODE_RG_GRID_SIZE": resolved_parameters.get("GEOCODE_RG_GRID_SIZE"),
            "ESTIMATE_RESIDUAL_HEIGHT": resolved_parameters.get("ESTIMATE_RESIDUAL_HEIGHT"),
            "DISPLACEMENT_MODEL_TYPE": resolved_parameters.get("DISPLACEMENT_MODEL_TYPE"),
            "DEM_SARSCAPEDATA": dem_report,
        },
        "scene_paths": scene_report,
    }

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = repo_root / "backend" / "runtime" / "sarscape_sbas_template_validation_latest.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
