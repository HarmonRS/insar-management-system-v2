"""ENVI/SARscape integration service via envipyengine.

Single execution engine: envipyengine → taskengine.exe subprocess.
Provides Import and D-InSAR workflows with smart chaining
(D-InSAR auto-detects missing imports and runs them first).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import defusedxml.ElementTree as ET
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from glob import glob
from typing import Any, Dict, List, Optional

from ..config import get_env_text, settings
from ..process_utils import is_any_process_running
from .dinsar_naming import (
    PAIR_META_FILENAME,
    build_fallback_pair_key,
    build_run_key,
    find_json_sidecar,
    write_run_metadata,
)
from .dinsar_result_layout_service import get_run_native_output_dir

_BACKEND_DIR = type(settings).BACKEND_DIR


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _read_env(name: str, default: Optional[str] = None) -> Optional[str]:
    return get_env_text(name, default)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _normalize_path(value: Any) -> str:
    """Normalize to forward-slash path (ENVI convention)."""
    normalized = str(value or "").strip().strip('"').strip("'")
    if not normalized:
        return ""
    return normalized.replace("\\", "/")


def _to_local_path(value: Any) -> str:
    """Normalize to OS-native path."""
    raw = str(value or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    return os.path.normpath(raw.replace("/", os.sep))


def get_envi_runner_python() -> str:
    configured = _to_local_path(getattr(settings, "PYTHON_PATH", "") or "")
    if configured:
        return configured
    return os.path.normpath(sys.executable)


def get_envi_taskengine_cwd() -> str:
    """Dedicated cwd for envipyengine/taskengine temp files.

    SARscape can create zero-byte env_*.xyz and IDL*.tmp files in the current
    working directory. Keep those files under runtime instead of the repo root.
    """
    base_dir = _to_local_path(
        getattr(settings, "IDL_WORKER_RUNTIME_DIR", "")
        or os.path.join(_BACKEND_DIR, "runtime", "idl_worker")
    )
    cwd = os.path.join(base_dir, "envi_cwd")
    os.makedirs(cwd, exist_ok=True)
    return os.path.normpath(os.path.abspath(cwd))


def get_envi_custom_code_dir() -> str:
    envi_root = _envi_install_root()
    if not envi_root:
        return ""
    candidates = [
        os.path.join(envi_root, "user_custom_code"),
        os.path.join(envi_root, "custom_code"),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return os.path.normpath(os.path.abspath(path))
    return ""


def get_envi_runner_cwd() -> str:
    return get_envi_taskengine_cwd()


def get_envi_runner_env() -> Dict[str, str]:
    env = os.environ.copy()
    project_root = os.path.normpath(os.path.abspath(type(settings).PROJECT_ROOT))
    taskengine_cwd = get_envi_taskengine_cwd()
    existing = [part for part in str(env.get("PYTHONPATH") or "").split(os.pathsep) if str(part).strip()]
    ordered = [project_root, *existing]
    deduped: List[str] = []
    seen = set()
    for raw_path in ordered:
        try:
            key = os.path.normcase(os.path.normpath(os.path.abspath(str(raw_path))))
        except Exception:
            key = str(raw_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(str(raw_path))
    env["PYTHONPATH"] = os.pathsep.join(deduped)
    env["TEMP"] = taskengine_cwd
    env["TMP"] = taskengine_cwd
    env["IDL_TMPDIR"] = taskengine_cwd
    custom_code_dir = get_envi_custom_code_dir()
    if custom_code_dir:
        env["ENVI_CUSTOM_CODE"] = custom_code_dir
    return env


@contextmanager
def _envi_taskengine_runtime_context():
    """Run in-process ENVI calls from the dedicated runtime cwd."""
    target_cwd = get_envi_taskengine_cwd()
    old_cwd = os.getcwd()
    old_env = {name: os.environ.get(name) for name in ("TEMP", "TMP", "IDL_TMPDIR")}
    os.environ["TEMP"] = target_cwd
    os.environ["TMP"] = target_cwd
    os.environ["IDL_TMPDIR"] = target_cwd
    try:
        os.chdir(target_cwd)
        yield target_cwd
    finally:
        os.chdir(old_cwd)
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def build_envi_runner_command(*args: Any) -> List[str]:
    command = [
        get_envi_runner_python(),
        "-m",
        "backend.app.services.envi_runner_cli",
    ]
    command.extend(str(arg) for arg in args if arg is not None)
    return command


def _list_taskengine_pids() -> set[int]:
    """Return taskengine.exe PIDs on Windows without importing optional deps."""
    if os.name != "nt":
        return set()
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-Process taskengine -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return set()

    pids: set[int] = set()
    for line in str(completed.stdout or "").splitlines():
        raw = line.strip()
        if raw.isdigit():
            pids.add(int(raw))
    return pids


def _stop_taskengine_pids(pids: set[int]) -> List[int]:
    """Stop specific taskengine.exe PIDs; avoids killing pre-existing sessions."""
    stopped: List[int] = []
    if os.name != "nt":
        return stopped
    for pid in sorted(pids):
        try:
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            stopped.append(int(pid))
        except Exception:
            continue
    return stopped


def _cleanup_new_taskengine_processes(existing_pids: set[int]) -> Dict[str, Any]:
    """Best-effort cleanup for taskengine.exe children spawned by a timed-out runner."""
    existing = set(existing_pids or set())
    first_targets = _list_taskengine_pids() - existing
    stopped = _stop_taskengine_pids(first_targets)

    # taskengine can take a moment to detach from the runner process. Re-check once.
    time.sleep(1)
    second_targets = _list_taskengine_pids() - existing
    stopped.extend(pid for pid in _stop_taskengine_pids(second_targets) if pid not in stopped)

    time.sleep(1)
    remaining = sorted(_list_taskengine_pids() - existing)
    return {
        "taskengine_cleanup_attempted": True,
        "taskengine_stopped_pids": sorted(set(stopped)),
        "taskengine_remaining_new_pids": remaining,
    }


def probe_envi_runner() -> Dict[str, Any]:
    python_path = get_envi_runner_python()
    project_root = get_envi_runner_cwd()
    result: Dict[str, Any] = {
        "python_path": python_path,
        "cwd": project_root,
        "ready": False,
        "returncode": None,
        "message": "",
    }
    if not python_path:
        result["message"] = "PYTHON_PATH is empty."
        return result
    if not os.path.isfile(python_path):
        result["message"] = f"Python executable not found: {python_path}"
        return result
    if not os.path.isdir(project_root):
        result["message"] = f"Project root not found: {project_root}"
        return result

    try:
        completed = subprocess.run(
            build_envi_runner_command("--help"),
            cwd=project_root,
            env=get_envi_runner_env(),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception as exc:
        result["message"] = str(exc)
        return result

    result["returncode"] = int(completed.returncode)
    if completed.returncode == 0:
        result["ready"] = True
        result["message"] = "Runner entrypoint is available."
        return result

    stderr_text = str(completed.stderr or "").strip()
    stdout_text = str(completed.stdout or "").strip()
    result["message"] = (stderr_text or stdout_text or f"returncode={completed.returncode}")[:1000]
    return result


# ---------------------------------------------------------------------------
# Configuration (read once at import time)
# ---------------------------------------------------------------------------

IDL_EXECUTABLE = _read_env(
    "IDL_EXECUTABLE",
    r"C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idl.exe",
)
IDL_WORKBENCH_PATH = _read_env(
    "IDL_WORKBENCH_PATH",
    r"C:\Program Files\Harris\ENVI56\IDL88\bin\bin.x86_64\idlde.exe",
)

# ---------------------------------------------------------------------------
# envipyengine config bootstrap
# ---------------------------------------------------------------------------
# envipyengine reads taskengine path from %LOCALAPPDATA%\envipyengine\settings.cfg.
# To avoid per-machine manual setup, we redirect LOCALAPPDATA to the project's
# runtime dir and auto-generate settings.cfg from IDL_EXECUTABLE at first use.

_ENVIPY_CONFIG_BASE = os.path.join(_BACKEND_DIR, "runtime")
_TASKENGINE_EXE = os.path.join(os.path.dirname(IDL_EXECUTABLE), "taskengine.exe") if IDL_EXECUTABLE else ""


def _ensure_envipyengine_config() -> None:
    """Write envipyengine settings.cfg into the project runtime dir and
    redirect LOCALAPPDATA so envipyengine picks it up automatically.
    Safe to call multiple times (no-op if already configured correctly).
    """
    if not _TASKENGINE_EXE:
        return
    cfg_dir = os.path.join(_ENVIPY_CONFIG_BASE, "envipyengine")
    cfg_path = os.path.join(cfg_dir, "settings.cfg")
    expected = (
        f"[envipyengine]\n"
        f"engine = {_TASKENGINE_EXE}\n\n"
        f"[engine-environment]\n"
    )
    try:
        with open(cfg_path, "r", encoding="utf-8") as _cfg_f:
            if _cfg_f.read() == expected:
                os.environ["LOCALAPPDATA"] = _ENVIPY_CONFIG_BASE
                return
    except OSError as exc:
        print(f"[WARN] _ensure_config: {exc}")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(expected)
    os.environ["LOCALAPPDATA"] = _ENVIPY_CONFIG_BASE
DEM_BASE_FILE = _to_local_path(
    _read_env("IDL_DINSAR_DEM_BASE_FILE", "")
)
RUNTIME_DIR = _read_env(
    "IDL_WORKER_RUNTIME_DIR",
    os.path.join(_BACKEND_DIR, "runtime", "idl_worker"),
)
DEFAULT_TIMEOUT = int(_read_env("IDL_WORKER_DEFAULT_TIMEOUT_SECONDS", "14400") or 14400)
MAX_TIMEOUT = int(_read_env("IDL_WORKER_MAX_TIMEOUT_SECONDS", "43200") or 43200)

# Custom D-InSAR parameters (step-by-step mode)
CUSTOM_TARGET_RESOLUTION_M = float(
    _read_env("IDL_DINSAR_CUSTOM_TARGET_RESOLUTION_M", "10.0") or 10.0
)
CUSTOM_FILTER_METHOD = _read_env("IDL_DINSAR_CUSTOM_FILTER_METHOD", "GOLDSTEIN")
CUSTOM_UNWRAP_COH_THRESHOLD = float(
    _read_env("IDL_DINSAR_CUSTOM_UNWRAP_COH_THRESHOLD", "0.05") or 0.05
)
CUSTOM_GCP_COH_THRESHOLD = float(
    _read_env("IDL_DINSAR_CUSTOM_GCP_COH_THRESHOLD", "0.7") or 0.7
)
CUSTOM_GCP_NUMBER = int(
    _read_env("IDL_DINSAR_CUSTOM_GCP_NUMBER", "100") or 100
)
CUSTOM_GEOCODING_COH_THRESHOLD = float(
    _read_env("IDL_DINSAR_CUSTOM_GEOCODING_COH_THRESHOLD", "0.0") or 0.0
)
CUSTOM_GEOCODING_PIXEL_SIZE_M = float(
    _read_env("IDL_DINSAR_CUSTOM_GEOCODING_PIXEL_SIZE_M", "10.0") or 10.0
)

# Timeout for individual envipyengine task.execute() calls (seconds).
# If envipyengine hangs after the task completes, this ensures we don't
# block forever. The caller's except block will use fallback file scanning.
_ENVI_TASK_TIMEOUT = int(_read_env("ENVI_TASK_TIMEOUT_SECONDS", "300") or 300)

# Global mutex: taskengine.exe is a singleton process and cannot handle
# concurrent calls. All execute_envi_task() calls must be serialized.
import threading
_ENVI_GLOBAL_LOCK = threading.Lock()


SARSCAPE_SBAS_NATIVE_WORKFLOW_CANDIDATES = [
    "wf_sbas",
    "wf_esbas",
]

SARSCAPE_SBAS_SUPPORT_TASK_CANDIDATES = [
    "SARscape_setting_output_folders",
    "SARsLoadPreferences",
    "SARsImportSarSelector",
    "SARscapeSuggestLooks",
    "SARscapeEnviuriToShape",
]

SARSCAPE_SBAS_STACK_TASK_CANDIDATES = [
    "SARsInSARStackSBASGenerateConnectionGraph",
    "SARsInSARStackSBASInterferogramGeneration",
    "SARsInSARStackSBASInversionStep1",
    "SARsInSARStackSBASInversionStep2",
    "SARsInSARStackSBASGeocode",
    "SARsInSARStackSBASVariogram",
    "SARsInSARStackESBASInterferogramGeneration",
    "SARsInSARStackESBASInversion",
    "SARsInSARStackESBASGeocode",
    "SARsInSARConnectionGraphESBAS",
]

SARSCAPE_SBAS_TASK_CANDIDATES = [
    *SARSCAPE_SBAS_NATIVE_WORKFLOW_CANDIDATES,
    *SARSCAPE_SBAS_SUPPORT_TASK_CANDIDATES,
    *SARSCAPE_SBAS_STACK_TASK_CANDIDATES,
]


# ---------------------------------------------------------------------------
# Progress file for subprocess ↔ job handler communication
# ---------------------------------------------------------------------------

def _progress_file_path(job_id: str) -> str:
    """Return the path to the progress JSON file for a given job."""
    return os.path.join(RUNTIME_DIR, f"job_{job_id}_progress.json")


def _write_progress(
    job_id: Optional[str],
    step: int,
    total_steps: int,
    message: str,
    output_dir: str = "",
    pair_index: int = 0,
    total_pairs: int = 0,
    pair_name: str = "",
) -> None:
    """Write a progress JSON file (atomic via tmp+rename).

    The job handler monitors this file's mtime to determine liveness.
    pair_index/total_pairs track which pair is being processed in a batch.
    """
    if not job_id:
        return
    try:
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        progress_file = _progress_file_path(job_id)
        data = {
            "step": step,
            "total_steps": total_steps,
            "message": message,
            "output_dir": output_dir,
            "pair_index": pair_index,
            "total_pairs": total_pairs,
            "pair_name": pair_name,
            "timestamp": time.time(),
        }
        tmp = progress_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, progress_file)
    except Exception as exc:
        print(f"[WARN] _write_progress: {exc}")


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def _collect_task_folders(root_dir: str) -> List[str]:
    """Return sorted list of Task_* directories under root_dir."""
    pattern = os.path.join(root_dir, "Task_*")
    return sorted(p for p in glob(pattern) if os.path.isdir(p))


def _find_meta_files(data_dir: str) -> List[str]:
    return sorted(glob(os.path.join(data_dir, "*.meta.xml")))


def _has_sml(data_dir: str) -> bool:
    return bool(glob(os.path.join(data_dir, "*.sml")))


def _first_sml_base(data_dir: str) -> Optional[str]:
    """Return base path (without .sml) of the first .sml file, or None."""
    smls = sorted(glob(os.path.join(data_dir, "*.sml")))
    return smls[0][:-4] if smls else None


def _utc_now_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _resolve_dinsar_pair_identity(task_dir: str, task_name: str) -> tuple[str, str, Dict[str, Any]]:
    pair_meta = find_json_sidecar(task_dir, PAIR_META_FILENAME, max_levels=0) or {}
    task_alias = str(pair_meta.get("task_alias") or task_name).strip() or task_name
    pair_key = str(pair_meta.get("pair_key") or "").strip() or build_fallback_pair_key(task_alias, task_dir)
    return task_alias, pair_key, pair_meta


def _write_envi_run_sidecar(
    output_dir: str,
    *,
    engine_code: str,
    profile_code: str,
    root_dir: str,
    task_dir: str,
    task_name: str,
    task_alias: str,
    pair_key: str,
    run_key: str,
    pair_meta: Dict[str, Any],
    started_at: str,
    params: Dict[str, Any],
    metrics: Dict[str, Any],
    native_output_dir: Optional[str] = None,
) -> None:
    write_run_metadata(
        output_dir,
        {
            "run_key": run_key,
            "pair_key": pair_key,
            "task_name": task_name,
            "task_alias": task_alias,
            "engine_code": engine_code,
            "profile_code": profile_code,
            "source_root": os.path.normpath(root_dir),
            "task_dir": os.path.normpath(task_dir),
            "output_dir": os.path.normpath(output_dir),
            "native_output_dir": os.path.normpath(native_output_dir or output_dir),
            "started_at": started_at,
            "finished_at": _utc_now_text(),
            "params": params,
            "metrics": metrics,
            "master_path": pair_meta.get("master_path"),
            "slave_path": pair_meta.get("slave_path"),
            "master_satellite": pair_meta.get("master_satellite"),
            "slave_satellite": pair_meta.get("slave_satellite"),
            "master_imaging_date": pair_meta.get("master_imaging_date"),
            "slave_imaging_date": pair_meta.get("slave_imaging_date"),
            "master_imaging_mode": pair_meta.get("master_imaging_mode"),
            "slave_imaging_mode": pair_meta.get("slave_imaging_mode"),
            "master_polarization": pair_meta.get("master_polarization"),
            "slave_polarization": pair_meta.get("slave_polarization"),
            "time_baseline_days": pair_meta.get("time_baseline_days"),
            "spatial_baseline_meters": pair_meta.get("spatial_baseline_meters"),
            "scene_center_distance_meters": pair_meta.get("scene_center_distance_meters"),
            "scene_pair_uid": pair_meta.get("scene_pair_uid") or pair_meta.get("pair_uid"),
            "pair_uid": pair_meta.get("pair_uid") or pair_meta.get("scene_pair_uid"),
            "network_run_id": pair_meta.get("network_run_id"),
            "network_edge_id": pair_meta.get("network_edge_id"),
            "policy_version": pair_meta.get("policy_version"),
            "selection_strategy": pair_meta.get("selection_strategy"),
        },
    )


def _build_sarscapedata(base_path: str) -> Dict[str, Any]:
    """Build a SARSCAPEDATA hash from a base path (without extension).

    envipyengine/taskengine expects SARSCAPEDATA parameters as a Hash:
      {"url": "...", "factory": "ENVISARscapedata", "auxiliary_url": [...]}
    Passing a plain string causes hydration failure.
    """
    normalized = _normalize_path(base_path)
    return {
        "url": normalized,
        "factory": "ENVISARscapedata",
        "auxiliary_url": [
            normalized + ".sml",
            normalized + ".hdr",
        ],
    }


# ---------------------------------------------------------------------------
# Core: execute a single ENVI task via envipyengine
# ---------------------------------------------------------------------------

def execute_envi_task(task_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Call envipyengine to execute one ENVI/SARscape task.

    Returns the outputParameters dict from the task result so callers
    can directly access output SARSCAPEDATA by parameter name.
    Raises RuntimeError on timeout (envipyengine hang).

    NOTE: taskengine.exe is a singleton — calls are serialized via _ENVI_GLOBAL_LOCK.
    """
    try:
        _ensure_envipyengine_config()
        from envipyengine import Engine
    except ImportError as exc:
        raise RuntimeError(
            "envipyengine is not installed. "
            "Install it with: pip install envipyengine"
        ) from exc

    with _ENVI_GLOBAL_LOCK:
        with _envi_taskengine_runtime_context():
            engine = Engine("ENVI")
            task = engine.task(task_name)
            existing_taskengine_pids = _list_taskengine_pids()

            # Run with timeout to handle envipyengine hangs
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(task.execute, parameters)
                try:
                    result = future.result(timeout=_ENVI_TASK_TIMEOUT)
                except FuturesTimeoutError:
                    try:
                        cleanup = _cleanup_new_taskengine_processes(existing_taskengine_pids)
                        stopped = cleanup.get("taskengine_stopped_pids") or []
                        remaining = cleanup.get("taskengine_remaining_new_pids") or []
                        print(
                            "[WARN] execute_envi_task: task timed out; "
                            f"stopped_new_taskengine_pids={stopped}; "
                            f"remaining_new_taskengine_pids={remaining}"
                        )
                    except Exception as _exc:
                        print(f"[WARN] execute_envi_task: taskengine cleanup failed — {_exc}")
                    raise RuntimeError(
                        f"Task {task_name} timed out after {_ENVI_TASK_TIMEOUT}s "
                        f"(envipyengine hung). Output files may still exist."
                    )

    # taskengine returns {"outputParameters": {...}, ...}
    return result.get("outputParameters", result)


