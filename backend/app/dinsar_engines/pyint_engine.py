"""PyINT D-InSAR engine backed by a WSL wrapper pipeline."""
from __future__ import annotations

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..config import get_env_text, read_bool_env, settings
from ..services.dinsar_completion_files import repair_managed_completion_files
from ..services.dinsar_naming import write_run_metadata
from ..services.isce2_result_validator import validate_isce2_result_files
from ..services.pyint_input_assets_service import (
    get_pyint_dem_summary,
    get_pyint_orbit_context,
    materialize_pyint_input_assets,
    resolve_pyint_task_input_assets,
)
from ..services.pyint_service import (
    DEFAULT_AZIMUTH_LOOKS,
    DEFAULT_DEM_RESOLUTION_M,
    DEFAULT_DERAMP_COH_THRESHOLD,
    DEFAULT_DERAMP_MODE,
    DEFAULT_ATMCOR_ENABLED,
    DEFAULT_ATMCOR_USE_FOR_DISP,
    DEFAULT_GEO_INTERP,
    DEFAULT_PARALLEL_WORKERS,
    DEFAULT_PRODUCT_COH_THRESHOLD,
    DEFAULT_RANGE_LOOKS,
    DEFAULT_REFLATTEN_AZIMUTH_STEP,
    DEFAULT_REFLATTEN_COH_THRESHOLD,
    DEFAULT_REFLATTEN_ENABLED,
    DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD,
    DEFAULT_REFLATTEN_MODEL,
    DEFAULT_REFLATTEN_RANGE_STEP,
    DEFAULT_REFERENCE_COH_THRESHOLD,
    DEFAULT_REFERENCE_MODE,
    DEFAULT_TARGET_GRID_SIZE_M,
    DEFAULT_UNWRAP_COH_THRESHOLD,
    MAX_LOOKS,
    MAX_PARALLEL_WORKERS,
    REFLATTEN_MODEL_CHOICES,
    TARGET_GRID_SIZE_MAX_M,
    TARGET_GRID_SIZE_MIN_M,
    build_profile_project_name,
    build_project_name,
    calculate_dem_oversampling,
    calculate_looks_from_task_dir,
    check_pyint_environment,
    infer_scene_date_from_archives,
    infer_task_identity,
    quote_shell,
    resolve_gamma_env_script,
    resolve_time_baseline_days,
    to_wsl_path,
    validate_pyint_root_dir,
)
from ..services.wsl_service import run_wsl_command_stream
from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult

