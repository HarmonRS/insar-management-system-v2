"""ISCE2 D-InSAR engine backed by a WSL pipeline script."""
from __future__ import annotations

import json
import os
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..config import get_env_text, read_bool_env, settings
from .base import DinsarEngine, EngineAvailability, EngineProfile, RunRequest, RunResult
from ..utils import normalize_satellite_family
from ..services.dinsar_naming import (
    PAIR_META_FILENAME,
    build_fallback_pair_key,
    build_run_key,
    find_json_sidecar,
    write_run_metadata,
)
from ..services.dinsar_result_layout_service import (
    normalize_isce2_run_layout,
)
from ..services.isce2_result_validator import validate_isce2_result_files


LT1_FIXED_WAVELENGTH = 0.23793052222222222
DEFAULT_TARGET_GRID_SIZE_M = 10
DEFAULT_BBOX_MARGIN = 0.05
DEFAULT_COH_THRESHOLD = 0.05
DEFAULT_REFERENCE_MODE = "coh_median"
DEFAULT_REFERENCE_COH_THRESHOLD = 0.30
DEFAULT_DERAMP_MODE = "plane"
DEFAULT_DERAMP_COH_THRESHOLD = 0.30
DEFAULT_DENSE_OFFSETS = True
DEFAULT_RUBBERSHEET_RANGE = True
DEFAULT_RUBBERSHEET_AZIMUTH = True
DEFAULT_IONOSPHERE_CORRECTION = True
DEFAULT_RUBBER_SHEET_SNR_THRESHOLD = 5.0
DEFAULT_RUBBER_SHEET_FILTER_SIZE = 9
DEFAULT_DENSE_WINDOW_WIDTH = 64
DEFAULT_DENSE_WINDOW_HEIGHT = 64
DEFAULT_DENSE_SEARCH_WIDTH = 20
DEFAULT_DENSE_SEARCH_HEIGHT = 20
DEFAULT_DENSE_SKIP_WIDTH = 32
DEFAULT_DENSE_SKIP_HEIGHT = 32
ORBIT_MARGIN_MIN_SEC = 60.0
ORBIT_MARGIN_MAX_SEC = 120.0
TARGET_GRID_SIZE_MIN_M = 5
TARGET_GRID_SIZE_MAX_M = 100
RERUN_MODE_UNFINISHED_ONLY = "unfinished_only"
RESUME_STAGE_CHOICES = {"", "unwrap", "geocode", "export"}
REFERENCE_MODE_CHOICES = {"none", "coh_median"}
DERAMP_MODE_CHOICES = {"none", "plane"}


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


def _join_argv_for_log(argv: List[str]) -> str:
    return " ".join(shlex.quote(str(item)) for item in argv if str(item))


def _normalize_rerun_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized == RERUN_MODE_UNFINISHED_ONLY else "rerun_all"


def _normalize_optional_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(text))


def _prefer_prepared_dem_variant(path: str) -> str:
    normalized = _normalize_optional_path(path)
    if not normalized or normalized.lower().endswith(".wgs84"):
        return normalized
    prepared = normalized + ".wgs84"
    if os.path.isfile(prepared) and os.path.isfile(prepared + ".xml"):
        return prepared
    return normalized