def _unwrap_sarscapedata(value: Any) -> Any:
    """Unwrap SARSCAPEDATA output from envipyengine.

    taskengine returns output SARSCAPEDATA as a list: [{url, factory, ...}].
    Input parameters expect a plain dict: {url, factory, ...}.
    """
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    return value


def _configured_sarscape_sbas_task_candidates() -> List[str]:
    configured = str(_read_env("SARSCAPE_SBAS_TASK_NAMES", "") or "").strip()
    if not configured:
        return list(SARSCAPE_SBAS_TASK_CANDIDATES)
    names: List[str] = []
    for raw in configured.replace(";", ",").split(","):
        name = raw.strip()
        if name and name not in names:
            names.append(name)
    return names or list(SARSCAPE_SBAS_TASK_CANDIDATES)


def _envi_install_root() -> str:
    executable = _to_local_path(IDL_EXECUTABLE)
    if not executable:
        return ""
    return os.path.abspath(os.path.join(os.path.dirname(executable), "..", "..", ".."))


def _static_envi_task_template_path(task_name: str) -> str:
    name = str(task_name or "").strip()
    if not name:
        return ""
    envi_root = _envi_install_root()
    if not envi_root:
        return ""
    candidates = [
        os.path.join(envi_root, "user_custom_code", f"{name}.task"),
        os.path.join(envi_root, "resource", "templates", "tasks", "SARscape", f"{name}.task"),
        os.path.join(envi_root, "resource", "templates", "tasks", f"{name}.task"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _json_safe_parameter(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe_parameter(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_parameter(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _summarize_task_parameters(raw_parameters: Any) -> Dict[str, Any]:
    safe_parameters = _json_safe_parameter(raw_parameters)
    input_names: List[str] = []
    output_names: List[str] = []
    required_input_names: List[str] = []

    if isinstance(safe_parameters, dict):
        iterable = safe_parameters.values()
    elif isinstance(safe_parameters, list):
        iterable = safe_parameters
    else:
        iterable = []

    for item in iterable:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("NAME") or "").strip()
        direction = str(item.get("direction") or item.get("DIRECTION") or "").strip().lower()
        required = bool(item.get("required") or item.get("REQUIRED"))
        if not name:
            continue
        if direction == "input":
            input_names.append(name)
            if required:
                required_input_names.append(name)
        elif direction == "output":
            output_names.append(name)

    return {
        "parameter_count": (
            len(safe_parameters)
            if isinstance(safe_parameters, (dict, list))
            else 0
        ),
        "input_names": input_names,
        "required_input_names": required_input_names,
        "output_names": output_names,
        "parameters": safe_parameters,
    }


def list_envi_tasks() -> Dict[str, Any]:
    """List ENVI task names without instantiating individual task parameters."""
    result: Dict[str, Any] = {
        "ok": False,
        "engine": "envipyengine",
        "task_count": 0,
        "tasks": [],
        "error": None,
    }
    try:
        _ensure_envipyengine_config()
        from envipyengine import Engine
    except ImportError:
        result["error"] = (
            "envipyengine is not installed. Install it with: pip install envipyengine"
        )
        return result

    with _ENVI_GLOBAL_LOCK:
        with _envi_taskengine_runtime_context():
            try:
                names = Engine("ENVI").tasks()
            except Exception as exc:
                result["error"] = str(exc)
                return result

    result["tasks"] = [str(name) for name in names]
    result["task_count"] = len(result["tasks"])
    result["ok"] = True
    return result


def discover_sarscape_sbas_tasks() -> Dict[str, Any]:
    """Discover installed SARscape SBAS/E-SBAS task names by filtering Engine.tasks()."""
    report = list_envi_tasks()
    task_names = list(report.get("tasks") or [])
    keywords = (
        "StackSBAS",
        "StackESBAS",
        "ConnectionGraphESBAS",
    )
    explicit_names = set(SARSCAPE_SBAS_NATIVE_WORKFLOW_CANDIDATES) | set(SARSCAPE_SBAS_SUPPORT_TASK_CANDIDATES)
    matches = [
        name
        for name in task_names
        if (
            str(name) in explicit_names
            or (
                str(name).startswith("SARsInSAR")
                and any(keyword.lower() in str(name).lower() for keyword in keywords)
            )
        )
    ]
    static_task_files: Dict[str, str] = {}
    for name in SARSCAPE_SBAS_TASK_CANDIDATES:
        path = _static_envi_task_template_path(name)
        if path:
            static_task_files[name] = path
            if name not in matches:
                matches.append(name)
    preferred_order = [
        *SARSCAPE_SBAS_NATIVE_WORKFLOW_CANDIDATES,
        *SARSCAPE_SBAS_SUPPORT_TASK_CANDIDATES,
        "SARsInSARStackSBASGenerateConnectionGraph",
        "SARsInSARStackSBASInterferogramGeneration",
        "SARsInSARStackSBASInversionStep1",
        "SARsInSARStackSBASInversionStep2",
        "SARsInSARStackSBASGeocode",
        "SARsInSARStackSBASVariogram",
        "SARsInSARStackESBASInterferogramGeneration",
        "SARsInSARStackESBASInversion",
        "SARsInSARStackESBASGeocode",
        "SARsInSARConnectionGraphESBAS",
    ]
    ordered: List[str] = []
    for name in preferred_order:
        if name in matches and name not in ordered:
            ordered.append(name)
    for name in sorted(matches):
        if name not in ordered:
            ordered.append(name)

    return {
        "ok": bool(report.get("ok")) and bool(ordered),
        "engine": report.get("engine"),
        "task_count": int(report.get("task_count") or 0),
        "sarscape_sbas_task_count": len(ordered),
        "sarscape_sbas_tasks": ordered,
        "static_task_files": {
            name: static_task_files[name]
            for name in ordered
            if name in static_task_files
        },
        "error": report.get("error"),
    }


def inspect_envi_tasks(task_names: List[str]) -> Dict[str, Any]:
    """Inspect ENVI/SARscape tasks without executing them."""
    started_at = _utc_now_text()
    deduped_names: List[str] = []
    for raw_name in task_names:
        name = str(raw_name or "").strip()
        if name and name not in deduped_names:
            deduped_names.append(name)

    result: Dict[str, Any] = {
        "ok": False,
        "engine": "envipyengine",
        "started_at": started_at,
        "finished_at": None,
        "task_count": len(deduped_names),
        "available_count": 0,
        "missing_count": 0,
        "tasks": [],
        "error": None,
    }
    if not deduped_names:
        result["error"] = "No task names provided."
        result["finished_at"] = _utc_now_text()
        return result

    try:
        _ensure_envipyengine_config()
        from envipyengine import Engine
    except ImportError as exc:
        result["error"] = (
            "envipyengine is not installed. Install it with: pip install envipyengine"
        )
        result["finished_at"] = _utc_now_text()
        return result

    with _ENVI_GLOBAL_LOCK:
        with _envi_taskengine_runtime_context():
            try:
                engine = Engine("ENVI")
            except Exception as exc:
                result["error"] = f"Failed to initialize ENVI engine: {exc}"
                result["finished_at"] = _utc_now_text()
                return result

            for task_name in deduped_names:
                item: Dict[str, Any] = {
                    "name": task_name,
                    "available": False,
                    "error": None,
                    "parameter_count": 0,
                    "input_names": [],
                    "required_input_names": [],
                    "output_names": [],
                    "parameters": [],
                }
                try:
                    task = engine.task(task_name)
                    summary = _summarize_task_parameters(getattr(task, "parameters", []))
                    item.update(summary)
                    item["available"] = True
                except Exception as exc:
                    item["error"] = str(exc)
                result["tasks"].append(item)

    result["available_count"] = sum(1 for item in result["tasks"] if item.get("available"))
    result["missing_count"] = sum(1 for item in result["tasks"] if not item.get("available"))
    result["ok"] = result["available_count"] > 0
    result["finished_at"] = _utc_now_text()
    return result


def inspect_sarscape_sbas_tasks(
    task_names: Optional[List[str]] = None,
    *,
    include_parameters: bool = False,
) -> Dict[str, Any]:
    """Inspect likely SARscape SBAS/E-SBAS task names for the installed version."""
    status = get_status()
    discovery = discover_sarscape_sbas_tasks()
    names = task_names or list(discovery.get("sarscape_sbas_tasks") or _configured_sarscape_sbas_task_candidates())
    if include_parameters:
        task_report = inspect_envi_tasks(names)
    else:
        discovered_set = set(discovery.get("sarscape_sbas_tasks") or [])
        task_report = {
            "ok": bool(discovery.get("ok")),
            "engine": "envipyengine",
            "task_count": len(names),
            "available_count": sum(1 for name in names if name in discovered_set),
            "missing_count": sum(1 for name in names if name not in discovered_set),
            "tasks": [
                {
                    "name": name,
                    "available": name in discovered_set,
                    "error": None if name in discovered_set else "Task name not listed by Engine.tasks().",
                    "parameter_count": None,
                    "input_names": [],
                    "required_input_names": [],
                    "output_names": [],
                    "parameters": [],
                }
                for name in names
            ],
            "error": discovery.get("error"),
        }
    task_report["status"] = {
        "idl_installed": status.get("idl_installed"),
        "idl_executable": status.get("idl_executable"),
        "runner_ready": status.get("runner_ready"),
        "runner_python": status.get("runner_python"),
        "runner_message": status.get("runner_message"),
        "dem_base_file": status.get("dem_base_file"),
        "dem_exists": status.get("dem_exists"),
    }
    task_report["candidate_source"] = (
        "SARSCAPE_SBAS_TASK_NAMES"
        if str(_read_env("SARSCAPE_SBAS_TASK_NAMES", "") or "").strip()
        else "engine_task_list"
    )
    task_report["include_parameters"] = bool(include_parameters)
    task_report["discovery"] = discovery
    task_report["ready_for_pipeline_design"] = bool(task_report.get("ok"))
    return task_report


def inspect_sarscape_sbas_tasks_subprocess(
    task_names: Optional[List[str]] = None,
    *,
    timeout_seconds: int = 120,
    include_parameters: bool = False,
) -> Dict[str, Any]:
    """Run SARscape SBAS task inspection through the isolated ENVI runner."""
    command = build_envi_runner_command("--inspect-sarscape-sbas")
    if include_parameters:
        command.append("--include-parameters")
    for name in task_names or []:
        if str(name or "").strip():
            command.extend(["--task-name", str(name).strip()])
    existing_taskengine_pids = _list_taskengine_pids()
    try:
        completed = subprocess.run(
            command,
            cwd=get_envi_runner_cwd(),
            env=get_envi_runner_env(),
            capture_output=True,
            text=True,
            timeout=max(10, int(timeout_seconds or 120)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleanup = _cleanup_new_taskengine_processes(existing_taskengine_pids)
        stdout_text = str(exc.stdout or "").strip()
        stderr_text = str(exc.stderr or "").strip()
        return {
            "ok": False,
            "returncode": None,
            "timeout": True,
            "timeout_seconds": max(10, int(timeout_seconds or 120)),
            "stdout": stdout_text[:2000],
            "stderr": stderr_text[:2000],
            "error": (
                "SARscape SBAS task inspection timed out. "
                "Use lightweight discovery without include_parameters, or provide a manually verified task template."
            ),
            "runner_command": command,
            **cleanup,
        }
    stdout_text = str(completed.stdout or "").strip()
    stderr_text = str(completed.stderr or "").strip()
    try:
        payload = json.loads(stdout_text) if stdout_text else {}
    except Exception:
        payload = {}
    payload.setdefault("returncode", int(completed.returncode))
    payload.setdefault("stdout", stdout_text[:2000])
    payload.setdefault("stderr", stderr_text[:2000])
    payload["runner_command"] = command
    if completed.returncode != 0:
        payload["ok"] = False
        payload.setdefault("error", stderr_text or stdout_text or f"returncode={completed.returncode}")
    return payload


# ---------------------------------------------------------------------------
# Import workflow
# ---------------------------------------------------------------------------

def _import_single_dir(
    display_name: str,
    data_dir: str,
    log_lines: List[str],
) -> tuple:
    """Import all .meta.xml in one directory. Returns (processed, failed, skipped)."""
    if _has_sml(data_dir):
        log_lines.append(f"[skip] {display_name}: .sml already exists")
        return 0, 0, 1

    meta_files = _find_meta_files(data_dir)
    if not meta_files:
        log_lines.append(f"[skip] {display_name}: no *.meta.xml")
        return 0, 0, 0

    processed = 0
    failed = 0
    for meta_file in meta_files:
        start = time.time()
        try:
            execute_envi_task(
                "SARsImportLuTan1",
                {
                    "INPUT_FILE_LIST": [meta_file],
                    "ROOT_URI_FOR_OUTPUT": data_dir,
                },
            )
            elapsed = round(time.time() - start, 1)
            processed += 1
            log_lines.append(
                f"[ok] import {display_name}: "
                f"{os.path.basename(meta_file)} ({elapsed}s)"
            )
        except Exception as exc:
            elapsed = round(time.time() - start, 1)
            failed += 1
            log_lines.append(
                f"[err] import {display_name}: "
                f"{os.path.basename(meta_file)} failed ({elapsed}s): {exc}"
            )
    return processed, failed, 0


def run_import_workflow(
    root_dir: str,
    num_to_process: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Scan root_dir for raw SAR data and batch-import via SARsImportLuTan1.

    Supports two layouts:
    - Task_* folders with master/slave subdirs
    - Flat folders containing .meta.xml directly
    """
    root_dir = _to_local_path(root_dir)
    if not root_dir or not os.path.isdir(root_dir):
        raise ValueError(f"Import root directory does not exist: {root_dir}")

    task_folders = _collect_task_folders(root_dir)
    scan_mode = "task_folder"
    if not task_folders:
        task_folders = sorted(
            p for p in glob(os.path.join(root_dir, "*")) if os.path.isdir(p)
        )
        scan_mode = "flat_folder"

    log_lines: List[str] = [
        f"[envi] import task=SARsImportLuTan1",
        f"[envi] root_dir={root_dir}",
        f"[envi] scan_mode={scan_mode}",
        f"[envi] found folders={len(task_folders)}",
    ]
    if not task_folders:
        return {
            "summary": {
                "scan_mode": scan_mode,
                "task_folders": 0,
                "processed": 0,
                "failed": 0,
                "skipped": 0,
            },
            "log_lines": log_lines,
        }

    total_processed = 0
    total_failed = 0
    total_skipped = 0

    for folder in task_folders:
        if num_to_process > 0 and total_processed >= num_to_process:
            log_lines.append(f"[envi] reached limit={num_to_process}")
            break
        name = os.path.basename(folder)

        if scan_mode == "flat_folder":
            candidates = [(name, folder)]
        else:
            candidates = []
            for side in ("master", "slave"):
                d = os.path.join(folder, side)
                if os.path.isdir(d):
                    candidates.append((f"{name}/{side}", d))
                else:
                    log_lines.append(f"[warn] {name}/{side} missing, skip")

        for display, data_dir in candidates:
            if num_to_process > 0 and total_processed >= num_to_process:
                break
            p, f, s = _import_single_dir(display, data_dir, log_lines)
            total_processed += p
            total_failed += f
            total_skipped += s

    if total_failed > 0 and total_processed == 0:
        detail = "\n".join(log_lines[-20:])
        raise RuntimeError(
            f"All import tasks failed. failed={total_failed}.\n{detail}"
        )

    return {
        "summary": {
            "scan_mode": scan_mode,
            "task_folders": len(task_folders),
            "processed": total_processed,
            "failed": total_failed,
            "skipped": total_skipped,
        },
        "log_lines": log_lines,
    }


# ---------------------------------------------------------------------------
# D-InSAR workflow (with smart chaining: auto-import if needed)
# ---------------------------------------------------------------------------

def run_dinsar_workflow(
    root_dir: str,
    num_to_process: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
    timeout_seconds: Optional[int] = None,
    job_id: Optional[str] = None,
    run_key: Optional[str] = None,
    profile_code: str = "metatask",
    started_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run D-InSAR metatask on Task_* folders.

    Smart chaining: for each Task_* folder, if master/slave lack .sml files,
    automatically run Import first, then proceed with D-InSAR.
    DEM path is read from .env (IDL_DINSAR_DEM_BASE_FILE).
    """
    timeout = min(timeout_seconds or timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
    root_dir = _to_local_path(root_dir)
    run_started_at = started_at or datetime.utcnow()
    run_started_at_text = run_started_at.isoformat(timespec="seconds") + "Z"
    resolved_run_key = run_key or build_run_key("sarscape", profile_code, started_at=run_started_at)
    dem_base_file = DEM_BASE_FILE
    if not root_dir or not os.path.isdir(root_dir):
        raise ValueError(f"D-InSAR root directory does not exist: {root_dir}")
    if not dem_base_file:
        raise ValueError(
            "DEM path not configured. Set IDL_DINSAR_DEM_BASE_FILE in .env"
        )

    task_folders = _collect_task_folders(root_dir)
    log_lines: List[str] = [
        f"[envi] dinsar metatask",
        f"[envi] root_dir={root_dir}",
        f"[envi] dem={dem_base_file}",
        f"[envi] Task_* folders={len(task_folders)}",
    ]
    if not task_folders:
        return {
            "summary": {
                "task_folders": 0,
                "processed": 0,
                "failed": 0,
                "skipped": 0,
                "auto_imported": 0,
            },
            "log_lines": log_lines,
            "task_results": [],
            "output_dirs": [],
            "run_key": resolved_run_key,
            "profile_code": profile_code,
        }

    processed = 0
    failed = 0
    skipped = 0
    auto_imported = 0
    task_results: List[Dict[str, Any]] = []
    output_dirs: List[str] = []

    for folder in task_folders:
        if num_to_process > 0 and processed >= num_to_process:
            log_lines.append(f"[envi] reached limit={num_to_process}")
            break
        task_name = os.path.basename(folder)
        task_alias, pair_key, pair_meta = _resolve_dinsar_pair_identity(folder, task_name)
        master_dir = os.path.join(folder, "master")
        slave_dir = os.path.join(folder, "slave")

        if not os.path.isdir(master_dir) or not os.path.isdir(slave_dir):
            skipped += 1
            log_lines.append(f"[skip] {task_name}: master/slave dir missing")
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "success": False,
                    "status": "skipped",
                    "error": "master/slave dir missing",
                }
            )
            continue

        # --- Smart chaining: auto-import if .sml missing ---
        for side, side_dir in [("master", master_dir), ("slave", slave_dir)]:
            if not _has_sml(side_dir):
                meta_files = _find_meta_files(side_dir)
                if not meta_files:
                    log_lines.append(
                        f"[warn] {task_name}/{side}: no .sml and no .meta.xml"
                    )
                    continue
                log_lines.append(
                    f"[auto-import] {task_name}/{side}: "
                    f"importing {len(meta_files)} file(s)"
                )
                for mf in meta_files:
                    start = time.time()
                    try:
                        execute_envi_task(
                            "SARsImportLuTan1",
                            {
                                "INPUT_FILE_LIST": [mf],
                                "ROOT_URI_FOR_OUTPUT": side_dir,
                            },
                        )
                        elapsed = round(time.time() - start, 1)
                        auto_imported += 1
                        log_lines.append(
                            f"[auto-import ok] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s)"
                        )
                    except Exception as exc:
                        elapsed = round(time.time() - start, 1)
                        log_lines.append(
                            f"[auto-import err] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s): {exc}"
                        )

        # After auto-import, check .sml again
        master_base = _first_sml_base(master_dir)
        slave_base = _first_sml_base(slave_dir)
        if not master_base or not slave_base:
            skipped += 1
            log_lines.append(
                f"[skip] {task_name}: still missing .sml after import "
                f"(master={'yes' if master_base else 'no'} "
                f"slave={'yes' if slave_base else 'no'})"
            )
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "success": False,
                    "status": "skipped",
                    "error": "missing .sml after import",
                }
            )
            continue

        output_dir = os.path.join(folder, "dinsar_results")
        os.makedirs(output_dir, exist_ok=True)

        start = time.time()
        try:
            execute_envi_task(
                "SARsMetataskInSARDisplacementGeneration",
                {
                    "REFERENCE_SARSCAPEDATA": _build_sarscapedata(master_base),
                    "SECONDARY_SARSCAPEDATA": _build_sarscapedata(slave_base),
                    "DEM_SARSCAPEDATA": _build_sarscapedata(dem_base_file),
                    "OUTPUT_FOLDER": _normalize_path(output_dir),
                },
            )
            elapsed = round(time.time() - start, 1)
            processed += 1
            log_lines.append(f"[ok] dinsar {task_name} ({elapsed}s)")
            _write_envi_run_sidecar(
                output_dir,
                engine_code="sarscape",
                profile_code=profile_code,
                root_dir=root_dir,
                task_dir=folder,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                run_key=resolved_run_key,
                pair_meta=pair_meta,
                started_at=run_started_at_text,
                params={
                    "timeout_seconds": timeout,
                    "workflow": "metatask",
                    "dem_base_file": dem_base_file,
                },
                metrics={
                    "elapsed_seconds": elapsed,
                },
            )
            output_dirs.append(output_dir)
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "output_dir": output_dir,
                    "success": True,
                    "status": "ok",
                    "elapsed_seconds": elapsed,
                }
            )
        except Exception as exc:
            elapsed = round(time.time() - start, 1)
            failed += 1
            log_lines.append(
                f"[err] dinsar {task_name} failed ({elapsed}s): {exc}"
            )
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "output_dir": output_dir,
                    "success": False,
                    "status": "failed",
                    "elapsed_seconds": elapsed,
                    "error": str(exc),
                }
            )

    if failed > 0 and processed == 0 and len(task_folders) > 0:
        detail = "\n".join(log_lines[-20:])
        raise RuntimeError(
            f"All D-InSAR tasks failed. failed={failed}, "
            f"skipped={skipped}.\n{detail}"
        )

    return {
        "summary": {
            "task_folders": len(task_folders),
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "auto_imported": auto_imported,
        },
        "log_lines": log_lines,
        "task_results": task_results,
        "output_dirs": output_dirs,
        "run_key": resolved_run_key,
        "profile_code": profile_code,
    }