RERUN_MODE_UNFINISHED_ONLY = "unfinished_only"
DEFAULT_COHERENCE_MASK_THRESHOLD = DEFAULT_PRODUCT_COH_THRESHOLD


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
    def _dem_resolution_m(self) -> float:
        return max(0.1, float(getattr(settings, "PYINT_DEM_RESOLUTION_M", DEFAULT_DEM_RESOLUTION_M) or DEFAULT_DEM_RESOLUTION_M))

    @property
    def _default_unwrap_coh_threshold(self) -> float:
        return float(getattr(settings, "PYINT_UNWRAP_COH_THRESHOLD", DEFAULT_UNWRAP_COH_THRESHOLD) or DEFAULT_UNWRAP_COH_THRESHOLD)

    @property
    def _default_product_coh_threshold(self) -> float:
        return float(getattr(settings, "PYINT_PRODUCT_COH_THRESHOLD", DEFAULT_PRODUCT_COH_THRESHOLD) or DEFAULT_PRODUCT_COH_THRESHOLD)

    @property
    def _default_reference_mode(self) -> str:
        return str(getattr(settings, "PYINT_REFERENCE_MODE", DEFAULT_REFERENCE_MODE) or DEFAULT_REFERENCE_MODE).strip().lower()

    @property
    def _default_reference_coh_threshold(self) -> float:
        return float(getattr(settings, "PYINT_REFERENCE_COH_THRESHOLD", DEFAULT_REFERENCE_COH_THRESHOLD) or DEFAULT_REFERENCE_COH_THRESHOLD)

    @property
    def _default_deramp_mode(self) -> str:
        return str(getattr(settings, "PYINT_DERAMP_MODE", DEFAULT_DERAMP_MODE) or DEFAULT_DERAMP_MODE).strip().lower()

    @property
    def _default_deramp_coh_threshold(self) -> float:
        return float(getattr(settings, "PYINT_DERAMP_COH_THRESHOLD", DEFAULT_DERAMP_COH_THRESHOLD) or DEFAULT_DERAMP_COH_THRESHOLD)

    @property
    def _gamma_nodata_value(self) -> float:
        return float(getattr(settings, "PYINT_GAMMA_NODATA_VALUE", -9999.0) if getattr(settings, "PYINT_GAMMA_NODATA_VALUE", None) is not None else -9999.0)

    @property
    def _geo_interp(self) -> str:
        value = str(getattr(settings, "PYINT_GEO_INTERP", DEFAULT_GEO_INTERP) or DEFAULT_GEO_INTERP).strip()
        return value if value in {"0", "1"} else DEFAULT_GEO_INTERP

    @property
    def _atmcor_enabled(self) -> bool:
        return bool(getattr(settings, "PYINT_ATMCOR_ENABLED", DEFAULT_ATMCOR_ENABLED))

    @property
    def _atmcor_use_for_disp(self) -> bool:
        return bool(getattr(settings, "PYINT_ATMCOR_USE_FOR_DISP", DEFAULT_ATMCOR_USE_FOR_DISP))

    @property
    def _reflatten_enabled(self) -> bool:
        return bool(getattr(settings, "PYINT_REFLATTEN_ENABLED", DEFAULT_REFLATTEN_ENABLED))

    @property
    def _reflatten_model(self) -> str:
        value = str(getattr(settings, "PYINT_REFLATTEN_MODEL", DEFAULT_REFLATTEN_MODEL) or DEFAULT_REFLATTEN_MODEL).strip().lower()
        if value == "linear":
            value = "plane"
        return value if value in REFLATTEN_MODEL_CHOICES else DEFAULT_REFLATTEN_MODEL

    @property
    def _reflatten_coh_threshold(self) -> float:
        return float(getattr(settings, "PYINT_REFLATTEN_COH_THRESHOLD", DEFAULT_REFLATTEN_COH_THRESHOLD) or DEFAULT_REFLATTEN_COH_THRESHOLD)

    @property
    def _reflatten_fallback_coh_threshold(self) -> float:
        return float(
            getattr(
                settings,
                "PYINT_REFLATTEN_FALLBACK_COH_THRESHOLD",
                DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD,
            )
            or DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD
        )

    @property
    def _reflatten_range_step(self) -> int:
        return max(1, int(getattr(settings, "PYINT_REFLATTEN_RANGE_STEP", DEFAULT_REFLATTEN_RANGE_STEP) or DEFAULT_REFLATTEN_RANGE_STEP))

    @property
    def _reflatten_azimuth_step(self) -> int:
        return max(1, int(getattr(settings, "PYINT_REFLATTEN_AZIMUTH_STEP", DEFAULT_REFLATTEN_AZIMUTH_STEP) or DEFAULT_REFLATTEN_AZIMUTH_STEP))

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

    def _pipeline_script_for_profile(self, profile: str) -> str:
        script_name = "run_s1_pyint_pipeline.py" if str(profile or "").strip() == "s1_gamma_dinsar" else "run_lt1_pyint_pipeline.py"
        local_script = Path(__file__).resolve().parent.parent / "pyint_pipeline" / script_name
        return _windows_path_to_wsl_mount(str(local_script))

    def get_profiles(self) -> List[EngineProfile]:
        shared_schema = {
                    "force": {
                        "label": "强制重跑",
                        "type": "boolean",
                        "default": False,
                        "section": "Execution",
                        "description": "删除当前 run_key 对应的工作区后重跑。",
                    },
                    "target_grid_size_m": {
                        "label": "目标网格尺寸（米）",
                        "type": "number",
                        "default": DEFAULT_TARGET_GRID_SIZE_M,
                        "step": 1,
                        "min": TARGET_GRID_SIZE_MIN_M,
                        "max": TARGET_GRID_SIZE_MAX_M,
                        "section": "Advanced",
                        "description": "可选。仅在未手动填写 looks 时用于估算多视数；不会重采样 DEM 或改写 Gamma 产品。",
                        "recommendation": "保持 0 使用显式或默认的 Gamma/PyINT looks。",
                    },
                    "range_looks": {
                        "label": "距离向多视（手动覆盖）",
                        "type": "number",
                        "default": DEFAULT_RANGE_LOOKS,
                        "step": 1,
                        "min": 1,
                        "max": MAX_LOOKS,
                        "section": "Execution",
                        "description": "PyINT/Gamma 模板中的 range_looks。",
                    },
                    "azimuth_looks": {
                        "label": "方位向多视（手动覆盖）",
                        "type": "number",
                        "default": DEFAULT_AZIMUTH_LOOKS,
                        "step": 1,
                        "min": 1,
                        "max": MAX_LOOKS,
                        "section": "Execution",
                        "description": "PyINT/Gamma 模板中的 azimuth_looks。",
                    },
                    "parallel_workers": {
                        "label": "并行数",
                        "type": "number",
                        "default": DEFAULT_PARALLEL_WORKERS,
                        "step": 1,
                        "min": 1,
                        "max": MAX_PARALLEL_WORKERS,
                        "section": "Execution",
                        "description": "同步控制 raw2slc/coreg/diff/unwrap/geocode 的并行数。",
                    },
                    "coherence_mask_threshold": {
                        "label": "Coherence quality",
                        "type": "number",
                        "default": self._default_product_coh_threshold,
                        "step": 0.05,
                        "min": 0.0,
                        "max": 1.0,
                        "section": "Delivery",
                        "description": "Only used for quality support statistics. It is not applied as a Python product mask.",
                        "recommendation": "Use 0.20 by default for LT-1 single-pair reporting; raise it for stricter review maps.",
                    },
                    "unwrap_coh_threshold": {
                        "label": "Unwrap coherence",
                        "type": "number",
                        "default": self._default_unwrap_coh_threshold,
                        "step": 0.05,
                        "min": 0.0,
                        "max": 1.0,
                        "section": "Advanced",
                        "description": "Minimum coherence used by Gamma rascc_mask/mcf during unwrapping.",
                        "recommendation": "Use 0.05 for ENVI-like permissive LT-1 unwrapping; raise it only when low-coherence bridges cause unwrap artifacts.",
                    },
                    "geo_interp": {
                        "label": "Geocode interpolation",
                        "type": "select",
                        "default": self._geo_interp,
                        "enum": ["0", "1"],
                        "section": "Advanced",
                        "description": "Gamma geocode_back interpolation mode: 0 nearest, 1 bicubic spline.",
                    },
                    "atmcor": {
                        "label": "Gamma atmcor",
                        "type": "boolean",
                        "default": self._atmcor_enabled,
                        "section": "Advanced",
                        "description": "Run PyINT/Gamma atm_correction stage using atm_mod_2d/atm_sim_2d/sub_phase.",
                    },
                    "atmcor_use_for_disp": {
                        "label": "Use atmcor for disp",
                        "type": "boolean",
                        "default": self._atmcor_use_for_disp,
                        "section": "Advanced",
                        "description": "Use the Gamma atmospheric-corrected unwrapped phase as dispmap input when available.",
                    },
                    "reflatten": {
                        "label": "Gamma residual reflatten",
                        "type": "boolean",
                        "default": self._reflatten_enabled,
                        "section": "Gamma Refinement",
                        "description": "After unwrapping, fit and remove residual long-wavelength phase ramps with Gamma rascc_mask/quad_fit/quad_sub.",
                        "recommendation": "Keep enabled for LT-1 D-InSAR unless validating the raw PyINT/Gamma baseline.",
                    },
                    "reflatten_model": {
                        "label": "Reflatten model",
                        "type": "select",
                        "default": self._reflatten_model,
                        "enum": ["plane", "quadratic"],
                        "section": "Gamma Refinement",
                        "description": "Gamma quad_fit model used for residual phase trend removal.",
                        "recommendation": "plane is safer for single-pair production; use quadratic only when a clear curved residual ramp remains.",
                    },
                    "reflatten_coh_threshold": {
                        "label": "Reflatten coherence",
                        "type": "number",
                        "default": self._reflatten_coh_threshold,
                        "step": 0.05,
                        "min": 0.0,
                        "max": 1.0,
                        "section": "Gamma Refinement",
                        "description": "Coherence threshold used to build the fit mask.",
                        "recommendation": "Keep the primary fit conservative at 0.70; the backend can retry with a looser fallback.",
                    },
                    "reflatten_fallback_coh_threshold": {
                        "label": "Reflatten fallback coherence",
                        "type": "number",
                        "default": self._reflatten_fallback_coh_threshold,
                        "step": 0.05,
                        "min": 0.0,
                        "max": 1.0,
                        "section": "Gamma Refinement",
                        "description": "Fallback coherence threshold if the primary reflatten fit does not have enough usable samples.",
                    },
                    "reflatten_range_step": {
                        "label": "Reflatten range step",
                        "type": "number",
                        "default": self._reflatten_range_step,
                        "step": 1,
                        "min": 1,
                        "section": "Gamma Refinement",
                        "description": "Sampling step in range pixels for Gamma quad_fit control points.",
                    },
                    "reflatten_azimuth_step": {
                        "label": "Reflatten azimuth step",
                        "type": "number",
                        "default": self._reflatten_azimuth_step,
                        "step": 1,
                        "min": 1,
                        "section": "Gamma Refinement",
                        "description": "Sampling step in azimuth lines for Gamma quad_fit control points.",
                    },
                    "unwrap": {
                        "label": "执行解缠",
                        "type": "boolean",
                        "default": True,
                        "section": "Execution",
                        "description": "关闭后仅做到差分干涉图，不做解缠。",
                    },
                    "geocode": {
                        "label": "执行地理编码",
                        "type": "boolean",
                        "default": True,
                        "section": "Execution",
                        "description": "关闭后不导出地理编码结果。",
                    },
                }
        return [
            EngineProfile(
                code="lt1_gamma_dinsar",
                label="LT-1 Gamma D-InSAR",
                description="Use PyINT + Gamma in WSL for single-pair LT-1 D-InSAR processing.",
                params_schema=shared_schema,
            ),
            EngineProfile(
                code="s1_gamma_dinsar",
                label="Sentinel-1 Gamma D-InSAR",
                description="Use PyINT + Gamma in WSL for single-pair Sentinel-1 D-InSAR processing.",
                params_schema=shared_schema,
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

        for key in ("force", "unwrap", "geocode", "atmcor", "atmcor_use_for_disp", "reflatten"):
            if key in normalized:
                normalized[key] = _coerce_bool(normalized[key])

        if "geo_interp" in normalized and normalized["geo_interp"] is not None:
            value = str(normalized["geo_interp"] or "").strip()
            if not value:
                normalized.pop("geo_interp", None)
            elif value not in {"0", "1"}:
                raise ValueError("geo_interp must be 0 or 1.")
            else:
                normalized["geo_interp"] = value

        if "target_grid_size_m" in normalized and str(normalized["target_grid_size_m"] or "").strip() == "":
            normalized.pop("target_grid_size_m", None)

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

        for key, maximum, label in (
            ("range_looks", MAX_LOOKS, "距离向多视"),
            ("azimuth_looks", MAX_LOOKS, "方位向多视"),
            ("parallel_workers", MAX_PARALLEL_WORKERS, "并行数"),
        ):
            if key not in normalized or normalized[key] is None:
                continue
            if str(normalized[key]).strip() == "":
                normalized.pop(key, None)
                continue
            try:
                parsed = int(normalized[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}必须为整数。") from exc
            if parsed < 1 or parsed > maximum:
                raise ValueError(f"{label}必须在 1 到 {maximum} 之间。")
            normalized[key] = parsed

        for mode_key, choices in (
            ("reference_mode", {"none", "coh_median"}),
            ("deramp_mode", {"none", "plane"}),
            ("reflatten_model", {"plane", "linear", "quadratic"}),
        ):
            if mode_key not in normalized or normalized[mode_key] is None:
                continue
            value = str(normalized[mode_key] or "").strip().lower()
            if not value:
                normalized.pop(mode_key, None)
                continue
            if mode_key == "reflatten_model" and value == "linear":
                value = "plane"
            if value not in choices:
                supported = ", ".join(sorted(choices))
                raise ValueError(f"{mode_key} must be one of: {supported}.")
            normalized[mode_key] = value

        for threshold_key in (
            "coherence_mask_threshold",
            "unwrap_coh_threshold",
            "reference_coh_threshold",
            "deramp_coh_threshold",
            "reflatten_coh_threshold",
            "reflatten_fallback_coh_threshold",
        ):
            if threshold_key not in normalized or normalized[threshold_key] is None:
                continue
            if str(normalized[threshold_key]).strip() == "":
                normalized.pop(threshold_key, None)
                continue
            try:
                parsed_threshold = float(normalized[threshold_key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{threshold_key} must be a number.") from exc
            if parsed_threshold < 0.0 or parsed_threshold > 1.0:
                raise ValueError(f"{threshold_key} must be between 0.0 and 1.0.")
            normalized[threshold_key] = parsed_threshold

        for step_key in ("reflatten_range_step", "reflatten_azimuth_step"):
            if step_key not in normalized or normalized[step_key] is None:
                continue
            if str(normalized[step_key]).strip() == "":
                normalized.pop(step_key, None)
                continue
            try:
                parsed_step = int(normalized[step_key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{step_key} must be an integer.") from exc
            if parsed_step < 1:
                raise ValueError(f"{step_key} must be greater than or equal to 1.")
            normalized[step_key] = parsed_step

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
            status = "unavailable" if critical_failed else "degraded"
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

        if request.profile not in {"lt1_gamma_dinsar", "s1_gamma_dinsar"}:
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
        managed_run_key = str(extra.get("__managed_run_key") or "").strip()
        run_key = managed_run_key or f"run_{run_started_at.strftime('%Y%m%dT%H%M%SZ')}_{self.engine_code}_{request.profile}"
        managed_run_dir_override = str(extra.get("__managed_run_dir") or "").strip()
        managed_native_output_dir_override = str(extra.get("__managed_native_output_dir") or "").strip()
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
        target_grid_size_m = int(extra.get("target_grid_size_m") or 0)
        manual_range_looks = extra.get("range_looks")
        manual_azimuth_looks = extra.get("azimuth_looks")
        parallel_workers = int(extra.get("parallel_workers", DEFAULT_PARALLEL_WORKERS))
        dem_resolution_m = self._dem_resolution_m
        dem_oversampling = calculate_dem_oversampling(
            dem_resolution_m=dem_resolution_m,
            target_grid_size_m=target_grid_size_m,
        )
        dem_lat_ovr = float(dem_oversampling["oversampling"])
        dem_lon_ovr = float(dem_oversampling["oversampling"])
        unwrap_coh_threshold = float(extra.get("unwrap_coh_threshold", self._default_unwrap_coh_threshold))
        coherence_mask_threshold = float(extra.get("coherence_mask_threshold", self._default_product_coh_threshold))
        reference_mode = "none"
        reference_coh_threshold = float(self._default_reference_coh_threshold)
        deramp_mode = "none"
        deramp_coh_threshold = float(self._default_deramp_coh_threshold)
        gamma_nodata_value = self._gamma_nodata_value
        geo_interp = str(extra.get("geo_interp", self._geo_interp) or self._geo_interp).strip()
        if geo_interp not in {"0", "1"}:
            geo_interp = DEFAULT_GEO_INTERP
        atmcor = bool(extra.get("atmcor", self._atmcor_enabled))
        atmcor_use_for_disp = bool(extra.get("atmcor_use_for_disp", self._atmcor_use_for_disp)) if atmcor else False
        reflatten = bool(extra.get("reflatten", self._reflatten_enabled))
        reflatten_model = str(extra.get("reflatten_model", self._reflatten_model) or self._reflatten_model).strip().lower()
        if reflatten_model == "linear":
            reflatten_model = "plane"
        if reflatten_model not in {"plane", "quadratic"}:
            reflatten_model = DEFAULT_REFLATTEN_MODEL
        reflatten_coh_threshold = float(extra.get("reflatten_coh_threshold", self._reflatten_coh_threshold))
        reflatten_fallback_coh_threshold = float(
            extra.get(
                "reflatten_fallback_coh_threshold",
                self._reflatten_fallback_coh_threshold,
            )
        )
        reflatten_range_step = int(extra.get("reflatten_range_step", self._reflatten_range_step))
        reflatten_azimuth_step = int(extra.get("reflatten_azimuth_step", self._reflatten_azimuth_step))
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

        def resolve_pair_looks(task_dir: str) -> Dict[str, Any]:
            manual_range = int(manual_range_looks) if manual_range_looks is not None else None
            manual_azimuth = int(manual_azimuth_looks) if manual_azimuth_looks is not None else None
            calculation: Dict[str, Any] = {}
            error_text = ""

            if target_grid_size_m > 0 and (manual_range is None or manual_azimuth is None):
                try:
                    calculation = calculate_looks_from_task_dir(
                        task_dir,
                        target_grid_size_m,
                    )
                except Exception as exc:
                    error_text = str(exc)
                    calculation = {
                        "mode": "fallback_default",
                        "target_resolution_m": target_grid_size_m,
                        "error": error_text,
                    }
            elif manual_range is None or manual_azimuth is None:
                calculation = {
                    "mode": "gamma_default_looks",
                    "target_resolution_m": None,
                }

            range_looks = manual_range
            if range_looks is None:
                range_looks = int(calculation.get("range_looks") or DEFAULT_RANGE_LOOKS)

            azimuth_looks = manual_azimuth
            if azimuth_looks is None:
                azimuth_looks = int(calculation.get("azimuth_looks") or DEFAULT_AZIMUTH_LOOKS)

            if manual_range is not None or manual_azimuth is not None:
                calculation = {
                    **calculation,
                    "mode": "manual_override" if calculation else "manual",
                    "manual_range_looks": manual_range,
                    "manual_azimuth_looks": manual_azimuth,
                }

            calculation["resolved_range_looks"] = int(range_looks)
            calculation["resolved_azimuth_looks"] = int(azimuth_looks)
            calculation["target_grid_size_m"] = int(target_grid_size_m)
            return {
                "range_looks": int(range_looks),
                "azimuth_looks": int(azimuth_looks),
                "calculation": calculation,
                "error": error_text,
            }

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
            run_dir = os.path.normpath(managed_run_dir_override) if managed_run_dir_override else os.path.normpath(
                os.path.join(self._output_root, pair_key, "runs", run_key)
            )
            output_dir = (
                os.path.normpath(managed_native_output_dir_override)
                if managed_native_output_dir_override
                else os.path.join(run_dir, "native")
            )
            template_root = os.path.normpath(os.path.join(self._template_root, pair_key, run_key))
            project_name = build_profile_project_name(
                task_identity.get("satellite_family"),
                pair_key,
                run_key,
            )
            project_dir = os.path.join(work_run_root, project_name)
            # Keep input assets outside the run root because the WSL pipeline may delete run_root on --force.
            input_assets_dir = os.path.join(self._work_root, pair_key, "input_assets", run_key)

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

            look_resolution = resolve_pair_looks(task_dir)
            range_looks = int(look_resolution["range_looks"])
            azimuth_looks = int(look_resolution["azimuth_looks"])
            look_calculation = dict(look_resolution.get("calculation") or {})
            look_message = (
                f"PyINT looks resolved for {task_alias}: "
                f"range={range_looks}, azimuth={azimuth_looks}, "
                f"target_grid={target_grid_size_m or 'not_set'}m, mode={look_calculation.get('mode', 'unknown')}"
            )
            if look_resolution.get("error"):
                look_message += f", fallback_reason={look_resolution['error']}"
            emit_progress(
                "log",
                pair_index=pair_index,
                pair_total=total_tasks,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                level="WARNING" if look_resolution.get("error") else "INFO",
                source="looks",
                message=look_message,
            )
            emit_progress(
                "log",
                pair_index=pair_index,
                pair_total=total_tasks,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                level="INFO",
                source="dem",
                message=(
                    f"PyINT DEM oversampling for {task_alias}: "
                    f"dem_resolution={dem_resolution_m:g}m, target_grid={target_grid_size_m or 'not_set'}m, "
                    f"dem_lat_ovr={dem_lat_ovr:g}, dem_lon_ovr={dem_lon_ovr:g}, "
                    f"actual_grid={float(dem_oversampling.get('actual_grid_size_m') or 0.0):g}m"
                ),
            )

            cmd_parts = [
                f"{quote_shell(self._python)} {quote_shell(self._pipeline_script_for_profile(request.profile))} {quote_shell(wsl_task_dir)}",
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
                f"--dem-resolution-m {dem_resolution_m}",
                f"--dem-lat-ovr {dem_lat_ovr}",
                f"--dem-lon-ovr {dem_lon_ovr}",
                f"--parallel-workers {parallel_workers}",
                f"--master-date {quote_shell(master_date)}" if master_date else "",
                f"--slave-date {quote_shell(slave_date)}" if slave_date else "",
                f"--time-baseline-days {time_baseline_days}",
                f"--target-grid-size-m {target_grid_size_m}",
                f"--unwrap-coh-threshold {unwrap_coh_threshold}",
                f"--coherence-mask-threshold {coherence_mask_threshold}",
                f"--geo-interp {quote_shell(geo_interp)}",
                f"--gamma-nodata-value {gamma_nodata_value}",
                "--reflatten" if reflatten else "--no-reflatten",
                f"--reflatten-model {quote_shell(reflatten_model)}",
                f"--reflatten-coh-threshold {reflatten_coh_threshold}",
                f"--reflatten-fallback-coh-threshold {reflatten_fallback_coh_threshold}",
                f"--reflatten-range-step {reflatten_range_step}",
                f"--reflatten-azimuth-step {reflatten_azimuth_step}",
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
                "--atmcor" if atmcor else "--no-atmcor",
                "--atmcor-use-for-disp" if atmcor_use_for_disp else "--no-atmcor-use-for-disp",
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
            def _emit_stream_log(level: str, source: str, text: str) -> None:
                line = str(text or "").strip()
                if not line:
                    return
                max_len = 2000
                if len(line) > max_len:
                    line = line[:max_len] + "...<truncated>"
                emit_progress(
                    "log",
                    pair_index=pair_index,
                    pair_total=total_tasks,
                    task_name=task_name,
                    task_alias=task_alias,
                    pair_key=pair_key,
                    level=level,
                    source=source,
                    message=line,
                )

            rc, stdout, stderr = run_wsl_command_stream(
                cmd,
                distro=self._distro,
                timeout=timeout,
                stdout_callback=lambda line: _emit_stream_log("INFO", "stdout", line),
                stderr_callback=lambda line: _emit_stream_log("WARNING", "stderr", line),
            )

            success = rc == 0
            error_text = stderr.strip() if stderr else ""
            validation_result: Dict[str, Any] = {}
            completion_files_result: Dict[str, Any] = {}
            primary_file = ""
            source_files: List[str] = []
            if success:
                try:
                    os.makedirs(output_dir, exist_ok=True)
                    os.makedirs(run_dir, exist_ok=True)
                    standard_disp_path = os.path.join(run_dir, "assets", "disp", "disp.tif")
                    standard_coh_path = os.path.join(run_dir, "assets", "coh", "coh.tif")
                    if geocode:
                        validation_sources = [standard_disp_path]
                        if os.path.isfile(standard_coh_path):
                            validation_sources.append(standard_coh_path)
                        validation_result = validate_isce2_result_files(
                            standard_disp_path,
                            validation_sources,
                        )
                        if not bool(validation_result.get("accepted")):
                            issues = validation_result.get("issues") or []
                            issue_text = "; ".join(str(item) for item in issues[:3]) or "unknown validation error"
                            raise RuntimeError(f"PyINT standard GeoTIFF validation failed: {issue_text}")
                        primary_file = str(validation_result.get("primary_file") or standard_disp_path)
                        source_files = list(validation_result.get("source_files") or validation_sources)

                    run_metadata = {
                        "run_key": run_key,
                        "pair_key": pair_key,
                        "task_name": task_name,
                        "task_alias": task_alias,
                        "engine_code": self.engine_code,
                        "profile_code": request.profile,
                        "source_root": os.path.normpath(request.root_dir),
                        "task_dir": os.path.normpath(task_dir),
                        "work_dir": work_run_root,
                        "output_dir": run_dir,
                        "native_output_dir": output_dir,
                        "project_dir": project_dir,
                        "runtime_id": settings.PYINT_RUNTIME_ID,
                        "started_at": run_started_at_text,
                        "finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                        "primary_file": primary_file,
                        "source_files": source_files,
                        "acceptance": validation_result,
                        "params": {
                            "force": force,
                            "target_grid_size_m": target_grid_size_m,
                            "dem_resolution_m": dem_resolution_m,
                            "dem_oversampling": dem_oversampling,
                            "dem_lat_ovr": dem_lat_ovr,
                            "dem_lon_ovr": dem_lon_ovr,
                            "range_looks": range_looks,
                            "azimuth_looks": azimuth_looks,
                            "manual_range_looks": manual_range_looks,
                            "manual_azimuth_looks": manual_azimuth_looks,
                            "look_calculation": look_calculation,
                            "parallel_workers": parallel_workers,
                            "unwrap_coh_threshold": unwrap_coh_threshold,
                            "coherence_quality_threshold": coherence_mask_threshold,
                            "reference_mode": reference_mode,
                            "reference_coh_threshold": reference_coh_threshold,
                            "deramp_mode": deramp_mode,
                            "deramp_coh_threshold": deramp_coh_threshold,
                            "gamma_nodata_value": gamma_nodata_value,
                            "geo_interp": geo_interp,
                            "atmcor": atmcor,
                            "atmcor_use_for_disp": atmcor_use_for_disp,
                            "reflatten": reflatten,
                            "reflatten_model": reflatten_model,
                            "reflatten_coh_threshold": reflatten_coh_threshold,
                            "reflatten_fallback_coh_threshold": reflatten_fallback_coh_threshold,
                            "reflatten_range_step": reflatten_range_step,
                            "reflatten_azimuth_step": reflatten_azimuth_step,
                            "gamma_native_export": {
                                "python_data_processing_applied": False,
                                "coherence_mask_applied": False,
                                "reference_applied": False,
                                "deramp_applied": False,
                            },
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
                        "scene_center_distance_meters": pair_meta.get("scene_center_distance_meters"),
                        "scene_pair_uid": pair_meta.get("scene_pair_uid") or pair_meta.get("pair_uid"),
                        "pair_uid": pair_meta.get("pair_uid") or pair_meta.get("scene_pair_uid"),
                        "network_run_id": pair_meta.get("network_run_id"),
                        "network_edge_id": pair_meta.get("network_edge_id"),
                        "policy_version": pair_meta.get("policy_version"),
                        "selection_strategy": pair_meta.get("selection_strategy"),
                        "input_assets": input_assets_summary,
                    }
                    write_run_metadata(run_dir, run_metadata)
                    write_run_metadata(output_dir, run_metadata)
                    if geocode and primary_file:
                        completion_files_result = repair_managed_completion_files(
                            run_dir,
                            primary_file=primary_file,
                            source_files=source_files,
                            run_meta=run_metadata,
                        )
                    output_dirs.append(run_dir)
                    pairs_processed += 1
                except Exception as exc:
                    success = False
                    error_text = str(exc)
                    stderr = (stderr.rstrip() + "\n" + error_text) if stderr else error_text

            if not success:
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
                    "run_dir": run_dir,
                    "output_dir": run_dir,
                    "native_output_dir": output_dir,
                    "primary_file": primary_file,
                    "source_files": source_files,
                    "acceptance": validation_result,
                    "completion_files": completion_files_result,
                    "target_grid_size_m": target_grid_size_m,
                    "dem_resolution_m": dem_resolution_m,
                    "dem_oversampling": dem_oversampling,
                    "dem_lat_ovr": dem_lat_ovr,
                    "dem_lon_ovr": dem_lon_ovr,
                    "range_looks": range_looks,
                    "azimuth_looks": azimuth_looks,
                    "manual_range_looks": manual_range_looks,
                    "manual_azimuth_looks": manual_azimuth_looks,
                    "look_calculation": look_calculation,
                    "unwrap_coh_threshold": unwrap_coh_threshold,
                    "coherence_quality_threshold": coherence_mask_threshold,
                    "reference_mode": reference_mode,
                    "reference_coh_threshold": reference_coh_threshold,
                    "deramp_mode": deramp_mode,
                    "deramp_coh_threshold": deramp_coh_threshold,
                    "gamma_nodata_value": gamma_nodata_value,
                    "geo_interp": geo_interp,
                    "atmcor": atmcor,
                    "atmcor_use_for_disp": atmcor_use_for_disp,
                    "gamma_native_export": {
                        "python_data_processing_applied": False,
                        "coherence_mask_applied": False,
                        "reference_applied": False,
                        "deramp_applied": False,
                    },
                    "command": cmd,
                    "success": success,
                    "returncode": rc,
                    "stdout_tail": stdout[-3000:] if stdout else "",
                    "stderr_tail": stderr[-3000:] if stderr else "",
                    "error": error_text,
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
                "target_grid_size_m": target_grid_size_m,
                "dem_resolution_m": dem_resolution_m,
                "dem_oversampling": dem_oversampling,
                "dem_lat_ovr": dem_lat_ovr,
                "dem_lon_ovr": dem_lon_ovr,
                "range_looks": last_task_result.get("range_looks"),
                "azimuth_looks": last_task_result.get("azimuth_looks"),
                "manual_range_looks": manual_range_looks,
                "manual_azimuth_looks": manual_azimuth_looks,
                "parallel_workers": parallel_workers,
                "unwrap_coh_threshold": unwrap_coh_threshold,
                "coherence_quality_threshold": coherence_mask_threshold,
                "reference_mode": reference_mode,
                "reference_coh_threshold": reference_coh_threshold,
                "deramp_mode": deramp_mode,
                "deramp_coh_threshold": deramp_coh_threshold,
                "gamma_nodata_value": gamma_nodata_value,
                "geo_interp": geo_interp,
                "atmcor": atmcor,
                "atmcor_use_for_disp": atmcor_use_for_disp,
                "gamma_native_export": {
                    "python_data_processing_applied": False,
                    "coherence_mask_applied": False,
                    "reference_applied": False,
                    "deramp_applied": False,
                },
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
        from ..services.pyint_service import discover_lt1_archives, discover_s1_scene_sources, infer_task_identity

        task_identity = infer_task_identity(task_dir)
        if str(task_identity.get("satellite_family") or "").strip().upper() == "S1":
            return discover_s1_scene_sources(task_dir)
        return discover_lt1_archives(task_dir)