def _load_json_file(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _find_isce2_export_outputs(export_dir: str) -> Dict[str, str]:
    normalized_export_dir = _normalize_optional_path(export_dir)
    if not normalized_export_dir or not os.path.isdir(normalized_export_dir):
        return {}

    outputs: Dict[str, str] = {}
    with os.scandir(normalized_export_dir) as entries:
        files = sorted(
            [entry for entry in entries if entry.is_file()],
            key=lambda entry: entry.name.lower(),
        )
    for entry in files:
        lower_name = entry.name.lower()
        if lower_name.endswith(("_disp_full.tif", "_disp_full.tiff")):
            continue
        if "disp" not in outputs and (
            lower_name in {"disp.tif", "disp.tiff"}
            or lower_name.endswith(("_disp.tif", "_disp.tiff"))
        ):
            outputs["disp"] = entry.path
            continue
        if "coh" not in outputs and (
            lower_name in {"coh.tif", "coh.tiff"}
            or lower_name.endswith(("_coh.tif", "_coh.tiff"))
        ):
            outputs["coh"] = entry.path
    return outputs


class Isce2Engine(DinsarEngine):
    @property
    def engine_code(self) -> str:
        return "isce2"

    @property
    def engine_label(self) -> str:
        return "ISCE2（WSL）"

    @property
    def default_timeout_seconds(self) -> int:
        return max(60, int(settings.ISCE2_PER_TASK_TIMEOUT_SECONDS or 43200))

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
    def _runtime_id(self) -> str:
        return _read_env("ISCE2_RUNTIME_ID", settings.ISCE2_RUNTIME_ID or "isce2_runtime_v1")

    @property
    def _dem_path(self) -> str:
        explicit = _prefer_prepared_dem_variant(_read_env("ISCE2_DEM_PATH", ""))
        if explicit:
            return explicit
        base = _read_env("IDL_DINSAR_DEM_BASE_FILE", "")
        return _prefer_prepared_dem_variant(base) if base else ""

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

    def _legacy_get_profiles(self) -> List[EngineProfile]:
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

    def get_profiles(self) -> List[EngineProfile]:
        return [
            EngineProfile(
                code="lt1_stripmap",
                label="LT-1 Stripmap",
                description=(
                    "Managed LT-1 stripmap D-InSAR production in WSL with the standard "
                    "ISCE2 enhancement steps and split-spectrum ionosphere correction enabled by default."
                ),
                params_schema={
                    "force": {
                        "label": "Rebuild Work Dir",
                        "type": "boolean",
                        "default": False,
                        "section": "Execution",
                        "description": "Delete the existing work directory before rerunning the task.",
                        "recommendation": "Use only when the previous work directory can be discarded.",
                    },
                    "target_grid_size_m": {
                        "label": "Target Grid Size (m)",
                        "type": "number",
                        "default": DEFAULT_TARGET_GRID_SIZE_M,
                        "step": 1,
                        "min": TARGET_GRID_SIZE_MIN_M,
                        "max": TARGET_GRID_SIZE_MAX_M,
                        "section": "Execution",
                        "description": "Controls multilook scale and geocoded output spacing.",
                        "recommendation": "Use 10 by default; try 5 for more detail or 15-20 for more stability.",
                    },
                    "bbox": {
                        "label": "Geocode BBox",
                        "type": "string",
                        "default": "",
                        "placeholder": "south,north,west,east",
                        "section": "Execution",
                        "description": "Optional manual geocode bounding box.",
                        "recommendation": "Leave empty unless you need to constrain the output area.",
                    },
                    "coh_threshold": {
                        "label": "Coherence Threshold",
                        "type": "number",
                        "default": DEFAULT_COH_THRESHOLD,
                        "step": 0.01,
                        "min": 0,
                        "max": 1,
                        "section": "Delivery",
                        "description": "Masks displacement pixels below this coherence threshold in the exported product.",
                        "recommendation": "Use 0.05 for broad inspection and 0.10+ for stricter delivery.",
                    },
                    "reference_mode": {
                        "label": "Reference Mode",
                        "type": "string",
                        "default": DEFAULT_REFERENCE_MODE,
                        "enum": sorted(REFERENCE_MODE_CHOICES),
                        "section": "Delivery",
                        "description": "Normalizes the final displacement field before delivery.",
                        "recommendation": "Use coh_median for production so the result is centered on stable high-coherence pixels.",
                    },
                    "reference_coh_threshold": {
                        "label": "Reference Coh Threshold",
                        "type": "number",
                        "default": DEFAULT_REFERENCE_COH_THRESHOLD,
                        "step": 0.01,
                        "min": 0,
                        "max": 1,
                        "section": "Delivery",
                        "description": "Minimum coherence used when selecting pixels for displacement referencing.",
                        "recommendation": "Use 0.30 by default; raise it only when you have enough high-quality support pixels.",
                    },
                    "deramp_mode": {
                        "label": "Deramp Mode",
                        "type": "string",
                        "default": DEFAULT_DERAMP_MODE,
                        "enum": sorted(DERAMP_MODE_CHOICES),
                        "section": "Delivery",
                        "description": "Removes long-wavelength ramp residuals after referencing.",
                        "recommendation": "Use plane for LT-1 production unless you are explicitly debugging raw ISCE2 output.",
                    },
                    "deramp_coh_threshold": {
                        "label": "Deramp Coh Threshold",
                        "type": "number",
                        "default": DEFAULT_DERAMP_COH_THRESHOLD,
                        "step": 0.01,
                        "min": 0,
                        "max": 1,
                        "section": "Delivery",
                        "description": "Minimum coherence used when selecting pixels for deramp fitting.",
                        "recommendation": "Use 0.30 by default so the ramp is fitted on cleaner support pixels.",
                    },
                    "bbox_margin": {
                        "label": "BBox Margin (deg)",
                        "type": "number",
                        "default": DEFAULT_BBOX_MARGIN,
                        "step": 0.01,
                        "min": 0,
                        "section": "Execution",
                        "description": "Extra degree margin added to the auto-estimated geocode bounding box.",
                        "recommendation": "Use 0.05 by default; increase only if edges are clipped.",
                    },
                    "ionosphere_correction": {
                        "label": "Enable Split-Spectrum Ionosphere Correction",
                        "type": "boolean",
                        "default": DEFAULT_IONOSPHERE_CORRECTION,
                        "section": "Enhancement",
                        "description": "Runs the split-spectrum dispersive correction branch before geocode and export.",
                        "recommendation": "Keep enabled by default; disable it when the correction itself is suspected to degrade a scene.",
                    },
                    "dense_offsets": {
                        "label": "Enable Dense Offsets",
                        "type": "boolean",
                        "default": DEFAULT_DENSE_OFFSETS,
                        "section": "Enhancement",
                        "description": "Run ISCE2 dense offset estimation before fine resampling.",
                        "recommendation": "Keep enabled for LT-1 stripmap production.",
                    },
                    "rubbersheet_range": {
                        "label": "Enable Range Rubbersheeting",
                        "type": "boolean",
                        "default": DEFAULT_RUBBERSHEET_RANGE,
                        "section": "Enhancement",
                        "description": "Update range offsets with dense offsets before fine resampling.",
                        "recommendation": "Keep enabled for LT-1 stripmap production.",
                    },
                    "rubbersheet_azimuth": {
                        "label": "Enable Azimuth Rubbersheeting",
                        "type": "boolean",
                        "default": DEFAULT_RUBBERSHEET_AZIMUTH,
                        "section": "Enhancement",
                        "description": "Update azimuth offsets with dense offsets before fine resampling.",
                        "recommendation": "Keep enabled for LT-1 stripmap production.",
                    },
                    "rubber_sheet_snr_threshold": {
                        "label": "Rubbersheet SNR Threshold",
                        "type": "number",
                        "default": DEFAULT_RUBBER_SHEET_SNR_THRESHOLD,
                        "step": 0.5,
                        "min": 0,
                        "section": "Enhancement",
                        "description": "SNR threshold used when masking dense offsets for rubbersheeting.",
                        "recommendation": "Start with 5.0 unless a scene-specific diagnosis suggests otherwise.",
                    },
                    "rubber_sheet_filter_size": {
                        "label": "Rubbersheet Filter Size",
                        "type": "number",
                        "default": DEFAULT_RUBBER_SHEET_FILTER_SIZE,
                        "step": 1,
                        "min": 1,
                        "section": "Enhancement",
                        "description": "Median filter size used when smoothing masked dense offsets.",
                        "recommendation": "Start with 9.",
                    },
                    "dense_window_width": {
                        "label": "Dense Window Width",
                        "type": "number",
                        "default": DEFAULT_DENSE_WINDOW_WIDTH,
                        "step": 1,
                        "min": 1,
                        "section": "Enhancement",
                        "description": "Dense offset correlation window width.",
                        "recommendation": "Start with 64.",
                    },
                    "dense_window_height": {
                        "label": "Dense Window Height",
                        "type": "number",
                        "default": DEFAULT_DENSE_WINDOW_HEIGHT,
                        "step": 1,
                        "min": 1,
                        "section": "Enhancement",
                        "description": "Dense offset correlation window height.",
                        "recommendation": "Start with 64.",
                    },
                    "dense_search_width": {
                        "label": "Dense Search Width",
                        "type": "number",
                        "default": DEFAULT_DENSE_SEARCH_WIDTH,
                        "step": 1,
                        "min": 1,
                        "section": "Enhancement",
                        "description": "Dense offset search width.",
                        "recommendation": "Start with 20.",
                    },
                    "dense_search_height": {
                        "label": "Dense Search Height",
                        "type": "number",
                        "default": DEFAULT_DENSE_SEARCH_HEIGHT,
                        "step": 1,
                        "min": 1,
                        "section": "Enhancement",
                        "description": "Dense offset search height.",
                        "recommendation": "Start with 20.",
                    },
                    "dense_skip_width": {
                        "label": "Dense Skip Width",
                        "type": "number",
                        "default": DEFAULT_DENSE_SKIP_WIDTH,
                        "step": 1,
                        "min": 1,
                        "section": "Enhancement",
                        "description": "Dense offset sampling stride in range direction.",
                        "recommendation": "Start with 32.",
                    },
                    "dense_skip_height": {
                        "label": "Dense Skip Height",
                        "type": "number",
                        "default": DEFAULT_DENSE_SKIP_HEIGHT,
                        "step": 1,
                        "min": 1,
                        "section": "Enhancement",
                        "description": "Dense offset sampling stride in azimuth direction.",
                        "recommendation": "Start with 32.",
                    },
                    "wavelength": {
                        "label": "Radar Wavelength (m)",
                        "type": "number",
                        "default": LT1_FIXED_WAVELENGTH,
                        "step": 0.000001,
                        "readonly": True,
                        "include_in_payload": False,
                        "section": "Execution",
                        "description": "Fixed LT-1 radar wavelength used for displacement conversion.",
                        "recommendation": "This value is locked by the system.",
                    },
                    "orbit_margin_sec": {
                        "label": "Orbit Margin (sec)",
                        "type": "number",
                        "default": ORBIT_MARGIN_MIN_SEC,
                        "step": 1,
                        "min": ORBIT_MARGIN_MIN_SEC,
                        "max": ORBIT_MARGIN_MAX_SEC,
                        "section": "Execution",
                        "description": "Extra time margin preserved when clipping the precise orbit XML.",
                        "recommendation": "Use 60 by default; raise to 90-120 only if scene timing is tight.",
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

        if "reference_mode" in normalized and normalized["reference_mode"] is not None:
            reference_mode = str(normalized["reference_mode"]).strip().lower()
            if reference_mode not in REFERENCE_MODE_CHOICES:
                supported_modes = ", ".join(sorted(REFERENCE_MODE_CHOICES))
                raise ValueError(f"reference_mode must be one of: {supported_modes}")
            normalized["reference_mode"] = reference_mode

        if "reference_coh_threshold" in normalized and normalized["reference_coh_threshold"] is not None:
            try:
                reference_coh_threshold = float(normalized["reference_coh_threshold"])
            except (TypeError, ValueError) as exc:
                raise ValueError("reference_coh_threshold must be numeric") from exc
            if reference_coh_threshold < 0 or reference_coh_threshold > 1:
                raise ValueError("reference_coh_threshold must be between 0 and 1")
            normalized["reference_coh_threshold"] = reference_coh_threshold

        if "deramp_mode" in normalized and normalized["deramp_mode"] is not None:
            deramp_mode = str(normalized["deramp_mode"]).strip().lower()
            if deramp_mode not in DERAMP_MODE_CHOICES:
                supported_modes = ", ".join(sorted(DERAMP_MODE_CHOICES))
                raise ValueError(f"deramp_mode must be one of: {supported_modes}")
            normalized["deramp_mode"] = deramp_mode

        if "deramp_coh_threshold" in normalized and normalized["deramp_coh_threshold"] is not None:
            try:
                deramp_coh_threshold = float(normalized["deramp_coh_threshold"])
            except (TypeError, ValueError) as exc:
                raise ValueError("deramp_coh_threshold must be numeric") from exc
            if deramp_coh_threshold < 0 or deramp_coh_threshold > 1:
                raise ValueError("deramp_coh_threshold must be between 0 and 1")
            normalized["deramp_coh_threshold"] = deramp_coh_threshold

        if "bbox_margin" in normalized and normalized["bbox_margin"] is not None:
            try:
                bbox_margin = float(normalized["bbox_margin"])
            except (TypeError, ValueError) as exc:
                raise ValueError("范围外扩量必须为数字。") from exc
            if bbox_margin < 0:
                raise ValueError("范围外扩量不能小于 0。")
            normalized["bbox_margin"] = bbox_margin

        for bool_key in ("ionosphere_correction", "dense_offsets", "rubbersheet_range", "rubbersheet_azimuth"):
            if bool_key in normalized:
                normalized[bool_key] = bool(normalized[bool_key])

        if "rubber_sheet_snr_threshold" in normalized and normalized["rubber_sheet_snr_threshold"] is not None:
            try:
                snr_threshold = float(normalized["rubber_sheet_snr_threshold"])
            except (TypeError, ValueError) as exc:
                raise ValueError("rubber_sheet_snr_threshold must be numeric") from exc
            if snr_threshold < 0:
                raise ValueError("rubber_sheet_snr_threshold must be non-negative")
            normalized["rubber_sheet_snr_threshold"] = snr_threshold

        for int_key in (
            "rubber_sheet_filter_size",
            "dense_window_width",
            "dense_window_height",
            "dense_search_width",
            "dense_search_height",
            "dense_skip_width",
            "dense_skip_height",
        ):
            if int_key not in normalized or normalized[int_key] is None:
                continue
            try:
                numeric_value = float(normalized[int_key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{int_key} must be numeric") from exc
            if int(numeric_value) != numeric_value:
                raise ValueError(f"{int_key} must be an integer")
            if int(numeric_value) <= 0:
                raise ValueError(f"{int_key} must be greater than 0")
            normalized[int_key] = int(numeric_value)

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

        if "full_geocode" in normalized:
            normalized["full_geocode"] = bool(normalized["full_geocode"])

        if "resume_from" in normalized and normalized["resume_from"] is not None:
            resume_from = str(normalized["resume_from"]).strip().lower()
            if resume_from not in RESUME_STAGE_CHOICES:
                raise ValueError("resume_from must be one of: unwrap, geocode, export")
            normalized["resume_from"] = resume_from

        if bool(normalized.get("force")) and str(normalized.get("resume_from") or "").strip():
            raise ValueError("force cannot be used together with resume_from")

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

    def _has_completed_task_result(self, task_dir: str, profile_code: str) -> bool:
        task_name = os.path.basename(os.path.normpath(task_dir))
        pair_meta = find_json_sidecar(task_dir, PAIR_META_FILENAME, max_levels=0) or {}
        task_alias = str(pair_meta.get("task_alias") or task_name).strip() or task_name
        satellite_family = normalize_satellite_family(
            pair_meta.get("master_satellite") or pair_meta.get("slave_satellite")
        )
        pair_key = str(pair_meta.get("pair_key") or "").strip() or build_fallback_pair_key(
            task_alias,
            task_dir,
            satellite_family=satellite_family,
        )
        pointer_path = os.path.join(
            settings.DINSAR_PRODUCT_DIR,
            pair_key,
            "current",
            f"{self.engine_code}__{str(profile_code or '').strip()}.json",
        )
        if not os.path.isfile(pointer_path):
            return False

        payload = _load_json_file(pointer_path)
        if str(payload.get("status") or "").strip().upper() != "COMPLETED":
            return False

        primary_file = _normalize_optional_path(payload.get("primary_file"))
        source_files = payload.get("source_files") if isinstance(payload.get("source_files"), list) else []
        validation = validate_isce2_result_files(primary_file, source_files)
        if not bool(validation.get("accepted")):
            return False

        manifest_path = _normalize_optional_path(payload.get("manifest_path"))
        if manifest_path and os.path.isfile(manifest_path):
            return True

        output_dir = _normalize_optional_path(payload.get("output_dir"))
        if output_dir and os.path.isfile(os.path.join(output_dir, "execution_manifest.json")):
            return True
        return False

    def validate_root_dir(
        self,
        root_dir: str,
        num_to_process: int = 0,
        rerun_mode: str = "rerun_all",
    ) -> Dict[str, Any]:
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

        discovered_task_count = len(task_dirs)
        skipped_completed_count = 0
        selected_task_dirs = task_dirs
        if _normalize_rerun_mode(rerun_mode) == RERUN_MODE_UNFINISHED_ONLY:
            selected_task_dirs = []
            for task_dir in task_dirs:
                if self._has_completed_task_result(task_dir, "lt1_stripmap"):
                    skipped_completed_count += 1
                    continue
                selected_task_dirs.append(task_dir)

        selected_count = int(num_to_process or 0)
        if selected_count > 0:
            selected_task_dirs = selected_task_dirs[:selected_count]

        return {
            "root_dir": normalized_root,
            "mode": mode,
            "task_dirs": selected_task_dirs,
            "task_count": len(selected_task_dirs),
            "selected_task_count": len(selected_task_dirs),
            "discovered_task_count": discovered_task_count,
            "skipped_completed_count": skipped_completed_count,
            "invalid_candidates": invalid_candidates,
        }

    def _build_lt1_manifest_payload(
        self,
        *,
        request: RunRequest,
        task_dir: str,
        task_name: str,
        task_alias: str,
        pair_key: str,
        run_key: str,
        work_dir: str,
        output_dir: str,
        orbit_output_dir: str,
        wsl_task_dir: str,
        wsl_work_dir: str,
        wsl_output_dir: str,
        wsl_orbit_output_dir: str,
        wsl_orbit_root: str,
        wsl_dem: str,
        force: bool,
        target_grid_size_m: int,
        bbox: str,
        coh_threshold: Any,
        reference_mode: str,
        reference_coh_threshold: Any,
        deramp_mode: str,
        deramp_coh_threshold: Any,
        bbox_margin: Any,
        dense_offsets: bool,
        rubbersheet_range: bool,
        rubbersheet_azimuth: bool,
        ionosphere_correction: bool,
        rubber_sheet_snr_threshold: Any,
        rubber_sheet_filter_size: Any,
        dense_window_width: Any,
        dense_window_height: Any,
        dense_search_width: Any,
        dense_search_height: Any,
        dense_skip_width: Any,
        dense_skip_height: Any,
        wavelength: Any,
        orbit_margin_sec: Any,
        full_geocode: bool,
        resume_from: str,
        pair_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "job_id": request.job_id,
            "profile": request.profile,
            "task_name": task_name,
            "task_alias": task_alias,
            "pair_key": pair_key,
            "run_key": run_key,
            "paths": {
                "source_root_windows": os.path.normpath(
                    str((request.extra or {}).get("__source_root_override") or request.root_dir)
                ),
                "task_dir_windows": os.path.normpath(task_dir),
                "task_dir_wsl": wsl_task_dir,
                "work_dir_windows": work_dir,
                "work_dir_wsl": wsl_work_dir,
                "output_dir_windows": output_dir,
                "output_dir_wsl": wsl_output_dir,
                "orbit_output_dir_windows": orbit_output_dir,
                "orbit_output_dir_wsl": wsl_orbit_output_dir,
                "orbit_root_windows": self._orbit_pool_isce2,
                "orbit_root_wsl": wsl_orbit_root,
                "dem_path_windows": self._dem_path,
                "dem_path_wsl": wsl_dem,
            },
            "params": {
                "force": bool(force),
                "target_grid_size_m": int(target_grid_size_m),
                "bbox": str(bbox or "").strip(),
                "coh_threshold": coh_threshold,
                "reference_mode": str(reference_mode or "").strip(),
                "reference_coh_threshold": reference_coh_threshold,
                "deramp_mode": str(deramp_mode or "").strip(),
                "deramp_coh_threshold": deramp_coh_threshold,
                "bbox_margin": bbox_margin,
                "dense_offsets": bool(dense_offsets),
                "rubbersheet_range": bool(rubbersheet_range),
                "rubbersheet_azimuth": bool(rubbersheet_azimuth),
                "ionosphere_correction": bool(ionosphere_correction),
                "rubber_sheet_snr_threshold": rubber_sheet_snr_threshold,
                "rubber_sheet_filter_size": rubber_sheet_filter_size,
                "dense_window_width": dense_window_width,
                "dense_window_height": dense_window_height,
                "dense_search_width": dense_search_width,
                "dense_search_height": dense_search_height,
                "dense_skip_width": dense_skip_width,
                "dense_skip_height": dense_skip_height,
                "wavelength": wavelength,
                "orbit_margin_sec": orbit_margin_sec,
                "full_geocode": bool(full_geocode),
                "resume_from": str(resume_from or "").strip(),
                "split_spectrum": bool(ionosphere_correction),
            },
            "pair_meta": dict(pair_meta or {}),
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
        from ..services.wsl_broker import wsl_broker
        from ..services.wsl_runtime_registry import get_wsl_runtime
        from ..services.wsl_service import windows_path_to_wsl

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
        run_key = str(extra.get("__managed_run_key") or "").strip() or build_run_key(
            self.engine_code,
            request.profile,
            started_at=run_started_at,
        )
        progress_callback = request.progress_callback
        runtime = get_wsl_runtime(self._runtime_id)
        managed_run_dir_override = _normalize_optional_path(extra.get("__managed_run_dir"))
        managed_native_output_dir_override = _normalize_optional_path(extra.get("__managed_native_output_dir"))
        managed_work_dir_override = _normalize_optional_path(extra.get("__managed_work_dir"))
        managed_export_dir_override = _normalize_optional_path(extra.get("__managed_export_dir"))
        managed_orbit_output_dir_override = _normalize_optional_path(extra.get("__managed_orbit_output_dir"))
        source_root_override = _normalize_optional_path(extra.get("__source_root_override")) or os.path.normpath(request.root_dir)
        has_managed_override = any(
            [
                managed_run_dir_override,
                managed_native_output_dir_override,
                managed_work_dir_override,
                managed_export_dir_override,
                managed_orbit_output_dir_override,
            ]
        )
        if has_managed_override and total_tasks > 1:
            return RunResult(
                success=False,
                engine_code=self.engine_code,
                profile=request.profile,
                job_id=request.job_id,
                error="Managed ISCE2 directory overrides require a single task request.",
            )

        def emit_progress(event_type: str, **payload: Any) -> None:
            if not callable(progress_callback):
                return
            try:
                progress_callback({"event": event_type, **payload})
            except Exception:
                return

        timeout = max(60, int(request.timeout_seconds or self.default_timeout_seconds))
        force = bool(extra.get("force"))
        target_grid_size_m = int(extra.get("target_grid_size_m", DEFAULT_TARGET_GRID_SIZE_M))
        bbox = extra.get("bbox", "")
        coh_threshold = extra.get("coh_threshold", DEFAULT_COH_THRESHOLD)
        reference_mode = str(
            extra.get("reference_mode", DEFAULT_REFERENCE_MODE) or DEFAULT_REFERENCE_MODE
        ).strip().lower()
        reference_coh_threshold = extra.get(
            "reference_coh_threshold",
            DEFAULT_REFERENCE_COH_THRESHOLD,
        )
        deramp_mode = str(
            extra.get("deramp_mode", DEFAULT_DERAMP_MODE) or DEFAULT_DERAMP_MODE
        ).strip().lower()
        deramp_coh_threshold = extra.get(
            "deramp_coh_threshold",
            DEFAULT_DERAMP_COH_THRESHOLD,
        )
        bbox_margin = extra.get("bbox_margin", DEFAULT_BBOX_MARGIN)
        ionosphere_correction = bool(
            extra.get("ionosphere_correction", DEFAULT_IONOSPHERE_CORRECTION)
        )
        dense_offsets = bool(extra.get("dense_offsets", DEFAULT_DENSE_OFFSETS))
        rubbersheet_range = bool(extra.get("rubbersheet_range", DEFAULT_RUBBERSHEET_RANGE))
        rubbersheet_azimuth = bool(extra.get("rubbersheet_azimuth", DEFAULT_RUBBERSHEET_AZIMUTH))
        rubber_sheet_snr_threshold = extra.get(
            "rubber_sheet_snr_threshold",
            DEFAULT_RUBBER_SHEET_SNR_THRESHOLD,
        )
        rubber_sheet_filter_size = extra.get(
            "rubber_sheet_filter_size",
            DEFAULT_RUBBER_SHEET_FILTER_SIZE,
        )
        dense_window_width = extra.get("dense_window_width", DEFAULT_DENSE_WINDOW_WIDTH)
        dense_window_height = extra.get("dense_window_height", DEFAULT_DENSE_WINDOW_HEIGHT)
        dense_search_width = extra.get("dense_search_width", DEFAULT_DENSE_SEARCH_WIDTH)
        dense_search_height = extra.get("dense_search_height", DEFAULT_DENSE_SEARCH_HEIGHT)
        dense_skip_width = extra.get("dense_skip_width", DEFAULT_DENSE_SKIP_WIDTH)
        dense_skip_height = extra.get("dense_skip_height", DEFAULT_DENSE_SKIP_HEIGHT)
        wavelength = LT1_FIXED_WAVELENGTH
        orbit_margin_sec = extra.get("orbit_margin_sec", ORBIT_MARGIN_MIN_SEC)
        full_geocode = bool(extra.get("full_geocode"))
        resume_from = str(extra.get("resume_from") or "").strip().lower()

        wsl_isce2_pool = ""
        if self._orbit_pool_isce2:
            wsl_isce2_pool = windows_path_to_wsl(self._orbit_pool_isce2, distro=runtime.distro)

        wsl_dem = ""
        if self._dem_path:
            wsl_dem = windows_path_to_wsl(self._dem_path, distro=runtime.distro)

        task_results: List[Dict[str, Any]] = []
        output_dirs: List[str] = []
        pairs_processed = 0
        pairs_failed = 0

        for pair_index, task_dir in enumerate(task_dirs, start=1):
            task_name = os.path.basename(os.path.normpath(task_dir))
            pair_meta = find_json_sidecar(task_dir, PAIR_META_FILENAME, max_levels=0) or {}
            task_alias = str(pair_meta.get("task_alias") or task_name).strip() or task_name
            satellite_family = normalize_satellite_family(
                pair_meta.get("master_satellite") or pair_meta.get("slave_satellite")
            )
            pair_key = str(pair_meta.get("pair_key") or "").strip() or build_fallback_pair_key(
                task_alias,
                task_dir,
                satellite_family=satellite_family,
            )
            output_root = self._output_root or os.path.join(task_dir, "isce2_output")
            run_dir = managed_run_dir_override or os.path.normpath(
                os.path.join(output_root, pair_key, "runs", run_key)
            )
            native_output_dir = managed_native_output_dir_override or os.path.join(run_dir, "native")
            work_dir = managed_work_dir_override or os.path.join(native_output_dir, "workflow")
            export_dir = managed_export_dir_override or os.path.join(native_output_dir, "export")
            orbit_output_dir = managed_orbit_output_dir_override or os.path.join(work_dir, "orbits")
            wsl_task_dir = windows_path_to_wsl(task_dir, distro=runtime.distro)
            wsl_work_dir = windows_path_to_wsl(work_dir, distro=runtime.distro)
            wsl_output_dir = windows_path_to_wsl(export_dir, distro=runtime.distro)
            wsl_orbit_output_dir = windows_path_to_wsl(orbit_output_dir, distro=runtime.distro)
            emit_progress(
                "pair_started",
                pair_index=pair_index,
                pair_total=total_tasks,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                task_dir=task_dir,
                work_dir=work_dir,
                output_dir=run_dir,
            )
            if not wsl_task_dir:
                pairs_failed += 1
                error_text = f"Unable to convert task dir to WSL path: {task_dir}"
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
                        "run_dir": run_dir,
                        "native_output_dir": native_output_dir,
                        "work_dir": work_dir,
                        "output_dir": run_dir,
                        "export_dir": export_dir,
                        "success": False,
                        "returncode": -2,
                        "error": error_text,
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
                error_text = "Unable to convert ISCE2 work/output paths to WSL paths."
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
                        "run_dir": run_dir,
                        "native_output_dir": native_output_dir,
                        "work_dir": work_dir,
                        "output_dir": run_dir,
                        "export_dir": export_dir,
                        "success": False,
                        "returncode": -2,
                        "error": error_text,
                        "stdout_tail": "",
                        "stderr_tail": "",
                        "command": "",
                        "wsl_task_dir": wsl_task_dir,
                        "wsl_work_dir": wsl_work_dir,
                        "wsl_output_dir": wsl_output_dir,
                    }
                )
                continue

            manifest_payload = self._build_lt1_manifest_payload(
                request=request,
                task_dir=task_dir,
                task_name=task_name,
                task_alias=task_alias,
                pair_key=pair_key,
                run_key=run_key,
                work_dir=work_dir,
                output_dir=export_dir,
                orbit_output_dir=orbit_output_dir,
                wsl_task_dir=wsl_task_dir,
                wsl_work_dir=wsl_work_dir,
                wsl_output_dir=wsl_output_dir,
                wsl_orbit_output_dir=wsl_orbit_output_dir,
                wsl_orbit_root=wsl_isce2_pool,
                wsl_dem=wsl_dem,
                force=force,
                target_grid_size_m=target_grid_size_m,
                bbox=bbox,
                coh_threshold=coh_threshold,
                reference_mode=reference_mode,
                reference_coh_threshold=reference_coh_threshold,
                deramp_mode=deramp_mode,
                deramp_coh_threshold=deramp_coh_threshold,
                bbox_margin=bbox_margin,
                dense_offsets=dense_offsets,
                rubbersheet_range=rubbersheet_range,
                rubbersheet_azimuth=rubbersheet_azimuth,
                ionosphere_correction=ionosphere_correction,
                rubber_sheet_snr_threshold=rubber_sheet_snr_threshold,
                rubber_sheet_filter_size=rubber_sheet_filter_size,
                dense_window_width=dense_window_width,
                dense_window_height=dense_window_height,
                dense_search_width=dense_search_width,
                dense_search_height=dense_search_height,
                dense_skip_width=dense_skip_width,
                dense_skip_height=dense_skip_height,
                wavelength=wavelength,
                orbit_margin_sec=orbit_margin_sec,
                full_geocode=full_geocode,
                resume_from=resume_from,
                pair_meta=pair_meta,
            )
            broker_result = wsl_broker.run_manifest(
                runtime_id=runtime.runtime_id,
                operation="lt1_stripmap",
                payload=manifest_payload,
                job_id=f"{request.job_id}_{pair_key}",
                timeout_seconds=timeout,
            )
            rc = broker_result.returncode
            stdout = broker_result.stdout
            stderr = broker_result.stderr
            command = _join_argv_for_log(list(broker_result.argv))

            success = False
            layout_result: Dict[str, Any] = {}
            validation_result: Dict[str, Any] = {}
            error_text = stderr.strip() if stderr else ""
            if rc == 0:
                try:
                    os.makedirs(run_dir, exist_ok=True)
                    export_outputs = _find_isce2_export_outputs(export_dir)
                    disp_path = export_outputs.get("disp", "")
                    coh_path = export_outputs.get("coh", "")
                    if not disp_path:
                        raise FileNotFoundError(
                            f"No ISCE2 displacement GeoTIFF found under export dir: {export_dir}"
                        )

                    source_files = [disp_path]
                    if coh_path:
                        source_files.append(coh_path)
                    validation_result = validate_isce2_result_files(disp_path, source_files)
                    if not bool(validation_result.get("accepted")):
                        issues = validation_result.get("issues") or []
                        issue_text = "; ".join(str(item) for item in issues[:3]) or "unknown validation error"
                        raise RuntimeError(f"ISCE2 output validation failed: {issue_text}")

                    layout_result = normalize_isce2_run_layout(
                        run_dir,
                        primary_file=str(validation_result.get("primary_file") or disp_path),
                        source_files=list(validation_result.get("source_files") or source_files),
                        rewrite_metadata=False,
                    )
                    write_run_metadata(
                        run_dir,
                        {
                            "run_key": run_key,
                            "pair_key": pair_key,
                            "task_name": task_name,
                            "task_alias": task_alias,
                            "engine_code": self.engine_code,
                            "profile_code": request.profile,
                            "source_root": source_root_override,
                            "task_dir": os.path.normpath(task_dir),
                            "work_dir": work_dir,
                            "export_dir": export_dir,
                            "output_dir": run_dir,
                            "native_output_dir": layout_result["native_output_dir"],
                            "orbit_output_dir": orbit_output_dir,
                            "runtime_id": runtime.runtime_id,
                            "manifest_path_windows": broker_result.manifest.manifest_path_windows,
                            "manifest_path_wsl": broker_result.manifest.manifest_path_wsl,
                            "started_at": run_started_at_text,
                            "finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                            "primary_file": layout_result["primary_file"],
                            "source_files": layout_result["source_files"],
                            "acceptance": validation_result,
                            "params": {
                                "force": force,
                                "target_grid_size_m": target_grid_size_m,
                                "bbox": bbox,
                                "coh_threshold": coh_threshold,
                                "reference_mode": reference_mode,
                                "reference_coh_threshold": reference_coh_threshold,
                                "deramp_mode": deramp_mode,
                                "deramp_coh_threshold": deramp_coh_threshold,
                                "bbox_margin": bbox_margin,
                                "ionosphere_correction": ionosphere_correction,
                                "dense_offsets": dense_offsets,
                                "rubbersheet_range": rubbersheet_range,
                                "rubbersheet_azimuth": rubbersheet_azimuth,
                                "rubber_sheet_snr_threshold": rubber_sheet_snr_threshold,
                                "rubber_sheet_filter_size": rubber_sheet_filter_size,
                                "dense_window_width": dense_window_width,
                                "dense_window_height": dense_window_height,
                                "dense_search_width": dense_search_width,
                                "dense_search_height": dense_search_height,
                                "dense_skip_width": dense_skip_width,
                                "dense_skip_height": dense_skip_height,
                                "wavelength": wavelength,
                                "orbit_margin_sec": orbit_margin_sec,
                                "split_spectrum": ionosphere_correction,
                                "ionosphere_correction": ionosphere_correction,
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
                            "scene_center_distance_meters": pair_meta.get("scene_center_distance_meters"),
                            "scene_pair_uid": pair_meta.get("scene_pair_uid") or pair_meta.get("pair_uid"),
                            "pair_uid": pair_meta.get("pair_uid") or pair_meta.get("scene_pair_uid"),
                            "network_run_id": pair_meta.get("network_run_id"),
                            "network_edge_id": pair_meta.get("network_edge_id"),
                            "policy_version": pair_meta.get("policy_version"),
                            "selection_strategy": pair_meta.get("selection_strategy"),
                        },
                    )
                    output_dirs.append(run_dir)
                    pairs_processed += 1
                    success = True
                except Exception as exc:
                    error_text = str(exc)
                    if stderr:
                        stderr = stderr.rstrip() + "\n" + error_text
                    else:
                        stderr = error_text

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
                    "run_dir": run_dir,
                    "native_output_dir": (
                        layout_result.get("native_output_dir")
                        or native_output_dir
                    ),
                    "work_dir": work_dir,
                    "output_dir": run_dir,
                    "export_dir": export_dir,
                    "primary_file": layout_result.get("primary_file", ""),
                    "source_files": layout_result.get("source_files", []),
                    "validation": validation_result,
                    "wsl_task_dir": wsl_task_dir,
                    "wsl_work_dir": wsl_work_dir,
                    "wsl_output_dir": wsl_output_dir,
                    "runtime_id": runtime.runtime_id,
                    "command": command,
                    "runner_argv": list(broker_result.argv),
                    "manifest_path_windows": broker_result.manifest.manifest_path_windows,
                    "manifest_path_wsl": broker_result.manifest.manifest_path_wsl,
                    "success": success,
                    "returncode": rc,
                    "stdout_tail": stdout[-3000:] if stdout else "",
                    "stderr_tail": stderr[-3000:] if stderr else "",
                    "error": error_text,
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
                "coh_threshold": coh_threshold,
                "reference_mode": reference_mode,
                "reference_coh_threshold": reference_coh_threshold,
                "deramp_mode": deramp_mode,
                "deramp_coh_threshold": deramp_coh_threshold,
                "bbox_margin": bbox_margin,
                "ionosphere_correction": ionosphere_correction,
                "dense_offsets": dense_offsets,
                "rubbersheet_range": rubbersheet_range,
                "rubbersheet_azimuth": rubbersheet_azimuth,
                "rubber_sheet_snr_threshold": rubber_sheet_snr_threshold,
                "rubber_sheet_filter_size": rubber_sheet_filter_size,
                "dense_window_width": dense_window_width,
                "dense_window_height": dense_window_height,
                "dense_search_width": dense_search_width,
                "dense_search_height": dense_search_height,
                "dense_skip_width": dense_skip_width,
                "dense_skip_height": dense_skip_height,
                "wavelength": wavelength,
                "orbit_margin_sec": orbit_margin_sec,
                "split_spectrum": ionosphere_correction,
                "ionosphere_correction": ionosphere_correction,
                "runtime_id": runtime.runtime_id,
                "command": last_task_result.get("command", ""),
                "runner_argv": last_task_result.get("runner_argv", []),
                "manifest_path_windows": last_task_result.get("manifest_path_windows", ""),
                "manifest_path_wsl": last_task_result.get("manifest_path_wsl", ""),
                "stdout_tail": last_task_result.get("stdout_tail", ""),
                "stderr_tail": last_task_result.get("stderr_tail", ""),
                "wsl_task_dir": last_task_result.get("wsl_task_dir", ""),
                "wsl_work_dir": last_task_result.get("wsl_work_dir", ""),
                "wsl_output_dir": last_task_result.get("wsl_output_dir", ""),
                "wsl_orbit_pool": wsl_isce2_pool,
                "wsl_dem": wsl_dem,
                "wsl_work_root": (
                    os.path.dirname(str(last_task_result.get("wsl_work_dir") or "").strip())
                    if str(last_task_result.get("wsl_work_dir") or "").strip()
                    else (windows_path_to_wsl(self._work_root, distro=runtime.distro) if self._work_root else "")
                ),
                "wsl_output_root": (
                    os.path.dirname(str(last_task_result.get("wsl_output_dir") or "").strip())
                    if str(last_task_result.get("wsl_output_dir") or "").strip()
                    else (windows_path_to_wsl(self._output_root, distro=runtime.distro) if self._output_root else "")
                ),
            },
        )