# ---------------------------------------------------------------------------
# Custom D-InSAR workflow helpers
# ---------------------------------------------------------------------------

def _read_sml_parameter(sml_file: str, param_name: str) -> Optional[str]:
    """Read a parameter value from a SARscape .sml XML file.

    SML files use a namespace (http://www.sarmap.ch/xml/SARscapeHeaderSchema),
    so we strip the namespace prefix before comparing tag names.
    """
    sml_path = _to_local_path(sml_file)
    if not os.path.isfile(sml_path):
        return None
    try:
        tree = ET.parse(sml_path)
        root = tree.getroot()
        tag_upper = param_name.upper()
        for elem in root.iter():
            # Strip namespace: {http://...}TagName -> TagName
            local_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_tag.upper() == tag_upper and elem.text:
                return elem.text.strip()
    except Exception as exc:
        print(f"[WARN] _read_sml: {sml_path} — {exc}")
    return None


def _calculate_looks(
    master_sml: str,
    slave_sml: str,
    target_resolution: float,
) -> tuple:
    """Calculate range and azimuth looks from SML pixel spacing.

    Returns (range_looks, azimuth_looks) as integers >= 1.
    Mirrors the IDL logic in batch_dinsarworkflow_all.pro.
    """
    m_rg = _read_sml_parameter(master_sml, "PixelSpacingRg")
    m_az = _read_sml_parameter(master_sml, "PixelSpacingAz")
    m_inc = _read_sml_parameter(master_sml, "IncidenceAngle")
    s_rg = _read_sml_parameter(slave_sml, "PixelSpacingRg")
    s_az = _read_sml_parameter(slave_sml, "PixelSpacingAz")
    s_inc = _read_sml_parameter(slave_sml, "IncidenceAngle")

    if not all([m_rg, m_az, m_inc, s_rg, s_az, s_inc]):
        raise ValueError(
            "Cannot read pixel spacing / incidence angle from SML files. "
            f"master={master_sml} slave={slave_sml}"
        )

    m_rg_f, m_az_f, m_inc_f = float(m_rg), float(m_az), float(m_inc)
    s_rg_f, s_az_f, s_inc_f = float(s_rg), float(s_az), float(s_inc)

    avg_az = (m_az_f + s_az_f) / 2.0
    azimuth_looks = max(1, int(target_resolution / avg_az))

    m_ground_rg = m_rg_f / math.sin(math.radians(m_inc_f))
    s_ground_rg = s_rg_f / math.sin(math.radians(s_inc_f))
    avg_ground_rg = (m_ground_rg + s_ground_rg) / 2.0
    range_looks = max(1, int(target_resolution / avg_ground_rg))

    return range_looks, azimuth_looks


