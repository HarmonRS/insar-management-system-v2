"""PyINT D-InSAR engine backed by a WSL wrapper pipeline."""
from __future__ import annotations

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..config import get_env_text, read_bool_env, settings
from ..services.dinsar_naming import write_run_metadata
from ..services.pyint_input_assets_service import (
    get_pyint_dem_summary,
    get_pyint_orbit_context,
    materialize_pyint_input_assets,
    resolve_pyint_task_input_assets,
)
from ..services.pyint_service import (
    DEFAULT_AZIMUTH_LOOKS,
    DEFAULT_PARALLEL_WORKERS,
    DEFAULT_RANGE_LOOKS,
    MAX_LOOKS,
    MAX_PARALLEL_WORKERS,
    build_project_name,
    check_pyint_environment,
    infer_scene_date_from_archives,
    infer_task_identity,
    quote_shell,
    resolve_gamma_env_script,
    resolve_time_baseline_days,
    to_wsl_path,
    validate_pyint_root_dir,
)
from ..services.wsl_service import run_wsl_command
from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult

RERUN_MODE_UNFINISHED_ONLY = "unfinished_only"


def _read_env(name: str, default: str = "") -> str:
    return get_env_text(name, default) or default


def _read_bool_env(name: str, default: bool = False) -> bool:
    return read_bool_env(name, default)


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


def _normalize_rerun_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized == RERUN_MODE_UNFINISHED_ONLY else "rerun_all"


