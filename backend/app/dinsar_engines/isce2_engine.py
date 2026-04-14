"""ISCE2 D-InSAR engine backed by a WSL pipeline script."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..config import get_env_text, read_bool_env
from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult
from ..services.dinsar_naming import (
    PAIR_META_FILENAME,
    build_fallback_pair_key,
    build_run_key,
    find_json_sidecar,
    write_run_metadata,
)


LT1_FIXED_WAVELENGTH = 0.23793052222222222
DEFAULT_TARGET_GRID_SIZE_M = 10
DEFAULT_BBOX_MARGIN = 0.05
DEFAULT_COH_THRESHOLD = 0.05
ORBIT_MARGIN_MIN_SEC = 60.0
ORBIT_MARGIN_MAX_SEC = 120.0
TARGET_GRID_SIZE_MIN_M = 5
TARGET_GRID_SIZE_MAX_M = 100


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


class Isce2Engine(DinsarEngine):
    @property
    def engine_code(self) -> str:
        return "isce2"

    @property
    def engine_label(self) -> str:
        return "ISCE2（WSL）"

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @property
    def _enabled(self) -> bool:
        return _read_bool_env("ISCE2_ENABLED", False)

    @property
    def _distro(self) -> str:
        return _read_env("ISCE2_WSL_DISTRO", "Ubuntu-24.04")

    @property
    def _python(self) -> str:
        return _read_env(
            "ISCE2_PYTHON",
            "/home/administrator/miniconda3/envs/isce2/bin/python",
        )

    @property
    def _dem_path(self) -> str:
        explicit = _read_env("ISCE2_DEM_PATH", "")
        if explicit:
            return explicit
        base = _read_env("IDL_DINSAR_DEM_BASE_FILE", "")
        return f"{base}.wgs84" if base else ""

    @property
    def _orbit_pool_isce2(self) -> str:
        return _read_env("ORBIT_POOL_ISCE2", "") or _read_env("ISCE2_ORBIT_DIR", "")

    @property
    def _work_root(self) -> str:
        return _read_env("ISCE2_WORK_ROOT", "")

    @property
    def _output_root(self) -> str:
        return _read_env("ISCE2_OUTPUT_ROOT", "")

    @property
    def _smoke_test(self) -> bool:
        return _read_bool_env("ISCE2_SMOKE_TEST_ENABLED", False)

    @property
    def _stripmap_app(self) -> str:
        return _read_env(
            "ISCE2_STRIPMAP_APP",
            "/home/administrator/miniconda3/envs/isce2/lib/python3.11"
            "/site-packages/isce/applications/stripmapApp.py",
        )

    @property
    def _pipeline_script(self) -> str:
        explicit = _read_env("ISCE2_PIPELINE_SCRIPT", "")
        if explicit:
            return explicit
        local_script = (
            Path(__file__).resolve().parent.parent
            / "isce2_pipeline"
            / "run_lt1_dinsar_pipeline.py"
        )
        return _windows_path_to_wsl_mount(str(local_script))

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def get_profiles(self) -> List[EngineProfile]:
        return [
            EngineProfile(
                code="lt1_stripmap",
                label="LT-1 条带模式",
                description="通过 WSL 环境运行 LT-1 条带模式 D-InSAR 处理流程。",
                params_schema={
                    "force": {
                        "label": "强制重建工作目录",
                        "type": "boolean",
                        "default": False,
                        "description": "如果工作目录已经存在，先删除旧目录再重新处理。",
                        "recommendation": "仅在确认旧结果可以丢弃时启用。",
                    },
                    "target_grid_size_m": {
                        "label": "目标网格尺寸（米）",
                        "type": "number",
                        "default": DEFAULT_TARGET_GRID_SIZE_M,
                        "step": 1,
                        "min": TARGET_GRID_SIZE_MIN_M,
                        "max": TARGET_GRID_SIZE_MAX_M,
                        "description": "控制多视尺度和地理编码输出粒度，系统会自动换算内部处理参数。",
                        "recommendation": "建议优先使用 10；希望保留更多细节可尝试 5，若更看重稳定性和效率可提高到 15 或 20。",
                    },
                    "bbox": {
                        "label": "地理编码范围",
                        "type": "string",
                        "default": "",
                        "placeholder": "南,北,西,东",
                        "description": "手工指定地理编码范围，格式为南、北、西、东。",
                        "recommendation": "通常留空即可，让系统自动估算；只有在你明确知道目标范围时再手工填写。",
                    },
                    "coh_threshold": {
                        "label": "相干性阈值",
                        "type": "number",
                        "default": DEFAULT_COH_THRESHOLD,
                        "step": 0.01,
                        "min": 0,
                        "max": 1,
                        "description": "导出位移结果时，低于该阈值的像元会被掩膜。",
                        "recommendation": "默认 0.05 便于先看全量结果；正式成果更建议从 0.10 起用。",
                    },
                    "bbox_margin": {
                        "label": "范围外扩量（度）",
                        "type": "number",
                        "default": DEFAULT_BBOX_MARGIN,
                        "step": 0.01,
                        "min": 0,
                        "description": "自动估算地理编码范围后，向四周额外扩展的角度。",
                        "recommendation": "推荐 0.05；如果边缘仍有裁切，可逐步提高到 0.08 或 0.10。",
                    },
                    "wavelength": {
                        "label": "雷达波长（米）",
                        "type": "number",
                        "default": LT1_FIXED_WAVELENGTH,
                        "step": 0.000001,
                        "readonly": True,
                        "include_in_payload": False,
                        "description": "LT-1 固定参数，用于位移量换算。",
                        "recommendation": "系统已锁定，不允许修改。",
                    },
                    "orbit_margin_sec": {
                        "label": "精轨裁剪时间余量（秒）",
                        "type": "number",
                        "default": ORBIT_MARGIN_MIN_SEC,
                        "step": 1,
                        "min": ORBIT_MARGIN_MIN_SEC,
                        "max": ORBIT_MARGIN_MAX_SEC,
                        "description": "生成精轨 XML 时，在场景开始和结束时刻前后额外保留的时间。",
                        "recommendation": "建议优先使用 60；如果元数据时间标签偏紧或边界异常，可提高到 90 或 120。",
                    },
                },
            ),
        ]

    def normalize_extra(self, extra: Dict[str, Any] | None) -> Dict[str, Any]:
        normalized: Dict[str, Any] = dict(extra or {})
        normalized.pop("wavelength", None)

        if "force" in normalized:
            normalized["force"] = bool(normalized["force"])

        if "bbox" in normalized and normalized["bbox"] is not None:
            normalized["bbox"] = str(normalized["bbox"]).strip()

        if "target_grid_size_m" in normalized and normalized["target_grid_size_m"] is not None:
            try:
                grid_size = float(normalized["target_grid_size_m"])
            except (TypeError, ValueError) as exc:
                raise ValueError("目标网格尺寸必须为数字。") from exc
            if int(grid_size) != grid_size:
                raise ValueError("目标网格尺寸必须使用整数米。")
            grid_size = int(grid_size)
            if grid_size < TARGET_GRID_SIZE_MIN_M or grid_size > TARGET_GRID_SIZE_MAX_M:
                raise ValueError(
                    f"目标网格尺寸必须在 {TARGET_GRID_SIZE_MIN_M} 到 {TARGET_GRID_SIZE_MAX_M} 米之间。"
                )
            normalized["target_grid_size_m"] = grid_size

        if "coh_threshold" in normalized and normalized["coh_threshold"] is not None:
            try:
                coh_threshold = float(normalized["coh_threshold"])
            except (TypeError, ValueError) as exc:
                raise ValueError("相干性阈值必须为数字。") from exc
            if coh_threshold < 0 or coh_threshold > 1:
                raise ValueError("相干性阈值必须在 0 到 1 之间。")
            normalized["coh_threshold"] = coh_threshold

        if "bbox_margin" in normalized and normalized["bbox_margin"] is not None:
            try:
                bbox_margin = float(normalized["bbox_margin"])
            except (TypeError, ValueError) as exc:
                raise ValueError("范围外扩量必须为数字。") from exc
            if bbox_margin < 0:
                raise ValueError("范围外扩量不能小于 0。")
            normalized["bbox_margin"] = bbox_margin

        if "orbit_margin_sec" in normalized and normalized["orbit_margin_sec"] is not None:
            try:
                orbit_margin = float(normalized["orbit_margin_sec"])
            except (TypeError, ValueError) as exc:
                raise ValueError("精轨裁剪时间余量必须为数字。") from exc
            if orbit_margin < ORBIT_MARGIN_MIN_SEC or orbit_margin > ORBIT_MARGIN_MAX_SEC:
                raise ValueError(
                    f"精轨裁剪时间余量必须在 {int(ORBIT_MARGIN_MIN_SEC)} 到 {int(ORBIT_MARGIN_MAX_SEC)} 秒之间。"
                )
            normalized["orbit_margin_sec"] = orbit_margin

        return normalized

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    def check_available(self) -> EngineAvailability:
        if not self._enabled:
            return EngineAvailability(
                engine_code=self.engine_code,
                status="unavailable",
                available=False,
                checks=[
                    {
                        "name": "ISCE2_ENABLED",
                        "ok": False,
                        "detail": "ISCE2_ENABLED=false",
                    }
                ],
                message="ISCE2 is disabled. Set ISCE2_ENABLED=true to enable it.",
            )

        from ..services.wsl_service import check_wsl_environment

        report = check_wsl_environment(
            distro=self._distro,
            python_cmd=self._python,
            stripmap_app_path=self._stripmap_app,
            pipeline_script_path=self._pipeline_script,
            dem_path_win=self._dem_path,
            orbit_dir_win=self._orbit_pool_isce2,
            output_dir_win=self._output_root,
            smoke_test=self._smoke_test,
        )

        checks_list = [
            {"name": check.name, "ok": check.ok, "detail": check.detail, "skipped": check.skipped}
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

    # ------------------------------------------------------------------
    # Task discovery
    # ------------------------------------------------------------------

    def validate_root_dir(self, root_dir: str, num_to_process: int = 0) -> Dict[str, Any]:
        normalized_root = os.path.normpath(os.path.abspath(str(root_dir or "").strip()))
        if not root_dir or not os.path.isdir(normalized_root):
            raise ValueError(f"ISCE2 root_dir does not exist or is not a directory: {root_dir}")

        if self._looks_like_task_dir(normalized_root):
            task_dirs = [normalized_root]
            invalid_candidates: List[Dict[str, Any]] = []
            mode = "single_task_dir"
        else:
            task_dirs = []
            invalid_candidates = []
            for entry in self._iter_child_dirs(normalized_root):
                if not entry.name.lower().startswith("task_"):
                    continue
                missing = self._missing_task_subdirs(entry.path)
                if missing:
                    invalid_candidates.append(
                        {"name": entry.name, "path": entry.path, "missing_subdirs": missing}
                    )
                    continue
                task_dirs.append(os.path.normpath(entry.path))
            mode = "task_root_dir"

        if not task_dirs:
            detail = ""
            if invalid_candidates:
                formatted = ", ".join(
                    f"{item['name']} missing {','.join(item['missing_subdirs'])}"
                    for item in invalid_candidates[:5]
                )
                detail = f" Invalid candidates: {formatted}."
            raise ValueError(
                "ISCE2 root_dir must be either a single task directory containing "
                "'master' and 'slave', or a parent directory containing valid Task_* subdirectories."
                f"{detail}"
            )

        selected_count = int(num_to_process or 0)
        if selected_count > 0:
            task_dirs = task_dirs[:selected_count]

        return {
            "root_dir": normalized_root,
            "mode": mode,
            "task_dirs": task_dirs,
            "task_count": len(task_dirs),
            "invalid_candidates": invalid_candidates,
        }

    @staticmethod
    def _iter_child_dirs(root_dir: str):
        with os.scandir(root_dir) as entries:
            child_dirs = [entry for entry in entries if entry.is_dir()]
        child_dirs.sort(key=lambda entry: entry.name.lower())
        return child_dirs

    @staticmethod
    def _missing_task_subdirs(task_dir: str) -> List[str]:
        missing: List[str] = []
        for subdir in ("master", "slave"):
            if not os.path.isdir(os.path.join(task_dir, subdir)):
                missing.append(subdir)
        return missing

    def _looks_like_task_dir(self, task_dir: str) -> bool:
        return not self._missing_task_subdirs(task_dir)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, request: RunRequest) -> RunResult:
        if not self._enabled:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error="ISCE2 is disabled.",
            )

        if request.profile != "lt1_stripmap":
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error=f"Unknown profile: {request.profile}",
            )

        return self._run_lt1_stripmap(request)

    def _run_lt1_stripmap(self, request: RunRequest) -> RunResult:
        from ..services.wsl_service import run_wsl_command, windows_path_to_wsl

        extra = self.normalize_extra(request.extra)
        validation = self.validate_root_dir(request.root_dir, request.num_to_process)
        task_dirs: List[str] = validation["task_dirs"]
        run_started_at = datetime.utcnow()
        run_started_at_text = run_started_at.isoformat(timespec="seconds") + "Z"
        run_key = build_run_key(self.engine_code, request.profile, started_at=run_started_at)

        timeout = request.timeout_seconds or 21600
        force = bool(extra.get("force"))
        target_grid_size_m = int(extra.get("target_grid_size_m", DEFAULT_TARGET_GRID_SIZE_M))
        bbox = extra.get("bbox", "")
        coh_threshold = extra.get("coh_threshold")
        bbox_margin = extra.get("bbox_margin")
        wavelength = LT1_FIXED_WAVELENGTH
        orbit_margin_sec = extra.get("orbit_margin_sec")

        wsl_isce2_pool = ""
        if self._orbit_pool_isce2:
            wsl_isce2_pool = windows_path_to_wsl(self._orbit_pool_isce2, distro=self._distro)

        wsl_dem = ""
        if self._dem_path:
            wsl_dem = windows_path_to_wsl(self._dem_path, distro=self._distro)

        task_results: List[Dict[str, Any]] = []
        output_dirs: List[str] = []
        pairs_processed = 0
        pairs_failed = 0

        for task_dir in task_dirs:
            task_name = os.path.basename(os.path.normpath(task_dir))
            pair_meta = find_json_sidecar(task_dir, PAIR_META_FILENAME, max_levels=0) or {}
            task_alias = str(pair_meta.get("task_alias") or task_name).strip() or task_name
            pair_key = str(pair_meta.get("pair_key") or "").strip() or build_fallback_pair_key(task_alias, task_dir)
            work_root = self._work_root or os.path.join(task_dir, "isce2_work")
            output_root = self._output_root or os.path.join(task_dir, "isce2_output")
            work_dir = os.path.normpath(os.path.join(work_root, pair_key, run_key))
            output_dir = os.path.normpath(os.path.join(output_root, pair_key, run_key, "native"))
            orbit_output_dir = os.path.join(work_dir, "orbits")
            wsl_task_dir = windows_path_to_wsl(task_dir, distro=self._distro)
            wsl_work_dir = windows_path_to_wsl(work_dir, distro=self._distro)
            wsl_output_dir = windows_path_to_wsl(output_dir, distro=self._distro)
            wsl_orbit_output_dir = windows_path_to_wsl(orbit_output_dir, distro=self._distro)
            if not wsl_task_dir:
                pairs_failed += 1
                task_results.append(
                    {
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "pair_key": pair_key,
                        "run_key": run_key,
                        "task_dir": task_dir,
                        "work_dir": work_dir,
                        "output_dir": output_dir,
                        "success": False,
                        "returncode": -2,
                        "error": f"Unable to convert task dir to WSL path: {task_dir}",
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "command": "",
                        "wsl_task_dir": "",
                        "wsl_work_dir": "",
                        "wsl_output_dir": "",
                    }
                )
                continue
            if not wsl_work_dir or not wsl_output_dir or not wsl_orbit_output_dir:
                pairs_failed += 1
                task_results.append(
                    {
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "pair_key": pair_key,
                        "run_key": run_key,
                        "task_dir": task_dir,
                        "work_dir": work_dir,
                        "output_dir": output_dir,
                        "success": False,
                        "returncode": -2,
                        "error": "Unable to convert ISCE2 work/output paths to WSL paths.",
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "command": "",
                        "wsl_task_dir": wsl_task_dir,
                        "wsl_work_dir": wsl_work_dir,
                        "wsl_output_dir": wsl_output_dir,
                    }
                )
                continue

            cmd_parts = [
                "export PROJ_DATA=/home/administrator/miniconda3/envs/isce2/share/proj",
                f"&& {self._python} '{self._pipeline_script}' '{wsl_task_dir}'",
                f"--task-name '{task_alias}'",
                f"--output-prefix '{task_alias}'",
                f"--work-dir '{wsl_work_dir}'",
                f"--output-dir '{wsl_output_dir}'",
                f"--orbit-output-dir '{wsl_orbit_output_dir}'",
            ]
            if wsl_isce2_pool:
                cmd_parts.append(f"--orbit-root '{wsl_isce2_pool}'")
            if wsl_dem:
                cmd_parts.append(f"--dem '{wsl_dem}'")
            if force:
                cmd_parts.append("--force")
            if target_grid_size_m:
                cmd_parts.append(f"--target-grid-size-m {target_grid_size_m}")
            if bbox:
                cmd_parts.append(f"--bbox '{bbox}'")
            if coh_threshold is not None:
                cmd_parts.append(f"--coh-threshold {coh_threshold}")
            if bbox_margin is not None:
                cmd_parts.append(f"--bbox-margin {bbox_margin}")
            if wavelength is not None:
                cmd_parts.append(f"--wavelength {wavelength}")
            if orbit_margin_sec is not None:
                cmd_parts.append(f"--orbit-margin-sec {orbit_margin_sec}")

            cmd = " ".join(cmd_parts)
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
                        "work_dir": work_dir,
                        "output_dir": output_dir,
                        "orbit_output_dir": orbit_output_dir,
                        "started_at": run_started_at_text,
                        "finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                        "params": {
                            "force": force,
                            "target_grid_size_m": target_grid_size_m,
                            "bbox": bbox,
                            "coh_threshold": coh_threshold,
                            "bbox_margin": bbox_margin,
                            "wavelength": wavelength,
                            "orbit_margin_sec": orbit_margin_sec,
                        },
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
                        "scene_pair_uid": pair_meta.get("scene_pair_uid") or pair_meta.get("pair_uid"),
                        "pair_uid": pair_meta.get("pair_uid") or pair_meta.get("scene_pair_uid"),
                        "network_run_id": pair_meta.get("network_run_id"),
                        "network_edge_id": pair_meta.get("network_edge_id"),
                        "policy_version": pair_meta.get("policy_version"),
                        "selection_strategy": pair_meta.get("selection_strategy"),
                    },
                )
                output_dirs.append(output_dir)
            else:
                pairs_failed += 1

            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "pair_key": pair_key,
                    "run_key": run_key,
                    "task_dir": task_dir,
                    "work_dir": work_dir,
                    "output_dir": output_dir,
                    "wsl_task_dir": wsl_task_dir,
                    "wsl_work_dir": wsl_work_dir,
                    "wsl_output_dir": wsl_output_dir,
                    "command": cmd,
                    "success": success,
                    "returncode": rc,
                    "stdout_tail": stdout[-3000:] if stdout else "",
                    "stderr_tail": stderr[-3000:] if stderr else "",
                    "error": stderr.strip() if stderr else "",
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
                error = f"All ISCE2 tasks failed: {', '.join(failed_task_names[:10])}"
            else:
                error = "ISCE2 run failed."

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
                "target_grid_size_m": target_grid_size_m,
                "wavelength": wavelength,
                "orbit_margin_sec": orbit_margin_sec,
                "command": last_task_result.get("command", ""),
                "stdout_tail": last_task_result.get("stdout_tail", ""),
                "stderr_tail": last_task_result.get("stderr_tail", ""),
                "wsl_task_dir": last_task_result.get("wsl_task_dir", ""),
                "wsl_work_dir": last_task_result.get("wsl_work_dir", ""),
                "wsl_output_dir": last_task_result.get("wsl_output_dir", ""),
                "wsl_orbit_pool": wsl_isce2_pool,
                "wsl_dem": wsl_dem,
                "wsl_work_root": windows_path_to_wsl(self._work_root, distro=self._distro) if self._work_root else "",
                "wsl_output_root": windows_path_to_wsl(self._output_root, distro=self._distro) if self._output_root else "",
            },
        )