def _find_latest_sarscapedata(
    directory: str,
    pattern_fragment: str,
    log_lines: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Scan directory for the latest SARscape file matching a pattern.

    Used as a fallback when envipyengine reports 'outputs not generated'
    but SARscape actually wrote the file. ``pattern_fragment`` is inserted
    into a glob like ``*{pattern_fragment}*.sml``.
    Returns a SARSCAPEDATA dict or None if not found.
    """
    pattern = os.path.join(directory, f"*{pattern_fragment}*.sml")
    candidates = sorted(glob(pattern), key=os.path.getmtime, reverse=True)
    if log_lines is not None:
        log_lines.append(f"[scan] pattern={pattern} found={len(candidates)}")
    if not candidates:
        return None
    sml_path = candidates[0]
    base = sml_path[:-4]  # strip .sml
    if log_lines is not None:
        log_lines.append(f"[scan] using: {os.path.basename(base)}")
    return _build_sarscapedata(base)


# Stability check configuration
_STABILITY_INTERVAL = int(_read_env("ENVI_STABILITY_CHECK_INTERVAL", "15") or 15)
_STABILITY_ROUNDS = int(_read_env("ENVI_STABILITY_ROUNDS", "3") or 3)
_STABILITY_MAX_WAIT = int(_read_env("ENVI_STABILITY_MAX_WAIT", "3600") or 3600)


def _wait_files_stable(
    directory: str,
    log_lines: Optional[List[str]] = None,
) -> None:
    """Wait until all files in directory have stable sizes.

    Checks every _STABILITY_INTERVAL seconds. Returns when file sizes
    are unchanged for _STABILITY_ROUNDS consecutive checks, or after
    _STABILITY_MAX_WAIT seconds (safety cap).
    """
    if not directory or not os.path.isdir(directory):
        return

    def _snapshot() -> Dict[str, int]:
        sizes: Dict[str, int] = {}
        try:
            for root, _dirs, files in os.walk(directory):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sizes[fp] = os.path.getsize(fp)
                    except OSError as exc:
                        print(f"[WARN] _snapshot getsize: {exc}")
        except Exception as exc:
            print(f"[WARN] _snapshot walk: {exc}")
        return sizes

    stable_count = 0
    prev = _snapshot()
    wait_start = time.time()

    while stable_count < _STABILITY_ROUNDS:
        elapsed = time.time() - wait_start
        if elapsed > _STABILITY_MAX_WAIT:
            if log_lines is not None:
                log_lines.append(
                    f"[stability] max wait {_STABILITY_MAX_WAIT}s reached, proceeding"
                )
            break
        time.sleep(_STABILITY_INTERVAL)
        cur = _snapshot()
        if cur == prev:
            stable_count += 1
        else:
            stable_count = 0
            prev = cur

    total_wait = round(time.time() - wait_start, 1)
    if log_lines is not None:
        log_lines.append(
            f"[stability] files stable after {total_wait}s "
            f"({stable_count}/{_STABILITY_ROUNDS} rounds, "
            f"{len(prev)} files)"
        )


def _wait_for_disp_stable(
    directory: str,
    log_lines: Optional[List[str]] = None,
) -> bool:
    """Wait for *_rsp_disp file to appear and stabilize.

    1. Poll until a file matching *_rsp_disp (no extra extension) appears.
    2. Then wait until that file's size is unchanged for consecutive checks.
    Returns True if the file was found and stabilized, False on timeout.
    """
    if not directory or not os.path.isdir(directory):
        return False

    wait_start = time.time()

    # Phase 1: wait for the file to appear
    disp_path = None
    while (time.time() - wait_start) < _STABILITY_MAX_WAIT:
        for f in os.listdir(directory):
            # Match *_rsp_disp exactly (not _rsp_disp_cc_geo etc.)
            if f.endswith("_rsp_disp") and not f.endswith(".hdr") and not f.endswith(".sml"):
                disp_path = os.path.join(directory, f)
                break
        if disp_path:
            break
        if log_lines is not None and int(time.time() - wait_start) % 60 == 0:
            log_lines.append(
                f"[wait_disp] waiting for _rsp_disp file... "
                f"({int(time.time() - wait_start)}s)"
            )
        time.sleep(_STABILITY_INTERVAL)

    if not disp_path:
        if log_lines is not None:
            log_lines.append(
                f"[wait_disp] _rsp_disp file not found after "
                f"{int(time.time() - wait_start)}s"
            )
        return False

    if log_lines is not None:
        log_lines.append(
            f"[wait_disp] found {os.path.basename(disp_path)} "
            f"after {int(time.time() - wait_start)}s"
        )

    # Phase 2: wait for the file size to stabilize
    stable_count = 0
    prev_size = None  # None = not yet read; -1 = read failed
    while stable_count < _STABILITY_ROUNDS:
        if (time.time() - wait_start) > _STABILITY_MAX_WAIT:
            if log_lines is not None:
                log_lines.append("[wait_disp] max wait reached, proceeding")
            break
        time.sleep(_STABILITY_INTERVAL)
        try:
            cur_size = os.path.getsize(disp_path)
        except OSError:
            cur_size = -1
        # Only count as stable if we got a real size (>= 0) and it matches prev
        if cur_size >= 0 and cur_size == prev_size:
            stable_count += 1
        else:
            stable_count = 0
            prev_size = cur_size

    total_wait = round(time.time() - wait_start, 1)
    if log_lines is not None:
        log_lines.append(
            f"[wait_disp] stable after {total_wait}s "
            f"(size={prev_size} bytes)"
        )
    return True


def _generate_gcps(
    coherence_file: str,
    output_shp: str,
    coh_threshold: float = 0.7,
    num_points: int = 100,
    log_lines: Optional[List[str]] = None,
) -> bool:
    """Generate GCP shapefile from coherence raster (Python rewrite of IDL logic).

    Divides the raster into a grid, picks the highest-coherence pixel per cell
    that exceeds coh_threshold, and writes a point shapefile with SARscape fields.
    """
    try:
        import numpy as np
        import rasterio
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError as exc:
        raise RuntimeError(
            "rasterio, geopandas, and shapely are required for GCP generation. "
            f"Missing: {exc}"
        ) from exc

    coh_path = _to_local_path(coherence_file)
    # SARscape coherence files may not have an extension — try common variants
    if not os.path.isfile(coh_path):
        for ext in [".hdr", ""]:
            candidate = coh_path + ext
            if os.path.isfile(candidate):
                coh_path = candidate
                break

    if not os.path.isfile(coh_path):
        if log_lines is not None:
            log_lines.append(f"[gcp] coherence file not found: {coh_path}")
        return False

    with rasterio.open(coh_path) as src:
        data = src.read(1)  # first band
        ns, nl = src.width, src.height

    grid_dim = math.ceil(math.sqrt(num_points))
    x_step = nl // grid_dim  # rows
    y_step = ns // grid_dim  # cols

    points = []
    for j in range(grid_dim):
        for i in range(grid_dim):
            x_start = i * y_step
            y_start = j * x_step
            x_end = min((i + 1) * y_step, ns)
            y_end = min((j + 1) * x_step, nl)
            if x_start >= ns or y_start >= nl:
                continue
            cell = data[y_start:y_end, x_start:x_end]
            max_val = float(np.nanmax(cell)) if cell.size > 0 else 0.0
            if max_val >= coh_threshold:
                idx = int(np.nanargmax(cell))
                cell_h, cell_w = cell.shape
                max_row = idx // cell_w
                max_col = idx % cell_w
                px_col = x_start + max_col
                px_row = y_start + max_row
                points.append((px_col, px_row))

    if not points:
        if log_lines is not None:
            log_lines.append(
                f"[gcp] no points found above threshold {coh_threshold}"
            )
        return False

    # Build GeoDataFrame with SARscape-compatible fields
    records = []
    for idx, (col, row) in enumerate(points):
        records.append({
            "SHP_ID": idx,
            "GCP_LABEL": f"GCP_{idx + 1}",
            "GCP_TYPE": "undefined",
            "GCP_COLUMN": float(col),
            "GCP_ROW": float(row),
            "GCP_OTHER_": "",
            "geometry": Point(float(col), float(row)),
        })

    gdf = gpd.GeoDataFrame(records)
    out_path = _to_local_path(output_shp)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    gdf.to_file(out_path, driver="ESRI Shapefile")

    if log_lines is not None:
        log_lines.append(f"[gcp] created {len(points)} GCPs -> {out_path}")
    return True


def _run_dinsar_custom_single(
    master_base: str,
    slave_base: str,
    dem_base: str,
    output_root: str,
    log_lines: List[str],
    job_id: Optional[str] = None,
    pair_index: int = 0,
    total_pairs: int = 0,
    pair_name: str = "",
) -> bool:
    """Execute the 6-step custom D-InSAR workflow for one pair.

    Uses envipyengine individual tasks instead of the metatask.
    Each step's output SARSCAPEDATA is chained as input to the next step.
    """
    master_sml = master_base + ".sml"
    slave_sml = slave_base + ".sml"

    # Build SARSCAPEDATA hashes
    master_sd = _build_sarscapedata(master_base)
    slave_sd = _build_sarscapedata(slave_base)
    dem_sd = _build_sarscapedata(dem_base)
    out_dir = _normalize_path(os.path.dirname(output_root))

    # --- Calculate looks ---
    try:
        range_looks, azimuth_looks = _calculate_looks(
            master_sml, slave_sml, CUSTOM_TARGET_RESOLUTION_M
        )
        log_lines.append(
            f"[custom] looks: range={range_looks} azimuth={azimuth_looks} "
            f"(target_res={CUSTOM_TARGET_RESOLUTION_M}m)"
        )
    except Exception as exc:
        log_lines.append(f"[custom] looks calculation failed: {exc}")
        return False

    # === STEP 1: Interferogram Generation ===
    log_lines.append("[custom] step 1/6: Interferogram Generation")
    _write_progress(job_id, 1, 6, "Interferogram Generation", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    try:
        r1 = execute_envi_task(
            "SARsInSARInterferogramGeneration",
            {
                "REFERENCE_SARSCAPEDATA": master_sd,
                "SECONDARY_SARSCAPEDATA": slave_sd,
                "DEM_SARSCAPEDATA": dem_sd,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "RG_LOOKS_NBR": float(range_looks),
                "AZ_LOOKS_NBR": float(azimuth_looks),
                "COREGISTRATION_WITH_DEM": True,
            },
        )
        log_lines.append(f"[custom] step 1 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        log_lines.append(
            f"[custom] step 1 failed ({round(time.time() - start, 1)}s): {exc}"
        )
        return False

    # Extract step 1 outputs for chaining
    s1_dint = _unwrap_sarscapedata(r1.get("DINT_SARSCAPEDATA"))
    s1_ref_pwr = _unwrap_sarscapedata(r1.get("REFERENCE_POWER_SARSCAPEDATA"))
    s1_sec_pwr = _unwrap_sarscapedata(r1.get("SECONDARY_POWER_SARSCAPEDATA"))
    s1_sint = _unwrap_sarscapedata(r1.get("SINT_SARSCAPEDATA"))
    s1_srdem = _unwrap_sarscapedata(r1.get("SRDEM_SARSCAPEDATA"))
    log_lines.append(
        f"[custom] step 1 outputs: dint={bool(s1_dint)} ref_pwr={bool(s1_ref_pwr)} "
        f"sec_pwr={bool(s1_sec_pwr)} sint={bool(s1_sint)} srdem={bool(s1_srdem)}"
    )

    # === STEP 2: Filtering and Coherence ===
    log_lines.append("[custom] step 2/6: Filtering and Coherence")
    _write_progress(job_id, 2, 6, "Filtering and Coherence", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    try:
        r2 = execute_envi_task(
            "SARsInSARFilterAndCoherence",
            {
                "DINT_SARSCAPEDATA": s1_dint,
                "REFERENCE_SARSCAPEDATA": s1_ref_pwr,
                "SECONDARY_SARSCAPEDATA": s1_sec_pwr,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "FILTERING_METHOD": CUSTOM_FILTER_METHOD,
                "COHERENCE": True,
                "INTERF_FILT": True,
            },
        )
        log_lines.append(f"[custom] step 2 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        log_lines.append(
            f"[custom] step 2 failed ({round(time.time() - start, 1)}s): {exc}"
        )
        return False

    s2_fint = _unwrap_sarscapedata(r2.get("FINT_SARSCAPEDATA"))
    s2_cc = _unwrap_sarscapedata(r2.get("COHERENCE_SARSCAPEDATA"))
    log_lines.append(
        f"[custom] step 2 outputs: fint={bool(s2_fint)} cc={bool(s2_cc)}"
    )

    # === STEP 3: Remove Residual Phase Frequency ===
    log_lines.append("[custom] step 3/6: Orbital Trend Removal")
    _write_progress(job_id, 3, 6, "Orbital Trend Removal", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    s3_rrpf = None
    try:
        r3 = execute_envi_task(
            "SARsInSARRemoveResidualPhaseFrequency",
            {
                "INTERFEROGRAM_SARSCAPEDATA": s2_fint,
                "COHERENCE_FILE_NAME": s2_cc,
                "ROOT_URI_FOR_OUTPUT": out_dir,
            },
        )
        s3_rrpf = _unwrap_sarscapedata(r3.get("RRPF_DINT_SARSCAPEDATA"))
        log_lines.append(f"[custom] step 3 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 3 engine error ({elapsed}s): {exc}")
        # envipyengine may report "outputs not generated" even though
        # SARscape DID write the file. Scan the directory for it.
        log_lines.append("[custom] step 3 scanning for generated RRPF file...")
        s3_rrpf = _find_latest_sarscapedata(
            _to_local_path(out_dir), "ISARRRPF", log_lines
        )

    if not s3_rrpf:
        log_lines.append("[custom] step 3 failed: no RRPF output found")
        return False
    log_lines.append(f"[custom] step 3 output: {s3_rrpf.get('url', '?')}")

    # === STEP 4: Phase Unwrapping ===
    log_lines.append("[custom] step 4/6: Phase Unwrapping")
    _write_progress(job_id, 4, 6, "Phase Unwrapping", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    s4_upha = None
    try:
        r4 = execute_envi_task(
            "SARsInSARPhaseUnwrapping",
            {
                "INFILE_NAME": s3_rrpf,
                "COHERENCEFILE_NAME": s2_cc,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "UPHA_COH_THRESHOLD": CUSTOM_UNWRAP_COH_THRESHOLD,
            },
        )
        s4_upha = _unwrap_sarscapedata(r4.get("OUTFILE_NAME"))
        log_lines.append(f"[custom] step 4 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 4 engine error ({elapsed}s): {exc}")
        log_lines.append("[custom] step 4 scanning for generated UPHA file...")
        s4_upha = _find_latest_sarscapedata(
            _to_local_path(out_dir), "ISARPU", log_lines
        )

    if not s4_upha:
        log_lines.append("[custom] step 4 failed: no UPHA output found")
        return False
    log_lines.append(f"[custom] step 4 output: {s4_upha.get('url', '?')}")

    # === STEP 5: Refinement and Reflattening (with auto GCP) ===
    # 5a: Generate GCPs from coherence
    log_lines.append("[custom] step 5a/6: GCP Generation")
    _write_progress(job_id, 5, 6, "GCP Generation + Refinement", out_dir, pair_index, total_pairs, pair_name)
    # Extract coherence base path from SARSCAPEDATA for rasterio
    cc_url = s2_cc.get("url", "") if isinstance(s2_cc, dict) else str(s2_cc)
    cc_local = _to_local_path(cc_url)
    auto_gcp_shp = os.path.join(os.path.dirname(cc_local) or out_dir, "auto_gcp.shp")
    gcp_ok = _generate_gcps(
        coherence_file=cc_local,
        output_shp=auto_gcp_shp,
        coh_threshold=CUSTOM_GCP_COH_THRESHOLD,
        num_points=CUSTOM_GCP_NUMBER,
        log_lines=log_lines,
    )
    if not gcp_ok:
        log_lines.append("[custom] step 5a failed: GCP generation returned no points")
        return False

    # 5b: Refinement and Reflattening
    log_lines.append("[custom] step 5b/6: Refinement and Reflattening")
    start = time.time()
    s5_upha = None
    try:
        r5 = execute_envi_task(
            "SARsInSARRefinementAndReflattening",
            {
                "INPUT_UPHA_FILE_NAME": s4_upha,
                "REFERENCE_SARSCAPEDATA": s1_ref_pwr,
                "SECONDARY_SARSCAPEDATA": s1_sec_pwr,
                "SLANT_RANGE_DEM_FILE_NAME": s1_srdem,
                "SYNTHETIC_FILE_NAME": s1_sint,
                "COHERENCE_FILE_NAME": s2_cc,
                "DEM_SARSCAPEDATA": dem_sd,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "REFINEMENT_GCP_FILE_NAME": _normalize_path(auto_gcp_shp),
            },
        )
        s5_upha = _unwrap_sarscapedata(r5.get("UPHA_REFLAT_SARSCAPEDATA"))
        log_lines.append(f"[custom] step 5b ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 5b engine error ({elapsed}s): {exc}")
        log_lines.append("[custom] step 5b scanning for generated REFLAT UPHA file...")
        s5_upha = _find_latest_sarscapedata(
            _to_local_path(out_dir), "ISARRF", log_lines
        )
        # Refinement produces multiple outputs; pick the _upha one
        if not s5_upha:
            s5_upha = _find_latest_sarscapedata(
                _to_local_path(out_dir), "_reflat_upha", log_lines
            )

    if not s5_upha:
        log_lines.append("[custom] step 5b failed: no REFLAT UPHA output found")
        return False
    log_lines.append(f"[custom] step 5b output: {s5_upha.get('url', '?')}")

    # === STEP 6: Phase to Displacement and Geocoding ===
    log_lines.append("[custom] step 6/6: Phase to Displacement + Geocoding")
    _write_progress(job_id, 6, 6, "Phase to Displacement + Geocoding", out_dir, pair_index, total_pairs, pair_name)
    start = time.time()
    try:
        r6 = execute_envi_task(
            "SARsInSARPhaseToDisplacement",
            {
                "INPUT_SARSCAPEDATA": s5_upha,
                "COHERNCE_SARSCAPEDATA": s2_cc,
                "DEM_SARSCAPEDATA": dem_sd,
                "ROOT_URI_FOR_OUTPUT": out_dir,
                "COHERENCE_THRESHOLD": CUSTOM_GEOCODING_COH_THRESHOLD,
                "GEOCODE_RG_GRID_SIZE": CUSTOM_GEOCODING_PIXEL_SIZE_M,
                "GEOCODE_AZ_GRID_SIZE": CUSTOM_GEOCODING_PIXEL_SIZE_M,
            },
        )
        log_lines.append(f"[custom] step 6 ok ({round(time.time() - start, 1)}s)")
    except Exception as exc:
        elapsed = round(time.time() - start, 1)
        log_lines.append(f"[custom] step 6 engine error ({elapsed}s): {exc}")

    # envipyengine may return (success or error) before ENVI finishes writing.
    # Always wait for the _rsp_disp file to appear and stabilize.
    log_lines.append("[custom] step 6: waiting for _rsp_disp file...")
    disp_ok = _wait_for_disp_stable(_to_local_path(out_dir), log_lines)
    if not disp_ok:
        log_lines.append("[custom] step 6 failed: _rsp_disp never appeared")
        return False

    # Final stability wait: ensure ALL output files are fully written
    log_lines.append("[custom] final stability check on output directory...")
    _wait_files_stable(_to_local_path(out_dir), log_lines)

    _write_progress(job_id, 6, 6, "Completed", out_dir, pair_index, total_pairs, pair_name)
    return True


# ---------------------------------------------------------------------------
# Custom D-InSAR batch workflow
# ---------------------------------------------------------------------------

def run_dinsar_custom_workflow(
    root_dir: str,
    num_to_process: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
    job_id: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    run_key: Optional[str] = None,
    profile_code: str = "custom6",
    started_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run custom 6-step D-InSAR on Task_* folders.

    Same smart-chaining as metatask version (auto-import if needed),
    but uses individual SARscape tasks with user-defined parameters.
    """
    timeout = min(timeout_seconds or timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
    root_dir = _to_local_path(root_dir)
    run_started_at = started_at or datetime.utcnow()
    run_started_at_text = run_started_at.isoformat(timespec="seconds") + "Z"
    resolved_run_key = run_key or build_run_key("sarscape", profile_code, started_at=run_started_at)
    dem_base_file = DEM_BASE_FILE
    if not root_dir or not os.path.isdir(root_dir):
        raise ValueError(f"D-InSAR root directory does not exist: {root_dir}")
    if not dem_base_file:
        raise ValueError(
            "DEM path not configured. Set IDL_DINSAR_DEM_BASE_FILE in .env"
        )

    task_folders = _collect_task_folders(root_dir)
    log_lines: List[str] = [
        f"[envi] dinsar custom (6-step)",
        f"[envi] root_dir={root_dir}",
        f"[envi] dem={dem_base_file}",
        f"[envi] target_resolution={CUSTOM_TARGET_RESOLUTION_M}m",
        f"[envi] filter={CUSTOM_FILTER_METHOD}",
        f"[envi] unwrap_coh={CUSTOM_UNWRAP_COH_THRESHOLD}",
        f"[envi] gcp_coh={CUSTOM_GCP_COH_THRESHOLD} gcp_n={CUSTOM_GCP_NUMBER}",
        f"[envi] geocode_coh={CUSTOM_GEOCODING_COH_THRESHOLD} "
        f"geocode_px={CUSTOM_GEOCODING_PIXEL_SIZE_M}m",
        f"[envi] Task_* folders={len(task_folders)}",
    ]
    if not task_folders:
        return {
            "summary": {
                "task_folders": 0,
                "processed": 0,
                "failed": 0,
                "skipped": 0,
                "auto_imported": 0,
            },
            "log_lines": log_lines,
            "task_results": [],
            "output_dirs": [],
            "run_key": resolved_run_key,
            "profile_code": profile_code,
        }

    processed = 0
    failed = 0
    skipped = 0
    auto_imported = 0
    task_results: List[Dict[str, Any]] = []
    output_dirs: List[str] = []
    effective_total = len(task_folders) if num_to_process <= 0 else min(num_to_process, len(task_folders))
    pair_counter = 0

    for folder in task_folders:
        if num_to_process > 0 and processed >= num_to_process:
            log_lines.append(f"[envi] reached limit={num_to_process}")
            break
        task_name = os.path.basename(folder)
        task_alias, pair_key, pair_meta = _resolve_dinsar_pair_identity(folder, task_name)
        master_dir = os.path.join(folder, "master")
        slave_dir = os.path.join(folder, "slave")

        if not os.path.isdir(master_dir) or not os.path.isdir(slave_dir):
            skipped += 1
            log_lines.append(f"[skip] {task_name}: master/slave dir missing")
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "success": False,
                    "status": "skipped",
                    "error": "master/slave dir missing",
                }
            )
            continue

        # --- Smart chaining: auto-import if .sml missing ---
        for side, side_dir in [("master", master_dir), ("slave", slave_dir)]:
            if not _has_sml(side_dir):
                meta_files = _find_meta_files(side_dir)
                if not meta_files:
                    log_lines.append(
                        f"[warn] {task_name}/{side}: no .sml and no .meta.xml"
                    )
                    continue
                log_lines.append(
                    f"[auto-import] {task_name}/{side}: "
                    f"importing {len(meta_files)} file(s)"
                )
                for mf in meta_files:
                    imp_start = time.time()
                    try:
                        execute_envi_task(
                            "SARsImportLuTan1",
                            {
                                "INPUT_FILE_LIST": [mf],
                                "ROOT_URI_FOR_OUTPUT": side_dir,
                            },
                        )
                        elapsed = round(time.time() - imp_start, 1)
                        auto_imported += 1
                        log_lines.append(
                            f"[auto-import ok] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s)"
                        )
                    except Exception as exc:
                        elapsed = round(time.time() - imp_start, 1)
                        log_lines.append(
                            f"[auto-import err] {task_name}/{side}: "
                            f"{os.path.basename(mf)} ({elapsed}s): {exc}"
                        )

        # After auto-import, check .sml again
        master_base = _first_sml_base(master_dir)
        slave_base = _first_sml_base(slave_dir)
        if not master_base or not slave_base:
            skipped += 1
            log_lines.append(
                f"[skip] {task_name}: still missing .sml after import "
                f"(master={'yes' if master_base else 'no'} "
                f"slave={'yes' if slave_base else 'no'})"
            )
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "success": False,
                    "status": "skipped",
                    "error": "missing .sml after import",
                }
            )
            continue

        output_dir = os.path.join(folder, "dinsar_results")
        os.makedirs(output_dir, exist_ok=True)
        output_root = os.path.join(output_dir, "workflow")

        pair_start = time.time()
        pair_counter += 1
        log_lines.append(f"[custom] === {task_name} start ({pair_counter}/{effective_total}) ===")
        try:
            success = _run_dinsar_custom_single(
                master_base, slave_base, dem_base_file, output_root, log_lines,
                job_id=job_id,
                pair_index=pair_counter,
                total_pairs=effective_total,
                pair_name=task_name,
            )
        except Exception as exc:
            success = False
            log_lines.append(f"[custom] {task_name} crashed: {exc}")
        elapsed = round(time.time() - pair_start, 1)

        if success:
            processed += 1
            log_lines.append(f"[ok] custom dinsar {task_name} ({elapsed}s)")
            _write_envi_run_sidecar(
                output_dir,
                engine_code="sarscape",
                profile_code=profile_code,
                root_dir=root_dir,
                task_dir=folder,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                run_key=resolved_run_key,
                pair_meta=pair_meta,
                started_at=run_started_at_text,
                params={
                    "timeout_seconds": timeout,
                    "workflow": "custom6",
                    "dem_base_file": dem_base_file,
                    "target_resolution_m": CUSTOM_TARGET_RESOLUTION_M,
                    "filter_method": CUSTOM_FILTER_METHOD,
                    "unwrap_coh_threshold": CUSTOM_UNWRAP_COH_THRESHOLD,
                    "gcp_coh_threshold": CUSTOM_GCP_COH_THRESHOLD,
                    "gcp_number": CUSTOM_GCP_NUMBER,
                    "geocoding_coh_threshold": CUSTOM_GEOCODING_COH_THRESHOLD,
                    "geocoding_pixel_size_m": CUSTOM_GEOCODING_PIXEL_SIZE_M,
                },
                metrics={
                    "elapsed_seconds": elapsed,
                },
            )
            output_dirs.append(output_dir)
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "output_dir": output_dir,
                    "success": True,
                    "status": "ok",
                    "elapsed_seconds": elapsed,
                }
            )
        else:
            failed += 1
            log_lines.append(f"[err] custom dinsar {task_name} failed ({elapsed}s)")
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": resolved_run_key,
                    "task_dir": folder,
                    "output_dir": output_dir,
                    "success": False,
                    "status": "failed",
                    "elapsed_seconds": elapsed,
                }
            )

        # Flush intermediate log so progress is preserved if process crashes
        try:
            os.makedirs(RUNTIME_DIR, exist_ok=True)
            _interim_log = os.path.join(RUNTIME_DIR, "dinsar_custom_progress.log")
            with open(_interim_log, "w", encoding="utf-8") as _fp:
                _fp.write("\n".join(log_lines))
        except Exception as exc:
            print(f"[WARN] dinsar_workflow log: {exc}")

    if failed > 0 and processed == 0 and len(task_folders) > 0:
        detail = "\n".join(log_lines[-20:])
        raise RuntimeError(
            f"All custom D-InSAR tasks failed. failed={failed}, "
            f"skipped={skipped}.\n{detail}"
        )

    return {
        "summary": {
            "task_folders": len(task_folders),
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "auto_imported": auto_imported,
        },
        "log_lines": log_lines,
        "task_results": task_results,
        "output_dirs": output_dirs,
        "run_key": resolved_run_key,
        "profile_code": profile_code,
    }


