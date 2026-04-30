from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings
from . import envi_service


PROCESSOR_CODE = "sarscape_sbas"
ENGINE_CODE = "sarscape"
PREPARED_STACK_SCHEMA = "insar.prepared-sbas-stack/v1"

NATIVE_WORKFLOW_TASK = "wf_sbas"
NATIVE_ESBAS_WORKFLOW_TASK = "wf_esbas"
TEMPLATE_STRATEGY_NATIVE = "native_workflow_metatask"
TEMPLATE_STRATEGY_EXPLICIT = "explicit_stack_tasks"
SUPPORTED_TEMPLATE_STRATEGIES = {
    TEMPLATE_STRATEGY_NATIVE,
    TEMPLATE_STRATEGY_EXPLICIT,
}

REQUIRED_STACK_TASKS = [
    "SARsInSARStackSBASGenerateConnectionGraph",
    "SARsInSARStackSBASInterferogramGeneration",
    "SARsInSARStackSBASInversionStep1",
    "SARsInSARStackSBASInversionStep2",
    "SARsInSARStackSBASGeocode",
]

REQUIRED_TASKS = REQUIRED_STACK_TASKS

OPTIONAL_TASKS = [
    NATIVE_WORKFLOW_TASK,
    NATIVE_ESBAS_WORKFLOW_TASK,
    "SARscape_setting_output_folders",
    "SARsLoadPreferences",
    "SARsImportSarSelector",
    "SARscapeSuggestLooks",
    "SARscapeEnviuriToShape",
    "SARsInSARStackSBASVariogram",
    "SARsInSARStackESBASInterferogramGeneration",
    "SARsInSARStackESBASInversion",
    "SARsInSARStackESBASGeocode",
    "SARsInSARConnectionGraphESBAS",
]

PIPELINE_PHASES = [
    {
        "phase_id": "connection_graph",
        "task_name": "SARsInSARStackSBASGenerateConnectionGraph",
        "purpose": "Build or ingest the SBAS connection graph.",
    },
    {
        "phase_id": "interferogram_generation",
        "task_name": "SARsInSARStackSBASInterferogramGeneration",
        "purpose": "Generate interferograms for the selected SBAS graph.",
    },
    {
        "phase_id": "inversion_step1",
        "task_name": "SARsInSARStackSBASInversionStep1",
        "purpose": "Run SARscape SBAS inversion step 1.",
    },
    {
        "phase_id": "inversion_step2",
        "task_name": "SARsInSARStackSBASInversionStep2",
        "purpose": "Run SARscape SBAS inversion step 2.",
    },
    {
        "phase_id": "geocode_export",
        "task_name": "SARsInSARStackSBASGeocode",
        "purpose": "Geocode velocity, displacement, and quality outputs.",
    },
    {
        "phase_id": "variogram_optional",
        "task_name": "SARsInSARStackSBASVariogram",
        "purpose": "Optional SARscape variogram/quality analysis.",
        "optional": True,
    },
]

REQUIRED_RESULT_ROLES = [
    "stack_manifest",
    "processor_manifest",
    "selected_network_edges",
    "velocity_product",
    "timeseries_product",
    "temporal_coherence",
    "geocoded_raster",
    "preview_png",
    "logs",
]


def default_parameter_template_path() -> str:
    configured = str(getattr(settings, "SARSCAPE_SBAS_PARAMETER_TEMPLATE_PATH", "") or "").strip()
    if configured:
        return configured
    return str(
        Path(__file__).resolve().parents[2]
        / "templates"
        / "sarscape_sbas_parameter_template.example.json"
    )


