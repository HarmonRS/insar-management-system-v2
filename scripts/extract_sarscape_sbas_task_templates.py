"""Extract SARscape SBAS task template metadata from installed .task files.

This script is intentionally file-based. It does not start ENVI, taskengine, or
envipyengine, so it is safe to use on workstations where live
task.parameters inspection can hang.

Examples:
    python scripts/extract_sarscape_sbas_task_templates.py --json
    python scripts/extract_sarscape_sbas_task_templates.py --template --output tmp_sarscape_sbas_template.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_ENVI_ROOT = Path(os.environ.get("SARSCAPE_ENVI_ROOT", r"C:\Program Files\Harris\ENVI56"))

NATIVE_WORKFLOW_TASKS = ["wf_sbas", "wf_esbas"]
SUPPORT_TASKS = [
    "SARscape_setting_output_folders",
    "SARsLoadPreferences",
    "SARsImportSarSelector",
    "SARscapeSuggestLooks",
    "SARscapeEnviuriToShape",
]
STACK_TASKS = [
    "SARsInSARStackSBASGenerateConnectionGraph",
    "SARsInSARStackSBASInterferogramGeneration",
    "SARsInSARStackSBASInversionStep1",
    "SARsInSARStackSBASInversionStep2",
    "SARsInSARStackSBASGeocode",
    "SARsInSARStackSBASVariogram",
]
ESBAS_TASKS = [
    "SARsInSARConnectionGraphESBAS",
    "SARsInSARStackESBASInterferogramGeneration",
    "SARsInSARStackESBASInversion",
    "SARsInSARStackESBASGeocode",
]
DEFAULT_TASKS = [
    *NATIVE_WORKFLOW_TASKS,
    *SUPPORT_TASKS,
    *STACK_TASKS,
    *ESBAS_TASKS,
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read SARscape SBAS .task files and emit a static parameter report."
    )
    parser.add_argument("--envi-root", default=str(DEFAULT_ENVI_ROOT), help="ENVI install root.")
    parser.add_argument("--task", action="append", default=[], help="Task name to extract. May be repeated.")
    parser.add_argument("--json", action="store_true", help="Print the extraction report as JSON.")
    parser.add_argument("--template", action="store_true", help="Print a backend template skeleton.")
    parser.add_argument("--output", default="", help="Optional output file for JSON/template output.")
    return parser.parse_args()


def _candidate_paths(envi_root: Path, task_name: str) -> Iterable[Path]:
    if task_name in NATIVE_WORKFLOW_TASKS:
        yield envi_root / "user_custom_code" / f"{task_name}.task"
    yield envi_root / "resource" / "templates" / "tasks" / "SARscape" / f"{task_name}.task"
    yield envi_root / "resource" / "templates" / "tasks" / f"{task_name}.task"
    yield envi_root / "user_custom_code" / f"{task_name}.task"


def _read_task(envi_root: Path, task_name: str) -> Dict[str, Any]:
    for path in _candidate_paths(envi_root, task_name):
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            return {"ok": True, "path": str(path), "payload": payload}
    return {"ok": False, "path": None, "payload": None, "error": "task file not found"}


def _choice_list(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return None


def _normalize_parameter(item: Dict[str, Any]) -> Dict[str, Any]:
    parameter_type = str(item.get("parameterType") or "").strip()
    required = bool(item.get("required")) or parameter_type.lower() == "required"
    default = item.get("defaultValue", item.get("default", item.get("value")))
    normalized: Dict[str, Any] = {
        "name": str(item.get("name") or "").strip(),
        "keyword": str(item.get("keyword") or item.get("name") or "").strip(),
        "display_name": str(item.get("displayName") or item.get("display_name") or "").strip(),
        "data_type": str(item.get("dataType") or item.get("type") or "").strip(),
        "direction": str(item.get("direction") or "").strip().lower(),
        "required": required,
    }
    if parameter_type:
        normalized["parameter_type"] = parameter_type
    if default is not None:
        normalized["default"] = default
    choices = _choice_list(item.get("choiceList") or item.get("choice_list"))
    if choices:
        normalized["choice_list"] = choices
    description = str(item.get("description") or "").strip()
    if description:
        normalized["description"] = description
    return normalized


def _dag_summary(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    parameters = payload.get("parameters") if isinstance(payload.get("parameters"), list) else []
    dag_param = next((item for item in parameters if item.get("name") == "DAG"), None)
    dag = dag_param.get("default") if isinstance(dag_param, dict) else None
    if not isinstance(dag, dict):
        return []

    summary: List[Dict[str, Any]] = []
    for node_id, node in dag.items():
        if not isinstance(node, dict):
            continue
        task_name = node.get("name")
        if isinstance(task_name, dict):
            task_name = task_name.get("base_class") or "<inline_task>"
        summary.append(
            {
                "node_id": str(node_id),
                "task_name": str(task_name or ""),
                "external_input": node.get("external_input") or {},
                "internal_input": node.get("internal_input") or {},
                "static_input": node.get("static_input") or {},
                "output": node.get("output") or {},
            }
        )
    return summary


def build_report(envi_root: Path, task_names: List[str]) -> Dict[str, Any]:
    tasks: List[Dict[str, Any]] = []
    missing: List[str] = []
    for task_name in task_names:
        raw = _read_task(envi_root, task_name)
        if not raw["ok"]:
            missing.append(task_name)
            tasks.append({"name": task_name, "available": False, "error": raw.get("error")})
            continue
        payload = raw["payload"] if isinstance(raw["payload"], dict) else {}
        parameters = payload.get("parameters") if isinstance(payload.get("parameters"), list) else []
        normalized_params = [
            _normalize_parameter(item)
            for item in parameters
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        tasks.append(
            {
                "name": str(payload.get("name") or task_name),
                "available": True,
                "path": raw["path"],
                "version": payload.get("version") or payload.get("revision"),
                "display_name": payload.get("displayName") or payload.get("display_name"),
                "base_class": payload.get("baseClass") or payload.get("base_class"),
                "parameter_count": len(normalized_params),
                "required_inputs": [
                    item["name"]
                    for item in normalized_params
                    if item.get("required") and item.get("direction") == "input"
                ],
                "outputs": [
                    item["name"]
                    for item in normalized_params
                    if item.get("direction") == "output"
                ],
                "parameters": normalized_params,
                "dag": _dag_summary(payload),
            }
        )

    return {
        "schema": "insar.sarscape-task-template-extract/v1",
        "generated_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "envi_root": str(envi_root),
        "task_count": len(tasks),
        "available_count": sum(1 for item in tasks if item.get("available")),
        "missing": missing,
        "tasks": tasks,
    }


def build_template(report: Dict[str, Any]) -> Dict[str, Any]:
    by_name = {str(item.get("name") or ""): item for item in report.get("tasks") or []}
    wf_sbas = by_name.get("wf_sbas") or {}
    stack_tasks = [by_name.get(name) or {"name": name, "available": False} for name in STACK_TASKS]
    return {
        "schema": "insar.sarscape-sbas-template/v1",
        "template_name": "SARscape SBAS native wf_sbas template skeleton",
        "sarscape_version_hint": "Extracted from installed ENVI/SARscape .task files",
        "validated": False,
        "execution_strategy": "native_workflow_metatask",
        "source_report_schema": report.get("schema"),
        "source_envi_root": report.get("envi_root"),
        "native_workflow": {
            "phase_id": "native_wf_sbas",
            "task_name": "wf_sbas",
            "source_task_file": wf_sbas.get("path"),
            "parameters": {
                "INPUT_FILE_LIST": "${scene_input_uris}",
                "SARSCAPE_PREFERENCE": "Use actual preferences",
                "DEM_SARSCAPEDATA": "${dem_sarscapedata}",
                "OUTPUT_FOLDER": "${output_root}",
                "GEOCODE_RG_GRID_SIZE": 10.0,
                "ESTIMATE_RESIDUAL_HEIGHT": True,
                "DISPLACEMENT_MODEL_TYPE": "linear",
            },
            "parameter_schema": wf_sbas.get("parameters") or [],
            "dag": wf_sbas.get("dag") or [],
        },
        "tasks": [
            {
                "phase_id": str(item.get("name") or ""),
                "task_name": str(item.get("name") or ""),
                "enabled": False,
                "source_task_file": item.get("path"),
                "required_inputs": item.get("required_inputs") or [],
                "outputs": item.get("outputs") or [],
                "parameter_schema": item.get("parameters") or [],
                "parameters": {},
            }
            for item in stack_tasks
        ],
    }


def main() -> int:
    args = _parse_args()
    envi_root = Path(args.envi_root)
    task_names = args.task or DEFAULT_TASKS
    report = build_report(envi_root, task_names)
    payload = build_template(report) if args.template else report
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
    if args.json or args.template or not args.output:
        print(text)
    return 0 if report.get("available_count") else 1


if __name__ == "__main__":
    raise SystemExit(main())