# ---------------------------------------------------------------------------
# Single-task D-InSAR workflow entrypoints
# ---------------------------------------------------------------------------

def run_single_task_workflow(
    workflow: str,
    task_dir: str,
    output_dir: str,
    *,
    source_root: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT,
    timeout_seconds: Optional[int] = None,
    job_id: Optional[str] = None,
    run_key: Optional[str] = None,
    profile_code: Optional[str] = None,
    started_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run one Task_* directory into an isolated output directory."""
    normalized_workflow = str(workflow or "").strip().lower()
    if normalized_workflow not in {"dinsar", "dinsar_custom"}:
        raise ValueError(f"Unsupported single-task workflow: {workflow}")

    timeout = min(timeout_seconds or timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
    task_dir = _to_local_path(task_dir)
    output_dir = _to_local_path(output_dir)
    source_root = _to_local_path(source_root or os.path.dirname(task_dir))
    run_started_at = started_at or datetime.utcnow()
    run_started_at_text = run_started_at.isoformat(timespec="seconds") + "Z"
    resolved_profile_code = profile_code or ("custom6" if normalized_workflow == "dinsar_custom" else "metatask")
    resolved_run_key = run_key or build_run_key("sarscape", resolved_profile_code, started_at=run_started_at)
    task_name = os.path.basename(os.path.normpath(task_dir))
    task_alias, pair_key, pair_meta = _resolve_dinsar_pair_identity(task_dir, task_name)

    if not task_dir or not os.path.isdir(task_dir):
        raise ValueError(f"D-InSAR task directory does not exist: {task_dir}")
    if not output_dir:
        raise ValueError("output_dir must not be empty.")
    if not DEM_BASE_FILE:
        raise ValueError("DEM path not configured. Set IDL_DINSAR_DEM_BASE_FILE in .env")

    native_output_dir = get_run_native_output_dir(output_dir)
    master_dir = os.path.join(task_dir, "master")
    slave_dir = os.path.join(task_dir, "slave")
    if not os.path.isdir(master_dir) or not os.path.isdir(slave_dir):
        raise RuntimeError(f"{task_name}: master/slave dir missing")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(native_output_dir, exist_ok=True)
    log_lines: List[str] = [
        f"[envi] single task workflow={normalized_workflow}",
        f"[envi] source_root={source_root}",
        f"[envi] task_dir={task_dir}",
        f"[envi] output_dir={output_dir}",
        f"[envi] native_output_dir={native_output_dir}",
        f"[envi] dem={DEM_BASE_FILE}",
    ]
    auto_imported = 0

    for side, side_dir in [("master", master_dir), ("slave", slave_dir)]:
        if _has_sml(side_dir):
            continue
        meta_files = _find_meta_files(side_dir)
        if not meta_files:
            log_lines.append(f"[warn] {task_name}/{side}: no .sml and no .meta.xml")
            continue
        log_lines.append(f"[auto-import] {task_name}/{side}: importing {len(meta_files)} file(s)")
        for meta_file in meta_files:
            start = time.time()
            try:
                execute_envi_task(
                    "SARsImportLuTan1",
                    {
                        "INPUT_FILE_LIST": [meta_file],
                        "ROOT_URI_FOR_OUTPUT": side_dir,
                    },
                )
                elapsed = round(time.time() - start, 1)
                auto_imported += 1
                log_lines.append(f"[auto-import ok] {task_name}/{side}: {os.path.basename(meta_file)} ({elapsed}s)")
            except Exception as exc:
                elapsed = round(time.time() - start, 1)
                log_lines.append(f"[auto-import err] {task_name}/{side}: {os.path.basename(meta_file)} ({elapsed}s): {exc}")

    master_base = _first_sml_base(master_dir)
    slave_base = _first_sml_base(slave_dir)
    if not master_base or not slave_base:
        raise RuntimeError(
            f"{task_name}: missing .sml after import "
            f"(master={'yes' if master_base else 'no'} slave={'yes' if slave_base else 'no'})"
        )

    start = time.time()
    if normalized_workflow == "dinsar":
        _write_progress(job_id, 1, 1, "Metatask D-InSAR", output_dir, 1, 1, task_name)
        execute_envi_task(
            "SARsMetataskInSARDisplacementGeneration",
            {
                "REFERENCE_SARSCAPEDATA": _build_sarscapedata(master_base),
                "SECONDARY_SARSCAPEDATA": _build_sarscapedata(slave_base),
                "DEM_SARSCAPEDATA": _build_sarscapedata(DEM_BASE_FILE),
                "OUTPUT_FOLDER": _normalize_path(native_output_dir),
            },
        )
        _write_progress(job_id, 1, 1, "Completed", output_dir, 1, 1, task_name)
    else:
        success = _run_dinsar_custom_single(
            master_base,
            slave_base,
            DEM_BASE_FILE,
            os.path.join(native_output_dir, "workflow"),
            log_lines,
            job_id=job_id,
            pair_index=1,
            total_pairs=1,
            pair_name=task_name,
        )
        if not success:
            detail = "\n".join(log_lines[-20:])
            raise RuntimeError(f"{task_name}: custom D-InSAR workflow failed.\n{detail}")

    elapsed = round(time.time() - start, 1)
    log_lines.append(f"[ok] {normalized_workflow} {task_name} ({elapsed}s)")
    _write_envi_run_sidecar(
        output_dir,
        engine_code="sarscape",
        profile_code=resolved_profile_code,
        root_dir=source_root,
        task_dir=task_dir,
        task_name=task_name,
        task_alias=task_alias,
        pair_key=pair_key,
        run_key=resolved_run_key,
        pair_meta=pair_meta,
        started_at=run_started_at_text,
        params=(
            {
                "timeout_seconds": timeout,
                "workflow": "custom6",
                "dem_base_file": DEM_BASE_FILE,
                "target_resolution_m": CUSTOM_TARGET_RESOLUTION_M,
                "filter_method": CUSTOM_FILTER_METHOD,
                "unwrap_coh_threshold": CUSTOM_UNWRAP_COH_THRESHOLD,
                "gcp_coh_threshold": CUSTOM_GCP_COH_THRESHOLD,
                "gcp_number": CUSTOM_GCP_NUMBER,
                "geocoding_coh_threshold": CUSTOM_GEOCODING_COH_THRESHOLD,
                "geocoding_pixel_size_m": CUSTOM_GEOCODING_PIXEL_SIZE_M,
            }
            if normalized_workflow == "dinsar_custom"
            else {
                "timeout_seconds": timeout,
                "workflow": "metatask",
                "dem_base_file": DEM_BASE_FILE,
            }
        ),
        metrics={
            "elapsed_seconds": elapsed,
        },
        native_output_dir=native_output_dir,
    )
    return {
        "summary": {
            "task_folders": 1,
            "processed": 1,
            "failed": 0,
            "skipped": 0,
            "auto_imported": auto_imported,
        },
        "log_lines": log_lines,
        "task_results": [
            {
                "task_name": task_name,
                "task_alias": task_alias,
                "pair_key": pair_key,
                "run_key": resolved_run_key,
                "task_dir": task_dir,
                "output_dir": output_dir,
                "native_output_dir": native_output_dir,
                "success": True,
                "status": "ok",
                "elapsed_seconds": elapsed,
            }
        ],
        "output_dirs": [output_dir],
        "run_key": resolved_run_key,
        "profile_code": resolved_profile_code,
    }


# ---------------------------------------------------------------------------
# Inspect (pre-check) functions
# ---------------------------------------------------------------------------

def inspect_import(root_dir: str) -> Dict[str, Any]:
    """Pre-check Import readiness for a root directory."""
    root_dir = _to_local_path(root_dir)
    result: Dict[str, Any] = {
        "workflow": "import",
        "root_dir": root_dir,
        "exists": bool(root_dir and os.path.isdir(root_dir)),
        "ready": False,
        "summary": {},
        "warnings": [],
    }
    if not result["exists"]:
        result["warnings"].append("root_dir does not exist.")
        return result

    task_folders = _collect_task_folders(root_dir)
    scan_mode = "task_folder"
    scan_targets = task_folders
    if not task_folders:
        scan_mode = "flat_folder"
        scan_targets = sorted(
            p for p in glob(os.path.join(root_dir, "*")) if os.path.isdir(p)
        )

    meta_count = 0
    sml_count = 0
    candidate_count = 0
    for folder in scan_targets:
        if scan_mode == "flat_folder":
            candidates = [folder]
        else:
            candidates = [
                os.path.join(folder, "master"),
                os.path.join(folder, "slave"),
            ]
        for candidate in candidates:
            if not os.path.isdir(candidate):
                continue
            metas = glob(os.path.join(candidate, "*.meta.xml"))
            smls = glob(os.path.join(candidate, "*.sml"))
            meta_count += len(metas)
            sml_count += len(smls)
            if metas and not smls:
                candidate_count += len(metas)

    result["summary"] = {
        "scan_mode": scan_mode,
        "folder_count": len(scan_targets),
        "meta_file_count": meta_count,
        "existing_sml_count": sml_count,
        "import_candidate_count": candidate_count,
    }
    result["ready"] = candidate_count > 0
    if candidate_count == 0:
        result["warnings"].append(
            "No import candidates (*.meta.xml without *.sml)."
        )
    return result


def inspect_dinsar(root_dir: str) -> Dict[str, Any]:
    """Pre-check D-InSAR readiness. Includes Import status detection."""
    root_dir = _to_local_path(root_dir)
    dem_base_file = DEM_BASE_FILE
    dem_ok = bool(
        dem_base_file
        and (os.path.isfile(dem_base_file) or os.path.isdir(dem_base_file))
    )

    result: Dict[str, Any] = {
        "workflow": "dinsar",
        "root_dir": root_dir,
        "exists": bool(root_dir and os.path.isdir(root_dir)),
        "ready": False,
        "summary": {},
        "warnings": [],
    }
    if not result["exists"]:
        result["warnings"].append("root_dir does not exist.")
        return result

    task_folders = _collect_task_folders(root_dir)
    ready_count = 0
    need_import_count = 0
    missing_structure = 0

    for folder in task_folders:
        master_dir = os.path.join(folder, "master")
        slave_dir = os.path.join(folder, "slave")
        if not os.path.isdir(master_dir) or not os.path.isdir(slave_dir):
            missing_structure += 1
            continue
        master_has_sml = _has_sml(master_dir)
        slave_has_sml = _has_sml(slave_dir)
        if master_has_sml and slave_has_sml:
            ready_count += 1
        else:
            master_has_meta = bool(_find_meta_files(master_dir))
            slave_has_meta = bool(_find_meta_files(slave_dir))
            if (master_has_sml or master_has_meta) and (
                slave_has_sml or slave_has_meta
            ):
                need_import_count += 1
            else:
                missing_structure += 1

    result["summary"] = {
        "task_folder_count": len(task_folders),
        "ready_for_dinsar": ready_count,
        "need_import_first": need_import_count,
        "missing_structure": missing_structure,
        "dem_base_file": dem_base_file or "(not configured)",
        "dem_exists": dem_ok,
    }
    result["ready"] = (ready_count + need_import_count) > 0 and dem_ok
    if not dem_ok:
        result["warnings"].append(
            "DEM path not configured or does not exist. "
            "Set IDL_DINSAR_DEM_BASE_FILE in .env"
        )
    if len(task_folders) == 0:
        result["warnings"].append("No Task_* folders found.")
    if need_import_count > 0:
        result["warnings"].append(
            f"{need_import_count} folder(s) need Import first "
            f"(will be auto-imported during D-InSAR)."
        )
    return result


# ---------------------------------------------------------------------------
# System status
# ---------------------------------------------------------------------------

def get_status() -> Dict[str, Any]:
    """Return ENVI/IDL system status and DEM configuration."""
    idl_installed = bool(IDL_EXECUTABLE and os.path.isfile(IDL_EXECUTABLE))
    is_running = is_any_process_running(["idl.exe", "idlde.exe", "taskengine.exe"])
    runner_status = probe_envi_runner()

    dem_ok = bool(
        DEM_BASE_FILE
        and (os.path.isfile(DEM_BASE_FILE) or os.path.isdir(DEM_BASE_FILE))
    )

    return {
        "engine": "envipyengine",
        "idl_installed": idl_installed,
        "idl_executable": IDL_EXECUTABLE,
        "idl_running": is_running,
        "dem_base_file": DEM_BASE_FILE or "(not configured)",
        "dem_exists": dem_ok,
        "runner_python": runner_status.get("python_path", ""),
        "runner_cwd": runner_status.get("cwd", ""),
        "runner_ready": bool(runner_status.get("ready")),
        "runner_returncode": runner_status.get("returncode"),
        "runner_message": runner_status.get("message", ""),
    }


# ---------------------------------------------------------------------------
# Run history (stored as JSON files in runtime dir)
# ---------------------------------------------------------------------------

_RUNS_DIR = os.path.join(RUNTIME_DIR, "runs")


def _save_run_record(record: Dict[str, Any]) -> None:
    os.makedirs(_RUNS_DIR, exist_ok=True)
    run_id = record.get("run_id", "unknown")
    path = os.path.join(_RUNS_DIR, f"{run_id}.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(record, fp, ensure_ascii=False, indent=2)


def list_recent_runs(limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent run records, newest first."""
    if not os.path.isdir(_RUNS_DIR):
        return []
    files = sorted(
        glob(os.path.join(_RUNS_DIR, "*.json")), reverse=True
    )
    runs: List[Dict[str, Any]] = []
    for f in files[:limit]:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                runs.append(json.load(fp))
        except Exception as exc:
            print(f"[WARN] get_run_history: {exc}")
            continue
    return runs


# ---------------------------------------------------------------------------
# Top-level workflow runner (called by envi_runner_cli.py)
# ---------------------------------------------------------------------------

def run_workflow(
    workflow: str,
    root_dir: str,
    num_to_process: int = 0,
    timeout: int = DEFAULT_TIMEOUT,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a complete workflow and persist the run record."""
    timeout = min(timeout or DEFAULT_TIMEOUT, MAX_TIMEOUT)
    run_started_at = datetime.utcnow()
    run_id = run_started_at.strftime("%Y%m%d_%H%M%S") + f"_{workflow}"
    started_at = run_started_at.isoformat(timespec="seconds") + "Z"
    started = time.time()
    profile_code = None
    run_key = None

    try:
        if workflow == "import":
            result = run_import_workflow(root_dir, num_to_process, timeout)
        elif workflow == "dinsar":
            profile_code = "metatask"
            run_key = build_run_key("sarscape", profile_code, started_at=run_started_at)
            result = run_dinsar_workflow(
                root_dir,
                num_to_process,
                timeout,
                job_id=job_id,
                run_key=run_key,
                profile_code=profile_code,
                started_at=run_started_at,
            )
        elif workflow == "dinsar_custom":
            profile_code = "custom6"
            run_key = build_run_key("sarscape", profile_code, started_at=run_started_at)
            result = run_dinsar_custom_workflow(
                root_dir,
                num_to_process,
                timeout,
                job_id=job_id,
                run_key=run_key,
                profile_code=profile_code,
                started_at=run_started_at,
            )
        else:
            raise ValueError(f"Unknown workflow: {workflow}")
        status = "success"
        error = None
    except Exception as exc:
        result = {"summary": {}, "log_lines": [str(exc)]}
        status = "failed"
        error = str(exc)

    elapsed = round(time.time() - started, 1)
    finished_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    # Write log file
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    log_path = os.path.join(RUNTIME_DIR, f"{run_id}.log")
    with open(log_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(result.get("log_lines", [])))

    record = {
        "run_id": run_id,
        "workflow": workflow,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": elapsed,
        "root_dir": root_dir,
        "summary": result.get("summary", {}),
        "task_results": result.get("task_results", []),
        "output_dirs": result.get("output_dirs", []),
        "run_key": result.get("run_key") or run_key,
        "profile_code": result.get("profile_code") or profile_code,
        "log_path": log_path,
        "error": error,
    }
    _save_run_record(record)

    # Clean up progress file
    if job_id:
        try:
            pf = _progress_file_path(job_id)
            if os.path.isfile(pf):
                os.remove(pf)
        except Exception as exc:
            print(f"[WARN] run_workflow cleanup: {exc}")

    if error:
        raise RuntimeError(error)
    return record


# ---------------------------------------------------------------------------
# Disp result extraction
# ---------------------------------------------------------------------------

import re as _re
import shutil as _shutil


def _get_dinsar_target_dir() -> str:
    raw = _read_env("MONITOR_DINSAR_DIRS", "")
    dirs = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not dirs:
        raise ValueError("MONITOR_DINSAR_DIRS 未在 .env 中配置")
    return dirs[0]


def extract_disp_results(root_dir: str, dest_dir: Optional[str] = None) -> Dict[str, Any]:
    from .envi_extract import extract_disp_results as _extract_disp_results

    return _extract_disp_results(root_dir, dest_dir)
    """将 Task_*/dinsar_results/ 下最新的 disp 文件复制到目标目录并重命名。

    Args:
        root_dir: 生产根目录（包含 Task_* 文件夹）
        dest_dir: 目标目录（可选，默认使用 MONITOR_DINSAR_DIRS[0]）
    """
    if dest_dir:
        target_dir = _to_local_path(dest_dir)
        if not os.path.isdir(target_dir):
            raise ValueError(f"目标目录不存在: {dest_dir}")
    else:
        target_dir = _get_dinsar_target_dir()
    os.makedirs(target_dir, exist_ok=True)

    _pattern = _re.compile(r"out_ISARPTD_(\d{14})_rsp_disp$")
    _extensions = ["", ".hdr", ".sml"]

    processed = 0
    copied = 0
    skipped = 0
    overwritten = 0
    failed = 0
    details: List[Dict[str, Any]] = []

    task_dirs = sorted(
        d for d in os.listdir(root_dir)
        if d.startswith("Task_") and os.path.isdir(os.path.join(root_dir, d))
    )

    for task_name in task_dirs:
        dinsar_results_dir = os.path.join(root_dir, task_name, "dinsar_results")
        if not os.path.isdir(dinsar_results_dir):
            continue

        # Find all no-extension files matching the pattern
        candidates = []
        try:
            for fname in os.listdir(dinsar_results_dir):
                m = _pattern.match(fname)
                if m and os.path.isfile(os.path.join(dinsar_results_dir, fname)):
                    candidates.append((m.group(1), fname))
        except OSError:
            continue

        if not candidates:
            continue

        # Pick the one with the largest timestamp
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, best_fname = candidates[0]
        base_src = os.path.join(dinsar_results_dir, best_fname)
        target_base = os.path.join(target_dir, f"{task_name}_geo_disp")

        task_status = "ok"
        task_copied = 0
        task_skipped = 0
        task_overwritten = 0
        task_failed = 0

        for ext in _extensions:
            src = base_src + ext
            dst = target_base + ext
            if not os.path.isfile(src):
                task_failed += 1
                continue
            if os.path.isfile(dst):
                if os.path.getsize(src) == os.path.getsize(dst):
                    task_skipped += 1
                    skipped += 1
                    continue
                else:
                    try:
                        _shutil.copy2(src, dst)
                        task_overwritten += 1
                        overwritten += 1
                    except OSError as e:
                        task_failed += 1
                        task_status = f"error: {e}"
            else:
                try:
                    _shutil.copy2(src, dst)
                    task_copied += 1
                    copied += 1
                except OSError as e:
                    task_failed += 1
                    task_status = f"error: {e}"

        failed += task_failed
        processed += 1
        details.append({
            "task": task_name,
            "target_name": f"{task_name}_geo_disp",
            "status": task_status,
            "copied": task_copied,
            "skipped": task_skipped,
            "overwritten": task_overwritten,
            "failed": task_failed,
        })

    return {
        "processed": processed,
        "copied": copied,
        "skipped": skipped,
        "overwritten": overwritten,
        "failed": failed,
        "target_dir": target_dir,
        "details": details,
    }


def get_task_overview(root_dir: str) -> Dict[str, Any]:
    from .envi_extract import get_task_overview as _get_task_overview

    return _get_task_overview(root_dir)
    """扫描 root_dir 下所有 Task_* 文件夹，返回每个 Task 的处理状态。"""
    try:
        target_dir = _get_dinsar_target_dir()
    except ValueError:
        target_dir = None

    if not os.path.isdir(root_dir):
        raise ValueError(f"目录不存在: {root_dir}")

    task_dirs = sorted(
        d for d in os.listdir(root_dir)
        if d.startswith("Task_") and os.path.isdir(os.path.join(root_dir, d))
    )

    tasks = []
    summary = {"total": 0, "imported": 0, "dinsar_done": 0, "extracted": 0}

    for task_name in task_dirs:
        task_path = os.path.join(root_dir, task_name)

        has_master = os.path.isdir(os.path.join(task_path, "master"))
        has_slave = os.path.isdir(os.path.join(task_path, "slave"))
        has_structure = has_master and has_slave

        imported = False
        for sub in ("master", "slave"):
            sub_path = os.path.join(task_path, sub)
            if os.path.isdir(sub_path):
                if any(f.endswith(".sml") for f in os.listdir(sub_path)):
                    imported = True
                    break

        dinsar_results_dir = os.path.join(task_path, "dinsar_results")
        dinsar_done = False
        if os.path.isdir(dinsar_results_dir):
            dinsar_done = any(
                _re.search(r"ISARPTD.*_rsp_disp$", f)
                for f in os.listdir(dinsar_results_dir)
                if os.path.isfile(os.path.join(dinsar_results_dir, f))
            )

        extracted = False
        if target_dir:
            extracted = os.path.isfile(os.path.join(target_dir, f"{task_name}_geo_disp"))

        try:
            mtime = os.path.getmtime(task_path)
            last_modified = datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            last_modified = "-"

        tasks.append({
            "task_name": task_name,
            "has_structure": has_structure,
            "imported": imported,
            "dinsar_done": dinsar_done,
            "extracted": extracted,
            "last_modified": last_modified,
        })

        summary["total"] += 1
        if imported:
            summary["imported"] += 1
        if dinsar_done:
            summary["dinsar_done"] += 1
        if extracted:
            summary["extracted"] += 1

    return {"tasks": tasks, "summary": summary, "target_dir": target_dir or ""}


# ---------------------------------------------------------------------------
# Backward-compatible re-exports from split modules
# These lazy imports avoid circular-import issues at module load time.
# Callers that import run_import_workflow etc. directly from envi_service
# will still work without any changes.
# ---------------------------------------------------------------------------

def __getattr__(name: str):  # noqa: N807
    _split_map = {
        "run_import_workflow": ("envi_import", "run_import_workflow"),
        "inspect_import": ("envi_import", "inspect_import"),
        "run_dinsar_workflow": ("envi_dinsar", "run_dinsar_workflow"),
        "run_dinsar_custom_workflow": ("envi_dinsar", "run_dinsar_custom_workflow"),
        "inspect_dinsar": ("envi_dinsar", "inspect_dinsar"),
        "extract_disp_results": ("envi_extract", "extract_disp_results"),
        "get_task_overview": ("envi_extract", "get_task_overview"),
    }
    if name in _split_map:
        mod_name, attr = _split_map[name]
        import importlib
        mod = importlib.import_module(f".{mod_name}", package=__name__.rsplit(".", 1)[0])
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
