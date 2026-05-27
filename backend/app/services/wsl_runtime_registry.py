from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from ..config import settings


def _windows_path_to_wsl_mount(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    drive, tail = os.path.splitdrive(os.path.normpath(text))
    if not drive:
        return text.replace("\\", "/")
    drive_letter = drive.rstrip(":").lower()
    normalized_tail = tail.replace("\\", "/")
    return f"/mnt/{drive_letter}/{normalized_tail}"


def _project_file_windows(*relative_parts: str) -> str:
    return os.path.normpath(str(Path(settings.PROJECT_ROOT, *relative_parts)))


@dataclass(frozen=True)
class WslRuntimeDefinition:
    runtime_id: str
    engine_code: str
    display_name: str
    distro: str
    conda_env_name: str
    python_path: str
    runner_path_windows: str
    runner_path_wsl: str
    allowed_operations: Tuple[str, ...] = ()
    env_profile_path_windows: str = ""
    env_profile_path_wsl: str = ""
    metadata_json: Dict[str, Any] = field(default_factory=dict)

    def entrypoint_argv(self) -> list[str]:
        return [self.python_path, self.runner_path_wsl]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "engine_code": self.engine_code,
            "display_name": self.display_name,
            "distro": self.distro,
            "conda_env_name": self.conda_env_name,
            "python_path": self.python_path,
            "runner_path_windows": self.runner_path_windows,
            "runner_path_wsl": self.runner_path_wsl,
            "allowed_operations": list(self.allowed_operations),
            "env_profile_path_windows": self.env_profile_path_windows,
            "env_profile_path_wsl": self.env_profile_path_wsl,
            "metadata_json": dict(self.metadata_json or {}),
        }


@dataclass(frozen=True)
class WslRuntimeRegistry:
    shared_distro: str
    shared_conda_env_name: str
    shared_python_path: str
    broker_job_root_windows: str
    broker_job_root_wsl: str
    runtimes: Mapping[str, WslRuntimeDefinition]

    def require(self, runtime_id: str) -> WslRuntimeDefinition:
        key = str(runtime_id or "").strip()
        runtime = self.runtimes.get(key)
        if runtime is None:
            known_ids = ", ".join(sorted(self.runtimes.keys()))
            raise KeyError(f"Unknown WSL runtime_id '{runtime_id}'. Known: {known_ids}")
        return runtime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shared_distro": self.shared_distro,
            "shared_conda_env_name": self.shared_conda_env_name,
            "shared_python_path": self.shared_python_path,
            "broker_job_root_windows": self.broker_job_root_windows,
            "broker_job_root_wsl": self.broker_job_root_wsl,
            "runtimes": {
                runtime_id: runtime.to_dict()
                for runtime_id, runtime in sorted(self.runtimes.items())
            },
        }