def _utcnow_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_payload(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _available_task_names(discovery_report: Optional[Dict[str, Any]]) -> set[str]:
    if not isinstance(discovery_report, dict):
        return set()
    names: set[str] = set()
    for item in discovery_report.get("tasks") or []:
        if isinstance(item, dict) and bool(item.get("available")):
            name = str(item.get("name") or "").strip()
            if name:
                names.add(name)
    discovered = discovery_report.get("discovery") or {}
    for name in discovered.get("sarscape_sbas_tasks") or []:
        text = str(name or "").strip()
        if text:
            names.add(text)
    return names


def _numeric_values(items: List[Dict[str, Any]], key: str) -> List[float]:
    values: List[float] = []
    for item in items:
        try:
            if item.get(key) is not None:
                values.append(float(item.get(key)))
        except Exception:
            continue
    return values


def load_parameter_template(parameter_template_path: Optional[str] = None) -> Dict[str, Any]:
    path = str(parameter_template_path or default_parameter_template_path() or "").strip()
    result: Dict[str, Any] = {
        "path": path or None,
        "exists": False,
        "readable": False,
        "schema": None,
        "validated": False,
        "execution_strategy": TEMPLATE_STRATEGY_NATIVE,
        "native_workflow_task": None,
        "task_count": 0,
        "missing_required_tasks": list(REQUIRED_STACK_TASKS),
        "tasks_without_parameters": [],
        "errors": [],
        "template": None,
    }
    if not path:
        result["errors"].append("SARscape SBAS parameter template path is empty.")
        return result
    template_file = Path(path)
    result["exists"] = template_file.is_file()
    if not template_file.is_file():
        result["errors"].append(f"SARscape SBAS parameter template not found: {path}")
        return result
    try:
        payload = json.loads(template_file.read_text(encoding="utf-8"))
    except Exception as exc:
        result["errors"].append(f"Failed to read SARscape SBAS parameter template: {exc}")
        return result

    if not isinstance(payload, dict):
        result["errors"].append("SARscape SBAS parameter template must be a JSON object.")
        return result

    raw_strategy = str(payload.get("execution_strategy") or TEMPLATE_STRATEGY_NATIVE).strip()
    execution_strategy = (
        raw_strategy if raw_strategy in SUPPORTED_TEMPLATE_STRATEGIES else TEMPLATE_STRATEGY_NATIVE
    )
    native_workflow = payload.get("native_workflow") if isinstance(payload.get("native_workflow"), dict) else {}
    native_workflow_task = str(native_workflow.get("task_name") or NATIVE_WORKFLOW_TASK).strip()
    native_workflow_parameters = native_workflow.get("parameters")
    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    task_names = {
        str(item.get("task_name") or "").strip()
        for item in tasks
        if isinstance(item, dict) and str(item.get("task_name") or "").strip()
    }
    missing_required = [name for name in REQUIRED_STACK_TASKS if name not in task_names]
    tasks_without_parameters = [
        str(item.get("task_name") or item.get("phase_id") or "<unnamed>")
        for item in tasks
        if isinstance(item, dict)
        and bool(item.get("enabled", True))
        and not isinstance(item.get("parameters"), dict)
    ]
    result.update(
        {
            "readable": True,
            "schema": payload.get("schema"),
            "validated": bool(payload.get("validated")),
            "execution_strategy": execution_strategy,
            "native_workflow_task": native_workflow_task,
            "task_count": len(tasks),
            "missing_required_tasks": missing_required,
            "tasks_without_parameters": tasks_without_parameters,
            "template": payload,
        }
    )
    if str(payload.get("schema") or "") != "insar.sarscape-sbas-template/v1":
        result["errors"].append("Unsupported SARscape SBAS parameter template schema.")
    if raw_strategy not in SUPPORTED_TEMPLATE_STRATEGIES:
        result["errors"].append(
            "Unsupported SARscape SBAS execution_strategy: " + (raw_strategy or "<empty>")
        )
    if execution_strategy == TEMPLATE_STRATEGY_NATIVE:
        if not native_workflow_task:
            result["errors"].append("Native SARscape workflow task name is empty.")
        if not isinstance(native_workflow_parameters, dict):
            result["errors"].append("Native SARscape workflow parameters must be a JSON object.")
    if execution_strategy == TEMPLATE_STRATEGY_EXPLICIT and missing_required:
        result["errors"].append("Template is missing required tasks: " + ", ".join(missing_required))
    if execution_strategy == TEMPLATE_STRATEGY_EXPLICIT and tasks_without_parameters:
        result["errors"].append("Template tasks without parameters object: " + ", ".join(tasks_without_parameters))
    if not payload.get("validated"):
        result["errors"].append("Template is not marked validated=true.")
    return result


def summarize_network_edges(network_edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    enabled_edges = [item for item in network_edges if bool(item.get("enabled", True))]
    temporal = _numeric_values(enabled_edges, "temporal_baseline_days")
    spatial = _numeric_values(enabled_edges, "spatial_baseline_meters")
    overlap = _numeric_values(enabled_edges, "pair_aoi_overlap_ratio")
    return {
        "edge_count": len(network_edges),
        "enabled_edge_count": len(enabled_edges),
        "temporal_baseline_days": {
            "min": min(temporal) if temporal else None,
            "max": max(temporal) if temporal else None,
        },
        "spatial_baseline_meters": {
            "min": min(spatial) if spatial else None,
            "max": max(spatial) if spatial else None,
        },
        "pair_aoi_overlap_ratio": {
            "min": min(overlap) if overlap else None,
            "max": max(overlap) if overlap else None,
        },
    }


def build_processor_manifest(
    stack_manifest: Dict[str, Any],
    *,
    discovery_report: Optional[Dict[str, Any]] = None,
    parameter_template_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the SARscape SBAS processor contract without executing ENVI tasks."""
    scenes = stack_manifest.get("scenes") if isinstance(stack_manifest.get("scenes"), list) else []
    network_edges = (
        stack_manifest.get("network_edges")
        if isinstance(stack_manifest.get("network_edges"), list)
        else []
    )
    template_status = load_parameter_template(parameter_template_path)
    template = template_status.get("template") if isinstance(template_status.get("template"), dict) else {}
    template_strategy = str(
        template_status.get("execution_strategy") or TEMPLATE_STRATEGY_NATIVE
    ).strip()
    native_workflow_task = str(
        template_status.get("native_workflow_task") or NATIVE_WORKFLOW_TASK
    ).strip()
    available_tasks = _available_task_names(discovery_report)
    missing_stack_tasks = [name for name in REQUIRED_STACK_TASKS if name not in available_tasks]
    missing_native_tasks = [native_workflow_task] if native_workflow_task not in available_tasks else []
    missing_required_tasks = (
        missing_native_tasks
        if template_strategy == TEMPLATE_STRATEGY_NATIVE
        else missing_stack_tasks
    )
    template_path = str(template_status.get("path") or "").strip()
    template_exists = bool(template_status.get("exists"))
    template_validated = bool(template_status.get("validated")) and not template_status.get("errors")
    execution_enabled = bool(getattr(settings, "SARSCAPE_SBAS_ALLOW_EXECUTION", False))
    native_workflow_available = not missing_native_tasks
    explicit_stack_available = not missing_stack_tasks

    blockers: List[str] = []
    if len(scenes) < 3:
        blockers.append("SARscape SBAS requires at least 3 stack scenes.")
    if not network_edges:
        blockers.append("No SBAS network_edges are present in the stack manifest.")
    if missing_required_tasks:
        blockers.append(
            "Missing required SARscape SBAS tasks for "
            f"{template_strategy}: " + ", ".join(missing_required_tasks)
        )
    if not template_exists:
        blockers.append(
            "SARscape SBAS parameter template is not configured. "
            "Live task.parameters is intentionally not used because it can hang taskengine."
        )
    elif not template_validated:
        blockers.extend(str(item) for item in (template_status.get("errors") or []))
    if not execution_enabled:
        blockers.append("SARSCAPE_SBAS_ALLOW_EXECUTION is false; SARscape SBAS production execution is disabled.")

    parameter_template_state = (
        "validated"
        if template_validated
        else ("configured_unvalidated" if template_exists else "required")
    )
    task_sequence = [
        {
            "phase_id": "native_wf_sbas",
            "task_name": native_workflow_task,
            "purpose": "Run SARscape's installed end-to-end SBAS metatask.",
            "available": native_workflow_available,
            "required": template_strategy == TEMPLATE_STRATEGY_NATIVE,
            "parameter_template_status": parameter_template_state,
            "has_template_parameters": isinstance(
                (template.get("native_workflow") or {}).get("parameters")
                if isinstance(template.get("native_workflow"), dict)
                else None,
                dict,
            ),
            "supports_system_selected_edges": False,
            "ready": (
                native_workflow_available
                and template_strategy == TEMPLATE_STRATEGY_NATIVE
                and template_validated
                and execution_enabled
            ),
        }
    ]
    template_tasks = {
        str(item.get("task_name") or "").strip(): item
        for item in (template.get("tasks") or [])
        if isinstance(item, dict)
    }
    for phase in PIPELINE_PHASES:
        task_name = str(phase["task_name"])
        optional = bool(phase.get("optional", False))
        template_task = template_tasks.get(task_name) or {}
        has_template_parameters = isinstance(template_task.get("parameters"), dict)
        task_sequence.append(
            {
                **phase,
                "available": task_name in available_tasks,
                "required": not optional,
                "template_phase_id": template_task.get("phase_id"),
                "parameter_template_status": parameter_template_state,
                "has_template_parameters": has_template_parameters,
                "ready": (
                    (task_name in available_tasks or optional)
                    and template_strategy == TEMPLATE_STRATEGY_EXPLICIT
                    and template_validated
                    and execution_enabled
                ),
            }
        )

    return {
        "schema": "insar.sarscape-sbas-processor/v1",
        "created_at_utc": _utcnow_iso(),
        "engine_code": ENGINE_CODE,
        "processor_code": PROCESSOR_CODE,
        "execution_enabled": execution_enabled,
        "ready_for_pipeline_design": bool(discovery_report and discovery_report.get("ok")),
        "ready_for_execution": not blockers,
        "blockers": blockers,
        "stack_manifest_checksum": _sha256_payload(stack_manifest),
        "stack_manifest_summary": {
            "schema": stack_manifest.get("schema"),
            "prepared_stack_schema": stack_manifest.get("prepared_stack_schema"),
            "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
            "manifest_role": stack_manifest.get("manifest_role"),
            "batch_id": stack_manifest.get("batch_id"),
            "plan_id": stack_manifest.get("plan_id"),
            "plan_strategy": stack_manifest.get("plan_strategy"),
            "reference_date": stack_manifest.get("reference_date"),
            "scene_count": len(scenes),
            "stack_key": stack_manifest.get("stack_key"),
            "group_key": stack_manifest.get("group_key"),
        },
        "network_summary": summarize_network_edges(network_edges),
        "execution_strategy": template_strategy,
        "execution_strategies": {
            TEMPLATE_STRATEGY_NATIVE: {
                "preferred": template_strategy == TEMPLATE_STRATEGY_NATIVE,
                "task_name": native_workflow_task,
                "available": native_workflow_available,
                "required_tasks": [native_workflow_task],
                "missing_tasks": missing_native_tasks,
                "supports_system_selected_edges": False,
                "graph_policy": "SARscape wf_sbas builds the connection graph internally; system network_edges are retained for audit and comparison.",
            },
            TEMPLATE_STRATEGY_EXPLICIT: {
                "preferred": template_strategy == TEMPLATE_STRATEGY_EXPLICIT,
                "available": explicit_stack_available,
                "required_tasks": list(REQUIRED_STACK_TASKS),
                "missing_tasks": missing_stack_tasks,
                "supports_system_selected_edges": "not_verified",
                "graph_policy": "Explicit task chaining can expose the connection graph step, but direct injection of the system-selected edge list still needs SARscape parameter validation.",
            },
        },
        "required_tasks": [native_workflow_task] if template_strategy == TEMPLATE_STRATEGY_NATIVE else list(REQUIRED_STACK_TASKS),
        "required_stack_tasks": list(REQUIRED_STACK_TASKS),
        "optional_tasks": list(OPTIONAL_TASKS),
        "available_tasks": sorted(available_tasks),
        "missing_required_tasks": missing_required_tasks,
        "parameter_template": {
            "path": template_path or None,
            "exists": template_exists,
            "readable": bool(template_status.get("readable")),
            "validated": bool(template_status.get("validated")),
            "execution_strategy": template_strategy,
            "native_workflow_task": native_workflow_task,
            "task_count": int(template_status.get("task_count") or 0),
            "errors": template_status.get("errors") or [],
            "source": "manual_sarscape_template",
        },
        "task_sequence": task_sequence,
        "input_contract": {
            "required_manifest_role_for_execution": "prepared_sbas_stack",
            "prepared_stack_schema": PREPARED_STACK_SCHEMA,
            "production_input_policy": "prepared_stack_manifest_only",
            "scene_path_fields": ["folder_path", "tiff_path", "meta_path"],
            "network_edge_source": "stack_manifest.network_edges",
            "dem_source": "IDL_DINSAR_DEM_BASE_FILE",
            "orbit_source": "ORBIT_POOL_ENVI",
        },
        "result_contract": {
            "catalog_name": "psinsar",
            "required_roles": list(REQUIRED_RESULT_ROLES),
            "publish_manifest_schema": "psinsar.publish.v2",
        },
        "notes": [
            "This manifest is a planning contract only; it does not execute SARscape tasks.",
            "Execution must use checked-in SARscape parameter templates, not live task.parameters.",
            "The native wf_sbas strategy is the preferred first integration path on this workstation.",
        ],
    }


def build_preflight_report(
    stack_manifest: Dict[str, Any],
    *,
    include_task_discovery: bool = True,
    discovery_timeout_seconds: int = 120,
    parameter_template_path: Optional[str] = None,
) -> Dict[str, Any]:
    status = envi_service.get_status()
    discovery_report: Optional[Dict[str, Any]] = None
    if include_task_discovery:
        discovery_report = envi_service.inspect_sarscape_sbas_tasks_subprocess(
            timeout_seconds=discovery_timeout_seconds,
            include_parameters=False,
        )
    processor_manifest = build_processor_manifest(
        stack_manifest,
        discovery_report=discovery_report,
        parameter_template_path=parameter_template_path,
    )
    env_blockers: List[str] = []
    if not status.get("idl_installed"):
        env_blockers.append("IDL/ENVI executable is not installed or not configured.")
    if not status.get("runner_ready"):
        env_blockers.append("ENVI runner is not ready: " + str(status.get("runner_message") or "unknown"))
    if not status.get("dem_exists"):
        env_blockers.append("SARscape DEM base file is missing: " + str(status.get("dem_base_file") or ""))
    if discovery_report is not None and not discovery_report.get("ok"):
        env_blockers.append("SARscape SBAS task discovery failed: " + str(discovery_report.get("error") or "unknown"))

    all_blockers = [*env_blockers, *(processor_manifest.get("blockers") or [])]
    return {
        "schema": "insar.sarscape-sbas-preflight/v1",
        "created_at_utc": _utcnow_iso(),
        "engine_code": ENGINE_CODE,
        "processor_code": PROCESSOR_CODE,
        "ready_for_pipeline_design": bool(
            status.get("idl_installed")
            and status.get("runner_ready")
            and (discovery_report is None or discovery_report.get("ok"))
        ),
        "ready_for_execution": not all_blockers,
        "blockers": all_blockers,
        "environment": status,
        "task_discovery": discovery_report,
        "processor_manifest": processor_manifest,
    }


def _resolve_template_value(value: Any, context: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text in context:
            return context[text]
        resolved = value
        for key, replacement in context.items():
            if key in resolved and isinstance(replacement, (str, int, float, bool)):
                resolved = resolved.replace(key, str(replacement))
        return resolved
    if isinstance(value, list):
        return [_resolve_template_value(item, context) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _resolve_template_value(item, context)
            for key, item in value.items()
        }
    return value


def _scene_input_uris(scenes: List[Dict[str, Any]]) -> List[str]:
    uris: List[str] = []
    for item in scenes:
        for key in ("meta_path", "tiff_path", "folder_path"):
            text = str(item.get(key) or "").strip()
            if text:
                uris.append(text)
                break
    return uris


def execute_template_workflow(
    stack_manifest: Dict[str, Any],
    *,
    work_root: str,
    selected_manifest_path: str,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Execute a validated SARscape SBAS template.

    This path is intentionally gated by SARSCAPE_SBAS_ALLOW_EXECUTION and
    template validated=true. The default checked-in template is not executable.
    """
    if stack_manifest.get("prepared_stack_schema") != PREPARED_STACK_SCHEMA:
        raise ValueError(
            f"SARscape SBAS execution requires a prepared stack manifest ({PREPARED_STACK_SCHEMA})."
        )
    if not str(stack_manifest.get("prepared_stack_id") or "").strip():
        raise ValueError("SARscape SBAS execution requires prepared_stack_id.")

    discovery_report = envi_service.inspect_sarscape_sbas_tasks_subprocess(
        timeout_seconds=int(getattr(settings, "SARSCAPE_SBAS_DISCOVERY_TIMEOUT_SECONDS", 120) or 120),
        include_parameters=False,
    )
    processor_manifest = build_processor_manifest(
        stack_manifest,
        discovery_report=discovery_report,
    )
    if not processor_manifest.get("ready_for_execution"):
        blockers = "; ".join(str(item) for item in (processor_manifest.get("blockers") or []))
        raise ValueError("SARscape SBAS execution is not ready: " + (blockers or "unknown blocker"))

    template_status = load_parameter_template()
    template = template_status.get("template") if isinstance(template_status.get("template"), dict) else {}
    tasks = [item for item in (template.get("tasks") or []) if isinstance(item, dict)]
    output_root = Path(work_root) / "sarscape_sbas"
    output_root.mkdir(parents=True, exist_ok=True)
    artifacts = stack_manifest.get("artifacts") if isinstance(stack_manifest.get("artifacts"), dict) else {}
    prepared_edges_path = str(artifacts.get("selected_network_edges_path_windows") or "").strip()
    if prepared_edges_path:
        network_edges_path = Path(prepared_edges_path)
        if not network_edges_path.is_file():
            raise FileNotFoundError(f"Prepared selected_network_edges.json not found: {network_edges_path}")
    else:
        network_edges_path = output_root / "selected_network_edges.json"
        network_edges_path.write_text(
            json.dumps(stack_manifest.get("network_edges") or [], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    scenes = stack_manifest.get("scenes") if isinstance(stack_manifest.get("scenes"), list) else []
    context: Dict[str, Any] = {
        "${work_root}": str(work_root),
        "${output_root}": str(output_root),
        "${selected_stack_manifest}": str(selected_manifest_path),
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

    executed: List[Dict[str, Any]] = []
    previous_outputs: Dict[str, Any] = {}
    execution_strategy = str(template.get("execution_strategy") or TEMPLATE_STRATEGY_NATIVE).strip()
    if execution_strategy not in SUPPORTED_TEMPLATE_STRATEGIES:
        execution_strategy = TEMPLATE_STRATEGY_NATIVE
    if execution_strategy == TEMPLATE_STRATEGY_NATIVE:
        native_workflow = template.get("native_workflow") if isinstance(template.get("native_workflow"), dict) else {}
        task_name = str(native_workflow.get("task_name") or NATIVE_WORKFLOW_TASK).strip()
        if not task_name:
            raise ValueError("Native SARscape SBAS workflow task_name is empty.")
        phase_id = str(native_workflow.get("phase_id") or "native_wf_sbas").strip()
        phase_output_dir = output_root / phase_id
        phase_output_dir.mkdir(parents=True, exist_ok=True)
        phase_context = {
            **context,
            "${phase_id}": phase_id,
            "${phase_output_dir}": str(phase_output_dir),
            "${previous_outputs}": previous_outputs,
        }
        parameters = _resolve_template_value(native_workflow.get("parameters") or {}, phase_context)
        result = envi_service.execute_envi_task(task_name, parameters)
        previous_outputs[phase_id] = result
        executed.append(
            {
                "phase_id": phase_id,
                "task_name": task_name,
                "output_dir": str(phase_output_dir),
                "output_keys": sorted((result or {}).keys()) if isinstance(result, dict) else [],
            }
        )
        tasks = []

    for item in tasks:
        if not bool(item.get("enabled", True)):
            continue
        task_name = str(item.get("task_name") or "").strip()
        phase_id = str(item.get("phase_id") or task_name).strip()
        if not task_name:
            raise ValueError(f"SARscape template task is missing task_name: {phase_id}")
        phase_output_dir = output_root / phase_id
        phase_output_dir.mkdir(parents=True, exist_ok=True)
        phase_context = {
            **context,
            "${phase_id}": phase_id,
            "${phase_output_dir}": str(phase_output_dir),
            "${previous_outputs}": previous_outputs,
        }
        parameters = _resolve_template_value(item.get("parameters") or {}, phase_context)
        result = envi_service.execute_envi_task(task_name, parameters)
        previous_outputs[phase_id] = result
        executed.append(
            {
                "phase_id": phase_id,
                "task_name": task_name,
                "output_dir": str(phase_output_dir),
                "output_keys": sorted((result or {}).keys()) if isinstance(result, dict) else [],
            }
        )

    return {
        "schema": "insar.sarscape-sbas-execution/v1",
        "created_at_utc": _utcnow_iso(),
        "processor_code": PROCESSOR_CODE,
        "execution_strategy": execution_strategy,
        "prepared_stack_id": stack_manifest.get("prepared_stack_id"),
        "work_root": str(work_root),
        "output_root": str(output_root),
        "selected_network_edges_path": str(network_edges_path),
        "task_count": len(executed),
        "executed_tasks": executed,
        "processor_manifest": processor_manifest,
    }


def write_processor_manifest(path: str | Path, manifest: Dict[str, Any]) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)