class PyintEngine(DinsarEngine):
    @property
    def engine_code(self) -> str:
        return "pyint"

    @property
    def engine_label(self) -> str:
        return "PyINT / Gamma"

    @property
    def default_timeout_seconds(self) -> int:
        return max(60, int(settings.PYINT_DEFAULT_TIMEOUT_SECONDS or 43200))

    @property
    def _enabled(self) -> bool:
        return _read_bool_env("PYINT_ENABLED", False)

    @property
    def _distro(self) -> str:
        return _read_env("PYINT_WSL_DISTRO", settings.ISCE2_WSL_DISTRO)

    @property
    def _python(self) -> str:
        return _read_env("PYINT_WSL_PYTHON", settings.ISCE2_PYTHON)

    @property
    def _pyint_home(self) -> str:
        return _read_env("PYINT_HOME", "")

    @property
    def _pyint_app_script(self) -> str:
        explicit = _read_env("PYINT_APP_SCRIPT", "")
        if explicit:
            return explicit
        home = self._pyint_home
        if not home:
            return ""
        return os.path.join(home, "pyint", "pyintApp.py")

    @property
    def _template_root(self) -> str:
        return _read_env("PYINT_TEMPLATE_ROOT", "")

    @property
    def _work_root(self) -> str:
        return _read_env("PYINT_WORK_ROOT", "")

    @property
    def _output_root(self) -> str:
        return _read_env("PYINT_OUTPUT_ROOT", "")

    @property
    def _dem_root(self) -> str:
        return _read_env("PYINT_DEM_ROOT", "")

    @property
    def _dem_mode(self) -> str:
        return str(getattr(settings, "PYINT_DEM_MODE", "local_fabdem") or "local_fabdem").strip().lower()

    @property
    def _fabdem_root(self) -> str:
        return _read_env("PYINT_FABDEM_ROOT", "")

    @property
    def _opentopo_dem_type(self) -> str:
        return _read_env("PYINT_OPENTOPO_DEM_TYPE", "SRTMGL1")

    @property
    def _opentopo_api_key(self) -> str:
        return _read_env("PYINT_OPENTOPO_API_KEY", "")

    @property
    def _orbit_policy(self) -> str:
        return str(getattr(settings, "PYINT_ORBIT_POLICY", "require_txt") or "require_txt").strip().lower()

    @property
    def _orbit_pool_txt(self) -> str:
        return _read_env("PYINT_ORBIT_POOL_TXT", settings.ORBIT_POOL_ENVI)

    @property
    def _record_input_assets(self) -> bool:
        return _read_bool_env("PYINT_RECORD_INPUT_ASSETS", True)

    @property
    def _gamma_env_script(self) -> str:
        return resolve_gamma_env_script()

    @property
    def _lt1_precise_orbit_enabled(self) -> bool:
        return _read_bool_env("PYINT_LT1_PRECISE_ORBIT_ENABLED", True)

    @property
    def _lt1_precise_orbit_mode(self) -> str:
        return str(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_MODE", "replace") or "replace").strip().lower()

    @property
    def _lt1_precise_orbit_strict(self) -> bool:
        return _read_bool_env("PYINT_LT1_PRECISE_ORBIT_STRICT", True)

    @property
    def _lt1_precise_orbit_validate_with_orb_filt(self) -> bool:
        return _read_bool_env("PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT", False)

    @property
    def _lt1_precise_orbit_backup(self) -> bool:
        return _read_bool_env("PYINT_LT1_PRECISE_ORBIT_BACKUP", True)

    @property
    def _lt1_precise_orbit_orb_filt_degree(self) -> int:
        return max(1, int(getattr(settings, "PYINT_LT1_PRECISE_ORBIT_ORB_FILT_DEGREE", 5) or 5))

    @property
    def _smoke_test(self) -> bool:
        return _read_bool_env("PYINT_SMOKE_TEST_ENABLED", False)

    @property
    def _pipeline_script(self) -> str:
        local_script = (
            Path(__file__).resolve().parent.parent
            / "pyint_pipeline"
            / "run_lt1_pyint_pipeline.py"
        )
        return _windows_path_to_wsl_mount(str(local_script))

    def get_profiles(self) -> List[EngineProfile]:
        return [
            EngineProfile(
                code="lt1_gamma_dinsar",
                label="LT-1 Gamma D-InSAR",
                description="Use PyINT + Gamma in WSL for single-pair LT-1 D-InSAR processing.",
                params_schema={
                    "force": {
                        "label": "强制重跑",
                        "type": "boolean",
                        "default": False,
                        "description": "删除当前 run_key 对应的工作区后重跑。",
                    },
                    "range_looks": {
                        "label": "距离向多视",
                        "type": "number",
                        "default": DEFAULT_RANGE_LOOKS,
                        "step": 1,
                        "min": 1,
                        "max": MAX_LOOKS,
                        "description": "PyINT 模板中的 range_looks。",
                    },
                    "azimuth_looks": {
                        "label": "方位向多视",
                        "type": "number",
                        "default": DEFAULT_AZIMUTH_LOOKS,
                        "step": 1,
                        "min": 1,
                        "max": MAX_LOOKS,
                        "description": "PyINT 模板中的 azimuth_looks。",
                    },
                    "parallel_workers": {
                        "label": "并行数",
                        "type": "number",
                        "default": DEFAULT_PARALLEL_WORKERS,
                        "step": 1,
                        "min": 1,
                        "max": MAX_PARALLEL_WORKERS,
                        "description": "同步控制 raw2slc/coreg/diff/unwrap/geocode 的并行数。",
                    },
                    "unwrap": {
                        "label": "执行解缠",
                        "type": "boolean",
                        "default": True,
                        "description": "关闭后仅做到差分干涉图，不做解缠。",
                    },
                    "geocode": {
                        "label": "执行地理编码",
                        "type": "boolean",
                        "default": True,
                        "description": "关闭后不导出地理编码结果。",
                    },
                },
            ),
        ]

    def normalize_extra(self, extra: Dict[str, Any] | None) -> Dict[str, Any]:
        normalized: Dict[str, Any] = dict(extra or {})

        def _coerce_bool(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            text = str(value or "").strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off", ""}:
                return False
            return bool(value)

        for key in ("force", "unwrap", "geocode"):
            if key in normalized:
                normalized[key] = _coerce_bool(normalized[key])

        for key, maximum, label in (
            ("range_looks", MAX_LOOKS, "距离向多视"),
            ("azimuth_looks", MAX_LOOKS, "方位向多视"),
            ("parallel_workers", MAX_PARALLEL_WORKERS, "并行数"),
        ):
            if key not in normalized or normalized[key] is None:
                continue
            try:
                parsed = int(normalized[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}必须为整数。") from exc
            if parsed < 1 or parsed > maximum:
                raise ValueError(f"{label}必须在 1 到 {maximum} 之间。")
            normalized[key] = parsed

        return normalized

    def _has_completed_task_result(self, task_dir: str, profile_code: str) -> bool:
        task_identity = infer_task_identity(task_dir)
        pair_key = task_identity["pair_key"]
        output_root = self._output_root or os.path.join(task_dir, "pyint_output")
        runs_root = os.path.join(output_root, pair_key, "runs")
        if not os.path.isdir(runs_root):
            return False

        with os.scandir(runs_root) as entries:
            run_dirs = [entry.path for entry in entries if entry.is_dir()]
        run_dirs.sort(key=lambda path: os.path.basename(path).lower(), reverse=True)

        for run_dir in run_dirs:
            metadata_path = os.path.join(run_dir, "native", ".dinsar_run.json")
            if not os.path.isfile(metadata_path):
                metadata_path = os.path.join(run_dir, ".dinsar_run.json")
                if not os.path.isfile(metadata_path):
                    continue
            try:
                with open(metadata_path, "r", encoding="utf-8") as fp:
                    metadata = json.load(fp) or {}
            except Exception:
                continue
            if str(metadata.get("engine_code") or "").strip().lower() != self.engine_code:
                continue
            if str(metadata.get("profile_code") or "").strip() != str(profile_code or "").strip():
                continue
            output_dir = str(metadata.get("output_dir") or os.path.join(run_dir, "native")).strip()
            if output_dir and os.path.isdir(output_dir):
                return True
        return False

    def validate_root_dir(
        self,
        root_dir: str,
        num_to_process: int = 0,
        rerun_mode: str = "rerun_all",
    ) -> Dict[str, Any]:
        validation = validate_pyint_root_dir(root_dir, 0)
        task_dirs: List[str] = list(validation.get("task_dirs") or [])
        discovered_task_count = len(task_dirs)
        skipped_completed_count = 0

        if _normalize_rerun_mode(rerun_mode) == RERUN_MODE_UNFINISHED_ONLY:
            filtered_task_dirs: List[str] = []
            for task_dir in task_dirs:
                if self._has_completed_task_result(task_dir, "lt1_gamma_dinsar"):
                    skipped_completed_count += 1
                    continue
                filtered_task_dirs.append(task_dir)
            task_dirs = filtered_task_dirs

        selected_count = int(num_to_process or 0)
        if selected_count > 0:
            task_dirs = task_dirs[:selected_count]

        return {
            **validation,
            "task_dirs": task_dirs,
            "task_count": len(task_dirs),
            "selected_task_count": len(task_dirs),
            "discovered_task_count": discovered_task_count,
            "skipped_completed_count": skipped_completed_count,
        }

    def check_available(self) -> EngineAvailability:
        report = check_pyint_environment(
            enabled=self._enabled,
            distro=self._distro,
            python_cmd=self._python,
            pyint_home=self._pyint_home,
            pyint_app_script=self._pyint_app_script,
            template_root=self._template_root,
            work_root=self._work_root,
            output_root=self._output_root,
            dem_root=self._dem_root,
            gamma_env_script=self._gamma_env_script,
            smoke_test=self._smoke_test,
        )
        checks_list = [
            {
                "name": check.name,
                "ok": check.ok,
                "detail": check.detail,
                "skipped": check.skipped,
            }
            for check in report.checks
        ]
        if report.overall_ok:
            status = "ok"
            available = True
        else:
            critical_failed = [check for check in report.checks if not check.ok and not check.skipped]
            status = "degraded" if critical_failed else "unavailable"
            available = False
        return EngineAvailability(
            engine_code=self.engine_code,
            status=status,
            available=available,
            checks=checks_list,
            message=report.message,
        )

    def run(self, request: RunRequest) -> RunResult:
        if not self._enabled:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error="PyINT is disabled.",
            )

        if request.profile != "lt1_gamma_dinsar":
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error=f"Unknown profile: {request.profile}",
            )

        return self._run_lt1_gamma_dinsar(request)

    def _run_lt1_gamma_dinsar(self, request: RunRequest) -> RunResult:
        extra = self.normalize_extra(request.extra)
        validation = self.validate_root_dir(
            request.root_dir,
            request.num_to_process,
            str((request.extra or {}).get("__rerun_mode") or "rerun_all"),
        )
        task_dirs: List[str] = validation["task_dirs"]
        total_tasks = len(task_dirs)
        run_started_at = datetime.utcnow()
        run_started_at_text = run_started_at.isoformat(timespec="seconds") + "Z"
        run_key = f"run_{run_started_at.strftime('%Y%m%dT%H%M%SZ')}_{self.engine_code}_{request.profile}"
        progress_callback = request.progress_callback

        def emit_progress(event_type: str, **payload: Any) -> None:
            if not callable(progress_callback):
                return
            try:
                progress_callback({"event": event_type, **payload})
            except Exception:
                return

        timeout = max(60, int(request.timeout_seconds or self.default_timeout_seconds))
        force = bool(extra.get("force"))
        range_looks = int(extra.get("range_looks", DEFAULT_RANGE_LOOKS))
        azimuth_looks = int(extra.get("azimuth_looks", DEFAULT_AZIMUTH_LOOKS))
        parallel_workers = int(extra.get("parallel_workers", DEFAULT_PARALLEL_WORKERS))
        unwrap = bool(extra.get("unwrap", True))
        geocode = bool(extra.get("geocode", True))

        wsl_pyint_home = to_wsl_path(self._pyint_home)
        wsl_pyint_app = to_wsl_path(self._pyint_app_script)
        wsl_dem_root = to_wsl_path(self._dem_root)
        wsl_fabdem_root = to_wsl_path(self._fabdem_root) if self._fabdem_root else ""
        wsl_orbit_pool = to_wsl_path(self._orbit_pool_txt) if self._orbit_pool_txt else ""
        shared_dem_summary = get_pyint_dem_summary()
        prepared_dem_path = str(shared_dem_summary.get("prepared_dem_path") or "").strip()
        prepared_dem_kind = str(shared_dem_summary.get("prepared_dem_kind") or "").strip()
        wsl_prepared_dem_path = to_wsl_path(prepared_dem_path) if prepared_dem_path else ""
        shared_orbit_context = get_pyint_orbit_context()

        task_results: List[Dict[str, Any]] = []
        output_dirs: List[str] = []
        pairs_processed = 0
        pairs_failed = 0

        for pair_index, task_dir in enumerate(task_dirs, start=1):
            task_identity = infer_task_identity(task_dir)
            task_name = task_identity["task_name"]
            task_alias = task_identity["task_alias"]
            pair_key = task_identity["pair_key"]
            pair_meta = task_identity["pair_meta"]
            master_date = task_identity["master_date"]
            slave_date = task_identity["slave_date"]

            work_run_root = os.path.normpath(os.path.join(self._work_root, pair_key, run_key))
            output_dir = os.path.normpath(os.path.join(self._output_root, pair_key, "runs", run_key, "native"))
            template_root = os.path.normpath(os.path.join(self._template_root, pair_key, run_key))
            project_name = build_project_name(pair_key, run_key)
            project_dir = os.path.join(work_run_root, project_name)
            input_assets_dir = os.path.join(work_run_root, "input_assets")

            wsl_task_dir = to_wsl_path(task_dir)
            wsl_project_dir = to_wsl_path(project_dir)
            wsl_output_dir = to_wsl_path(output_dir)
            wsl_template_root = to_wsl_path(template_root)

            emit_progress(
                "pair_started",
                pair_index=pair_index,
                pair_total=total_tasks,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                task_dir=task_dir,
                work_dir=work_run_root,
                output_dir=output_dir,
            )

            if not all((wsl_task_dir, wsl_project_dir, wsl_output_dir, wsl_template_root, wsl_pyint_home, wsl_pyint_app, wsl_dem_root)):
                pairs_failed += 1
                error_text = "Unable to convert PyINT paths to WSL paths."
                emit_progress(
                    "pair_finished",
                    pair_index=pair_index,
                    pair_total=total_tasks,
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    success=False,
                    returncode=-2,
                    error=error_text,
                )
                task_results.append(
                    {
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "pair_key": pair_key,
                        "run_key": run_key,
                        "task_dir": task_dir,
                        "work_dir": work_run_root,
                        "project_dir": project_dir,
                        "output_dir": output_dir,
                        "success": False,
                        "returncode": -2,
                        "error": error_text,
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "command": "",
                        "wsl_task_dir": wsl_task_dir,
                        "wsl_project_dir": wsl_project_dir,
                        "wsl_output_dir": wsl_output_dir,
                    }
                )
                continue

            archives = self._discover_archives(task_dir)
            master_archives = archives.get("master", [])
            slave_archives = archives.get("slave", [])
            if not master_date:
                master_date = infer_scene_date_from_archives(master_archives)
            if not slave_date:
                slave_date = infer_scene_date_from_archives(slave_archives)
            time_baseline_days = resolve_time_baseline_days(master_date, slave_date, pair_meta)
            try:
                task_input_assets = resolve_pyint_task_input_assets(
                    task_dir,
                    dem_summary=shared_dem_summary,
                    orbit_context=shared_orbit_context,
                )
            except Exception as exc:
                pairs_failed += 1
                error_text = f"Failed to resolve PyINT input assets: {exc}"
                emit_progress(
                    "pair_finished",
                    pair_index=pair_index,
                    pair_total=total_tasks,
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    success=False,
                    returncode=-3,
                    error=error_text,
                )
                task_results.append(
                    {
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "pair_key": pair_key,
                        "run_key": run_key,
                        "task_dir": task_dir,
                        "work_dir": work_run_root,
                        "project_dir": project_dir,
                        "output_dir": output_dir,
                        "success": False,
                        "returncode": -3,
                        "error": error_text,
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "command": "",
                        "wsl_task_dir": wsl_task_dir,
                        "wsl_project_dir": wsl_project_dir,
                        "wsl_output_dir": wsl_output_dir,
                    }
                )
                continue

            if not task_input_assets.get("allow_submit"):
                pairs_failed += 1
                error_text = "; ".join(task_input_assets.get("blockers") or []) or "PyINT input assets are incomplete."
                emit_progress(
                    "pair_finished",
                    pair_index=pair_index,
                    pair_total=total_tasks,
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    success=False,
                    returncode=-4,
                    error=error_text,
                )
                task_results.append(
                    {
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "pair_key": pair_key,
                        "run_key": run_key,
                        "task_dir": task_dir,
                        "work_dir": work_run_root,
                        "project_dir": project_dir,
                        "output_dir": output_dir,
                        "success": False,
                        "returncode": -4,
                        "error": error_text,
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "command": "",
                        "input_assets": task_input_assets.get("input_assets"),
                        "wsl_task_dir": wsl_task_dir,
                        "wsl_project_dir": wsl_project_dir,
                        "wsl_output_dir": wsl_output_dir,
                    }
                )
                continue

            try:
                materialized_input_assets = materialize_pyint_input_assets(
                    task_summary=task_input_assets,
                    input_assets_dir=input_assets_dir,
                    project_name=project_name,
                )
            except Exception as exc:
                pairs_failed += 1
                error_text = f"Failed to materialize PyINT input assets: {exc}"
                emit_progress(
                    "pair_finished",
                    pair_index=pair_index,
                    pair_total=total_tasks,
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    success=False,
                    returncode=-5,
                    error=error_text,
                )
                task_results.append(
                    {
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "pair_key": pair_key,
                        "run_key": run_key,
                        "task_dir": task_dir,
                        "work_dir": work_run_root,
                        "project_dir": project_dir,
                        "output_dir": output_dir,
                        "success": False,
                        "returncode": -5,
                        "error": error_text,
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "command": "",
                        "input_assets": task_input_assets.get("input_assets"),
                        "wsl_task_dir": wsl_task_dir,
                        "wsl_project_dir": wsl_project_dir,
                        "wsl_output_dir": wsl_output_dir,
                    }
                )
                continue

            input_assets_summary = materialized_input_assets.get("input_assets") or task_input_assets.get("input_assets") or {}
            wsl_input_assets_dir = (
                to_wsl_path(materialized_input_assets.get("input_assets_dir", ""))
                if materialized_input_assets.get("input_assets_dir")
                else ""
            )
            wsl_input_assets_json = (
                to_wsl_path(materialized_input_assets.get("task_manifest_path", ""))
                if materialized_input_assets.get("task_manifest_path")
                else ""
            )

            cmd_parts = [
                f"{quote_shell(self._python)} {quote_shell(self._pipeline_script)} {quote_shell(wsl_task_dir)}",
                f"--project-dir {quote_shell(wsl_project_dir)}",
                f"--template-root {quote_shell(wsl_template_root)}",
                f"--output-dir {quote_shell(wsl_output_dir)}",
                f"--pyint-home {quote_shell(wsl_pyint_home)}",
                f"--pyint-app-script {quote_shell(wsl_pyint_app)}",
                f"--python {quote_shell(self._python)}",
                f"--dem-root {quote_shell(wsl_dem_root)}",
                f"--dem-mode {quote_shell(self._dem_mode)}",
                f"--project-name {quote_shell(project_name)}",
                f"--pair-key {quote_shell(pair_key)}",
                f"--task-alias {quote_shell(task_alias)}",
                f"--orbit-policy {quote_shell(self._orbit_policy)}",
                f"--range-looks {range_looks}",
                f"--azimuth-looks {azimuth_looks}",
                f"--parallel-workers {parallel_workers}",
                f"--master-date {quote_shell(master_date)}" if master_date else "",
                f"--slave-date {quote_shell(slave_date)}" if slave_date else "",
                f"--time-baseline-days {time_baseline_days}",
                f"--input-assets-dir {quote_shell(wsl_input_assets_dir)}" if wsl_input_assets_dir else "",
                f"--input-assets-json {quote_shell(wsl_input_assets_json)}" if wsl_input_assets_json else "",
                f"--lt1-precise-orbit-enabled {'true' if self._lt1_precise_orbit_enabled else 'false'}",
                f"--lt1-precise-orbit-mode {quote_shell(self._lt1_precise_orbit_mode)}",
                f"--lt1-precise-orbit-strict {'true' if self._lt1_precise_orbit_strict else 'false'}",
                (
                    f"--lt1-precise-orbit-validate-with-orb-filt "
                    f"{'true' if self._lt1_precise_orbit_validate_with_orb_filt else 'false'}"
                ),
                f"--lt1-precise-orbit-backup {'true' if self._lt1_precise_orbit_backup else 'false'}",
                f"--lt1-precise-orbit-orb-filt-degree {self._lt1_precise_orbit_orb_filt_degree}",
                "--unwrap" if unwrap else "--no-unwrap",
                "--geocode" if geocode else "--no-geocode",
            ]
            if self._dem_mode == "local_fabdem" and wsl_fabdem_root:
                cmd_parts.append(f"--fabdem-root {quote_shell(wsl_fabdem_root)}")
            if self._dem_mode == "prepared_file" and wsl_prepared_dem_path:
                cmd_parts.append(f"--prepared-dem-path {quote_shell(wsl_prepared_dem_path)}")
            if self._dem_mode == "opentopo":
                if self._opentopo_dem_type:
                    cmd_parts.append(f"--opentopo-dem-type {quote_shell(self._opentopo_dem_type)}")
                if self._opentopo_api_key:
                    cmd_parts.append(f"--opentopo-api-key {quote_shell(self._opentopo_api_key)}")
            if self._gamma_env_script:
                cmd_parts.append(f"--gamma-env-script {quote_shell(to_wsl_path(self._gamma_env_script))}")
            if force:
                cmd_parts.append("--force")

            cmd = " ".join(part for part in cmd_parts if part)
            rc, stdout, stderr = run_wsl_command(
                cmd,
                distro=self._distro,
                timeout=timeout,
            )

            success = rc == 0
            if success:
                pairs_processed += 1
                os.makedirs(output_dir, exist_ok=True)
                write_run_metadata(
                    output_dir,
                    {
                        "run_key": run_key,
                        "pair_key": pair_key,
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "engine_code": self.engine_code,
                        "profile_code": request.profile,
                        "source_root": os.path.normpath(request.root_dir),
                        "task_dir": os.path.normpath(task_dir),
                        "work_dir": work_run_root,
                        "output_dir": output_dir,
                        "project_dir": project_dir,
                        "started_at": run_started_at_text,
                        "finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                        "params": {
                            "force": force,
                            "range_looks": range_looks,
                            "azimuth_looks": azimuth_looks,
                            "parallel_workers": parallel_workers,
                            "unwrap": unwrap,
                            "geocode": geocode,
                        },
                        "master_path": pair_meta.get("master_path"),
                        "slave_path": pair_meta.get("slave_path"),
                        "master_satellite": task_input_assets.get("master_satellite") or pair_meta.get("master_satellite"),
                        "slave_satellite": task_input_assets.get("slave_satellite") or pair_meta.get("slave_satellite"),
                        "master_imaging_date": pair_meta.get("master_imaging_date") or master_date,
                        "slave_imaging_date": pair_meta.get("slave_imaging_date") or slave_date,
                        "master_imaging_mode": pair_meta.get("master_imaging_mode"),
                        "slave_imaging_mode": pair_meta.get("slave_imaging_mode"),
                        "master_polarization": pair_meta.get("master_polarization"),
                        "slave_polarization": pair_meta.get("slave_polarization"),
                        "time_baseline_days": pair_meta.get("time_baseline_days") or time_baseline_days,
                        "spatial_baseline_meters": pair_meta.get("spatial_baseline_meters"),
                        "scene_pair_uid": pair_meta.get("scene_pair_uid") or pair_meta.get("pair_uid"),
                        "pair_uid": pair_meta.get("pair_uid") or pair_meta.get("scene_pair_uid"),
                        "network_run_id": pair_meta.get("network_run_id"),
                        "network_edge_id": pair_meta.get("network_edge_id"),
                        "policy_version": pair_meta.get("policy_version"),
                        "selection_strategy": pair_meta.get("selection_strategy"),
                        "input_assets": input_assets_summary,
                    },
                )
                output_dirs.append(output_dir)
            else:
                pairs_failed += 1

            emit_progress(
                "pair_finished",
                pair_index=pair_index,
                pair_total=total_tasks,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                success=success,
                returncode=rc,
                error=stderr.strip() if stderr else "",
            )
            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": run_key,
                    "task_dir": task_dir,
                    "work_dir": work_run_root,
                    "project_dir": project_dir,
                    "output_dir": output_dir,
                    "command": cmd,
                    "success": success,
                    "returncode": rc,
                    "stdout_tail": stdout[-3000:] if stdout else "",
                    "stderr_tail": stderr[-3000:] if stderr else "",
                    "error": stderr.strip() if stderr else "",
                    "wsl_task_dir": wsl_task_dir,
                    "wsl_project_dir": wsl_project_dir,
                    "wsl_output_dir": wsl_output_dir,
                    "wsl_template_root": wsl_template_root,
                    "master_date": master_date,
                    "slave_date": slave_date,
                    "archive_counts": {
                        "master": len(master_archives),
                        "slave": len(slave_archives),
                    },
                    "input_assets": input_assets_summary,
                    "wsl_input_assets_dir": wsl_input_assets_dir,
                }
            )

        invalid_candidates = validation.get("invalid_candidates", [])
        pairs_failed += len(invalid_candidates)
        overall_success = pairs_processed > 0 or (pairs_processed == 0 and pairs_failed == 0)
        failed_task_names = [
            item["task_name"]
            for item in task_results
            if not item.get("success")
        ] + [item["name"] for item in invalid_candidates]

        error = None
        if not overall_success:
            if failed_task_names:
                error = f"All PyINT tasks failed: {', '.join(failed_task_names[:10])}"
            else:
                error = "PyINT run failed."

        last_task_result = task_results[-1] if task_results else {}
        return RunResult(
            success=overall_success,
            engine_code=self.engine_code,
            profile=request.profile,
            job_id=request.job_id,
            pairs_processed=pairs_processed,
            pairs_failed=pairs_failed,
            output_dirs=output_dirs,
            error=error,
            detail={
                "mode": validation["mode"],
                "task_count": len(task_dirs),
                "selected_tasks": [item.get("task_alias") or item.get("task_name") for item in task_results],
                "invalid_candidates": invalid_candidates,
                "task_results": task_results,
                "run_key": run_key,
                "started_at": run_started_at_text,
                "force": force,
                "timeout_seconds": timeout,
                "range_looks": range_looks,
                "azimuth_looks": azimuth_looks,
                "parallel_workers": parallel_workers,
                "unwrap": unwrap,
                "geocode": geocode,
                "command": last_task_result.get("command", ""),
                "stdout_tail": last_task_result.get("stdout_tail", ""),
                "stderr_tail": last_task_result.get("stderr_tail", ""),
                "wsl_task_dir": last_task_result.get("wsl_task_dir", ""),
                "wsl_project_dir": last_task_result.get("wsl_project_dir", ""),
                "wsl_output_dir": last_task_result.get("wsl_output_dir", ""),
                "wsl_template_root": last_task_result.get("wsl_template_root", ""),
                "wsl_dem_root": wsl_dem_root,
                "wsl_dem": wsl_dem_root,
                "wsl_pyint_home": wsl_pyint_home,
                "wsl_orbit_pool": wsl_orbit_pool,
                "wsl_work_root": to_wsl_path(self._work_root) if self._work_root else "",
                "wsl_output_root": to_wsl_path(self._output_root) if self._output_root else "",
                "dem_mode": self._dem_mode,
                "prepared_dem_path": prepared_dem_path,
                "prepared_dem_kind": prepared_dem_kind,
                "wsl_prepared_dem_path": wsl_prepared_dem_path,
                "orbit_policy": self._orbit_policy,
                "lt1_precise_orbit_enabled": self._lt1_precise_orbit_enabled,
                "lt1_precise_orbit_mode": self._lt1_precise_orbit_mode,
                "lt1_precise_orbit_strict": self._lt1_precise_orbit_strict,
                "lt1_precise_orbit_validate_with_orb_filt": self._lt1_precise_orbit_validate_with_orb_filt,
                "lt1_precise_orbit_backup": self._lt1_precise_orbit_backup,
                "lt1_precise_orbit_orb_filt_degree": self._lt1_precise_orbit_orb_filt_degree,
                "record_input_assets": self._record_input_assets,
            },
        )

    @staticmethod
    def _discover_archives(task_dir: str) -> Dict[str, List[str]]:
        from ..services.pyint_service import discover_lt1_archives

        return discover_lt1_archives(task_dir)