def build_wsl_runtime_registry() -> WslRuntimeRegistry:
    shared_distro = str(
        settings.WSL_DISTRO
        or settings.ISCE2_WSL_DISTRO
        or settings.PYINT_WSL_DISTRO
        or "Ubuntu-24.04"
    ).strip()
    shared_conda_env = str(settings.WSL_SHARED_CONDA_ENV or "insar_wsl_v1").strip() or "insar_wsl_v1"
    shared_python_path = str(
        settings.WSL_SHARED_PYTHON
        or settings.ISCE2_PYTHON
        or settings.PYINT_WSL_PYTHON
        or f"/home/administrator/miniconda3/envs/{shared_conda_env}/bin/python"
    ).strip()
    broker_job_root_windows = os.path.normpath(
        settings.WSL_BROKER_JOB_ROOT
        or os.path.join(settings.BACKEND_DIR, "runtime", "wsl_jobs")
    )
    broker_job_root_wsl = _windows_path_to_wsl_mount(broker_job_root_windows)

    isce2_runner_windows = _project_file_windows("deploy", "wsl", "runners", "isce2_runner.py")
    gamma_runner_windows = _project_file_windows("deploy", "wsl", "runners", "gamma_pyint_runner.py")
    gamma_sbas_runner_windows = _project_file_windows("deploy", "wsl", "runners", "gamma_sbas_runner.py")
    gamma_profile_windows = _project_file_windows("deploy", "wsl", "profiles", "gamma_env.sh")

    runtimes = {
        settings.ISCE2_RUNTIME_ID: WslRuntimeDefinition(
            runtime_id=settings.ISCE2_RUNTIME_ID,
            engine_code="isce2",
            display_name="ISCE2 Runtime V1",
            distro=shared_distro,
            conda_env_name=shared_conda_env,
            python_path=shared_python_path,
            runner_path_windows=isce2_runner_windows,
            runner_path_wsl=_windows_path_to_wsl_mount(isce2_runner_windows),
            allowed_operations=("lt1_stripmap",),
            metadata_json={
                "shared_runtime": True,
                "legacy_distro_env_var": "ISCE2_WSL_DISTRO",
                "legacy_python_env_var": "ISCE2_PYTHON",
                "legacy_pipeline_env_var": "ISCE2_PIPELINE_SCRIPT",
            },
        ),
        settings.PYINT_RUNTIME_ID: WslRuntimeDefinition(
            runtime_id=settings.PYINT_RUNTIME_ID,
            engine_code="pyint",
            display_name="Gamma / PyINT Runtime V1",
            distro=shared_distro,
            conda_env_name=shared_conda_env,
            python_path=shared_python_path,
            runner_path_windows=gamma_runner_windows,
            runner_path_wsl=_windows_path_to_wsl_mount(gamma_runner_windows),
            allowed_operations=("lt1_gamma_dinsar", "gamma_refine"),
            env_profile_path_windows=gamma_profile_windows,
            env_profile_path_wsl=_windows_path_to_wsl_mount(gamma_profile_windows),
            metadata_json={
                "shared_runtime": True,
                "legacy_distro_env_var": "PYINT_WSL_DISTRO",
                "legacy_python_env_var": "PYINT_WSL_PYTHON",
                "legacy_profile_env_var": "PYINT_GAMMA_ENV_SCRIPT",
            },
        ),
        settings.GAMMA_SBAS_RUNTIME_ID: WslRuntimeDefinition(
            runtime_id=settings.GAMMA_SBAS_RUNTIME_ID,
            engine_code="gamma",
            display_name="Gamma SBAS Runtime V1",
            distro=str(settings.GAMMA_SBAS_WSL_DISTRO or shared_distro).strip() or shared_distro,
            conda_env_name=shared_conda_env,
            python_path=str(settings.GAMMA_SBAS_PYTHON or shared_python_path).strip() or shared_python_path,
            runner_path_windows=gamma_sbas_runner_windows,
            runner_path_wsl=_windows_path_to_wsl_mount(gamma_sbas_runner_windows),
            allowed_operations=("lt1_gamma_sbas_workflow", "lt1_gamma_sbas_step"),
            env_profile_path_windows=str(settings.GAMMA_SBAS_ENV_SCRIPT or gamma_profile_windows).strip(),
            env_profile_path_wsl=_windows_path_to_wsl_mount(
                str(settings.GAMMA_SBAS_ENV_SCRIPT or gamma_profile_windows).strip()
            ),
            metadata_json={
                "shared_runtime": True,
                "workflow_code": "sbas_insar",
                "processor_code": "gamma_ipta_sbas",
                "env_vars": {
                    "runtime_id": "GAMMA_SBAS_RUNTIME_ID",
                    "python": "GAMMA_SBAS_PYTHON",
                    "env_profile": "GAMMA_SBAS_ENV_SCRIPT",
                    "work_root": "GAMMA_SBAS_WORK_ROOT",
                    "product_root": "GAMMA_SBAS_PRODUCT_ROOT",
                },
            },
        ),
    }

    return WslRuntimeRegistry(
        shared_distro=shared_distro,
        shared_conda_env_name=shared_conda_env,
        shared_python_path=shared_python_path,
        broker_job_root_windows=broker_job_root_windows,
        broker_job_root_wsl=broker_job_root_wsl,
        runtimes=runtimes,
    )


def get_wsl_runtime(runtime_id: str) -> WslRuntimeDefinition:
    return wsl_runtime_registry.require(runtime_id)


wsl_runtime_registry = build_wsl_runtime_registry()
