from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import settings
from ..dinsar_engines.landsar_engine import (
    LandsarEngine,
    _collect_tail,
    _decode_line,
    _extract_date,
    _find_dll,
    _landsar_process_env,
    _norm_path,
    _path_search_dirs,
    _summarize_landsar_failure,
)


SBAS_PROID = str(settings.LANDSAR_SBAS_PROID or "280039").strip() or "280039"
SBAS_PROCESS_NAME = str(settings.LANDSAR_SBAS_PROCESS_NAME or "SBAS Stream").strip() or "SBAS Stream"
IMPORT_PROID = "100016"
PROCESSOR_CODE = "landsar_sbas"
PROFILE_CODE = "lt1_landsar_sbas"
ENGINE_CODE = "landsar"
WORKFLOW_CODE = "sbas_insar"
_DEFAULT_MIN_COMMON_OVERLAP_RATIO = 0.30

_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._-]+")
_SUCCESS_RE = re.compile(r"(success|成功)", re.IGNORECASE)
_UNSUPPORTED_PROID_RE = re.compile(r"(Cannot read this ID|Unknown ID)", re.IGNORECASE)
_SBAS_EXTRA_DLLS = (
    "SAR_InSAR_MTInSARModel.dll",
    "SAR_InSAR_PSInSAR_CSU.dll",
    "SAR_InSAR_MBCP_MTInSARModel.dll",
)

def _effective_min_common_overlap_ratio(value: Any) -> float:
    try:
        requested = float(value or 0.0)
    except (TypeError, ValueError):
        requested = 0.0
    try:
        configured = float(settings.GAMMA_SBAS_MIN_COMMON_OVERLAP_RATIO or _DEFAULT_MIN_COMMON_OVERLAP_RATIO)
    except (TypeError, ValueError):
        configured = _DEFAULT_MIN_COMMON_OVERLAP_RATIO
    requested = min(1.0, max(0.0, requested))
    configured = min(1.0, max(0.0, configured))
    return max(requested, configured)


_DEFAULT_PARAMS: dict[str, Any] = {
    "dem_data_type": 1,
    "dem_format": 4,
    "do_select_intf": 1,
    "intf_method": 0,
    "perp_baseline": 200,
    "time_baseline": 300,
    "doppler_baseline": 100,
    "do_multilook": 1,
    "multi_pass": 0,
    "az_looks": 3,
    "rg_looks": 3,
    "do_select_points": 1,
    "da_threshold": 0.25,
    "intensity_threshold": 0.0,
    "calibration_method": 0,
    "calibration_threshold": 0.4,
    "fine_reg_window": 128,
    "resample_factor": 2,
    "do_coherent_intf": 1,
    "do_coherent_diff": 1,
    "remove_trend_phase": 0,
    "do_build_network": 1,
    "network_type": 0,
    "max_arc_distance": 1000,
    "do_arc_solve": 1,
    "solve_method": 0,
    "max_temporal_coh": 0.7,
    "do_network_adjust": 1,
    "ref_point_index": 0,
    "do_spatial_filter": 1,
    "spatial_filter_dist": 1000,
    "do_phase_unwrap": 1,
    "unwrap_ref_index": 0,
    "do_nonlinear_deform": 1,
    "time_filter_threshold": 0.3,
    "do_deform_integrate": 1,
    "do_los_output": 1,
    "gen_vector_map": 0,
    "gen_pre_raster": 0,
    "gen_post_raster": 1,
    "post_raster_res": 0,
    "window_size": 0,
}

_INT_PARAM_KEYS = {
    "dem_data_type",
    "dem_format",
    "do_select_intf",
    "intf_method",
    "perp_baseline",
    "time_baseline",
    "doppler_baseline",
    "do_multilook",
    "multi_pass",
    "az_looks",
    "rg_looks",
    "do_select_points",
    "calibration_method",
    "fine_reg_window",
    "resample_factor",
    "do_coherent_intf",
    "do_coherent_diff",
    "remove_trend_phase",
    "do_build_network",
    "network_type",
    "max_arc_distance",
    "do_arc_solve",
    "solve_method",
    "do_network_adjust",
    "ref_point_index",
    "do_spatial_filter",
    "spatial_filter_dist",
    "do_phase_unwrap",
    "unwrap_ref_index",
    "do_nonlinear_deform",
    "do_deform_integrate",
    "do_los_output",
    "gen_vector_map",
    "gen_pre_raster",
    "gen_post_raster",
    "post_raster_res",
    "window_size",
}

_FLOAT_PARAM_KEYS = {
    "da_threshold",
    "intensity_threshold",
    "calibration_threshold",
    "max_temporal_coh",
    "time_filter_threshold",
}


def _utc_text(value: datetime | None = None) -> str:
    return (value or datetime.utcnow()).isoformat(timespec="seconds") + "Z"


def _safe_name(value: Any, fallback: str = "task") -> str:
    text = _SAFE_NAME_RE.sub("_", str(value or "").strip()).strip("._-")
    return text or fallback


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return bool(value)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _short_hash(value: Any, length: int = 10) -> str:
    digest = hashlib.sha1(str(value or "").encode("utf-8", errors="ignore")).hexdigest()
    return digest[: max(6, int(length or 10))]


def _split_config_paths(value: str) -> list[str]:
    return [
        part.strip().strip('"').strip("'")
        for part in str(value or "").replace(";", ",").split(",")
        if part.strip().strip('"').strip("'")
    ]


def format_stack_label(stack_manifest: dict[str, Any], fallback: str) -> str:
    stack = stack_manifest.get("stack") or {}
    dates = stack_manifest.get("dates") or []
    parts = [
        stack.get("satellite") or "LT1",
        stack.get("orbit_direction"),
        f"relOrbit {stack.get('relative_orbit')}" if stack.get("relative_orbit") else None,
        dates[0] if dates else None,
    ]
    label = " ".join(str(item).strip() for item in parts if str(item or "").strip())
    return label or fallback


def _normalize_path_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[\r\n;,]+", value)
    else:
        raw_items = list(value)
    return [_norm_path(item) for item in raw_items if str(item or "").strip()]


def count_landsar_slc_files(input_data_dir: str) -> tuple[int, list[dict[str, str]]]:
    input_dir = _norm_path(input_data_dir)
    if not os.path.isdir(input_dir):
        return 0, []
    pairs: list[dict[str, str]] = []
    for xml_path in sorted(Path(input_dir).glob("LT1*_SLC.xml"), key=lambda item: item.name.lower()):
        tif_path = xml_path.with_name(xml_path.name.replace("_SLC.xml", "_SLC.tif"))
        if not tif_path.is_file():
            tif_path = xml_path.with_name(xml_path.name.replace("_SLC.xml", "_SLC.tiff"))
        if not tif_path.is_file():
            continue
        pairs.append(
            {
                "date": _extract_date(xml_path.name),
                "xml": _norm_path(xml_path),
                "tif": _norm_path(tif_path),
                "name": xml_path.stem,
            }
        )
    return len(pairs), pairs


def _generate_sbas_param_file(
    filepath: str,
    *,
    slc_folder: str,
    dem_path: str,
    output_dir: str,
    project_name: str,
    params: dict[str, Any],
) -> str:
    def p(key: str, default: Any) -> Any:
        return params.get(key, default)

    lines = [
        SBAS_PROCESS_NAME,
        f"ID                  {SBAS_PROID}",
        "",
        "输入输出数据设置",
        f"SLC文件夹路径               <{slc_folder}>",
        f"参考DEM数据地址             <{dem_path}>",
        f"参考DEM数据类型_0文件夹_1文件    {p('dem_data_type', 1)}",
        f"参考DEM数据格式_0strm1*1deg/1strm5*5deg/2aster/3tandem/4coper    {p('dem_format', 4)}",
        f"项目名称                    {project_name}",
        f"项目输出根目录              <{output_dir}>",
        "",
        f"是否执行选取干涉对           {p('do_select_intf', 1)}",
        f"干涉对选取方法_0single_1prim   {p('intf_method', 0)}",
        f"干涉对垂直基线阈值           {p('perp_baseline', 200)}",
        f"干涉对时间基线阈值           {p('time_baseline', 300)}",
        f"干涉对多普勒基线阈值         {p('doppler_baseline', 100)}",
        "",
        f"是否执行RSLC多视            {p('do_multilook', 1)}",
        f"是否需要做多次              {p('multi_pass', 0)}",
        f"方位向多视数                {p('az_looks', 3)}",
        f"距离向多视数                {p('rg_looks', 3)}",
        "",
        f"是否执行选取相干点           {p('do_select_points', 1)}",
        f"振幅差阈值最大              {p('da_threshold', 0.25)}",
        f"强度最小阈值                {p('intensity_threshold', 0.0)}",
        f"定标方法                    {p('calibration_method', 0)}",
        f"相干点强度定标阈值           {p('calibration_threshold', 0.4)}",
        f"精配准窗口尺寸              {p('fine_reg_window', 128)}",
        f"重采样因子                  {p('resample_factor', 2)}",
        "",
        f"是否执行相干点干涉           {p('do_coherent_intf', 1)}",
        f"是否执行相干点差分干涉       {p('do_coherent_diff', 1)}",
        f"是否去除趋势性相位           {p('remove_trend_phase', 0)}",
        "",
        f"是否执行相干点网络构建       {p('do_build_network', 1)}",
        f"网型_0delaunay_1star_2free   {p('network_type', 0)}",
        f"弧段最大距离阈值             {p('max_arc_distance', 1000)}",
        "",
        f"是否执行弧段模型解算         {p('do_arc_solve', 1)}",
        f"解算方法_0periodogram_1lsm   {p('solve_method', 0)}",
        f"弧段最大时域相干性阈值       {p('max_temporal_coh', 0.7)}",
        "",
        f"是否执行全体点网平差         {p('do_network_adjust', 1)}",
        f"参考点索引号                 {p('ref_point_index', 0)}",
        "",
        f"是否执行相位矢量数据空间滤波  {p('do_spatial_filter', 1)}",
        f"空间滤波距离阈值             {p('spatial_filter_dist', 1000)}",
        "",
        f"是否执行相干点相位解缠       {p('do_phase_unwrap', 1)}",
        f"相位解缠参考点索引号         {p('unwrap_ref_index', 0)}",
        "",
        f"是否执行相干点非线性形变提取  {p('do_nonlinear_deform', 1)}",
        f"时间滤波时间阈值             {p('time_filter_threshold', 0.3)}",
        "",
        f"是否执行相干点形变整合       {p('do_deform_integrate', 1)}",
        f"是否执行LOS向时序文件输出    {p('do_los_output', 1)}",
        f"是否生成矢量图               {p('gen_vector_map', 0)}",
        f"是否生成编码前栅格图          {p('gen_pre_raster', 0)}",
        f"是否生成编码后栅格图          {p('gen_post_raster', 1)}",
        f"编码后分辨率                  {p('post_raster_res', 0)}",
        f"窗口大小                     {p('window_size', 0)}",
    ]
    target = _norm_path(filepath)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return target


def _generate_lt1_multiscene_import_param_file(
    filepath: str,
    *,
    scene_dirs: list[str],
    export_dir: str,
    sat_mode: str = "MONO",
) -> str:
    lines = [
        "卫星数据导入LT-1",
        f"处理编号       {IMPORT_PROID}",
        "设置数据导入形式_0文件夹导入_1数据导入  文件夹导入",
        "读取成像参数文件_0否_1是 1",
        "读取SLC数据文件_0否_1是 1",
        "文件夹导入标识  TRUE",
        f"文件夹导入个数  {len(scene_dirs)}",
    ]
    for index, scene_dir in enumerate(scene_dirs, start=1):
        lines.append(f"文件夹{index}路径  <{scene_dir}>")
    lines.extend(
        [
            "数据导入  FALSE",
            f"输入卫星数据格式  {sat_mode}",
            "输入主影像成像参数文件路径  <>",
            "输入主影像SLC数据文件路径  <>",
            "输入主影像RPB数据文件路径  <>",
            "输入辅影像成像参数文件路径  <>",
            "输入辅影像SLC数据文件路径  <>",
            "输入辅影像RPB数据文件路径  <>",
            "设置数据导出目标路径_0原目录_1新目录  1",
            f"设置输出文件目录  <{export_dir}>",
        ]
    )
    target = _norm_path(filepath)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return target


class LandsarSbasService:
    def __init__(self) -> None:
        self.work_root = Path(settings.LANDSAR_SBAS_WORK_ROOT or Path(settings.LANDSAR_WORK_ROOT) / "sbas")
        self.product_root = Path(settings.LANDSAR_SBAS_PRODUCT_ROOT or Path(settings.TIMESERIES_PRODUCT_DIR) / "sbas_landsar")
        self._engine = LandsarEngine()

    def _allocate_work_run_root(self, run_id: str, created_at: datetime) -> Path:
        short_parent = self.work_root / "x"
        short_parent.mkdir(parents=True, exist_ok=True)
        base_name = f"r{created_at.strftime('%y%m%d%H%M%S')}_{_short_hash(run_id, 8)}"
        for index in range(1000):
            suffix = "" if index == 0 else f"_{index}"
            candidate = short_parent / f"{base_name}{suffix}"
            if not candidate.exists():
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
        raise RuntimeError("Unable to allocate a short LandSAR SBAS work directory.")

    def get_run_root(self) -> str:
        root = self.product_root / "runs"
        root.mkdir(parents=True, exist_ok=True)
        return _norm_path(root)

    def configured_run_root(self) -> str:
        return _norm_path(self.product_root / "runs")

    def get_capabilities(self) -> dict[str, Any]:
        availability = self.check_available()
        return {
            "workflow_code": WORKFLOW_CODE,
            "processor_code": PROCESSOR_CODE,
            "profile_code": PROFILE_CODE,
            "engine_code": ENGINE_CODE,
            "proid": SBAS_PROID,
            "process_name": SBAS_PROCESS_NAME,
            "enabled": bool(settings.LANDSAR_SBAS_ENABLED),
            "available": availability["available"],
            "status": availability["status"],
            "message": availability["message"],
            "checks": availability["checks"],
            "work_root": str(self.work_root),
            "product_root": str(self.product_root),
            "source_roots": _split_config_paths(settings.LANDSAR_SBAS_SOURCE_ROOTS),
            "default_dem_path": self.default_dem_path,
            "default_timeout_seconds": settings.LANDSAR_SBAS_TIMEOUT_SECONDS,
            "min_scenes": settings.LANDSAR_SBAS_MIN_SCENES,
            "min_common_overlap_ratio": _effective_min_common_overlap_ratio(None),
            "params_schema": self.params_schema(),
        }

    @property
    def default_dem_path(self) -> str:
        dem_path = str(settings.LANDSAR_SBAS_DEM_PATH or settings.LANDSAR_DEM_PATH or "").strip()
        return _norm_path(dem_path) if dem_path else ""

    def resolve_lt1_scene_dir(self, file_path: Any) -> str:
        path = Path(_norm_path(file_path))
        candidates = [path]
        if path.is_file():
            candidates.insert(0, path.parent)
        for candidate in candidates:
            if self._looks_like_lt1_scene_dir(candidate):
                return _norm_path(candidate)
        return ""

    @staticmethod
    def _looks_like_lt1_scene_dir(path: Path) -> bool:
        if not path.is_dir():
            return False
        has_meta = any(path.glob("*.meta.xml"))
        has_tif = bool(list(path.glob("*.tif")) + list(path.glob("*.tiff")))
        return has_meta and has_tif

    def params_schema(self) -> dict[str, Any]:
        return {
            "dem_path": {"label": "DEM 文件", "type": "string", "default": self.default_dem_path},
            "dem_format": {"label": "DEM 格式", "type": "number", "default": 4, "min": 0, "max": 4},
            "intf_method": {"label": "干涉对方法", "type": "select", "default": 0, "options": [{"value": 0, "label": "single"}, {"value": 1, "label": "prim"}]},
            "perp_baseline": {"label": "垂直基线阈值", "type": "number", "default": 200},
            "time_baseline": {"label": "时间基线阈值", "type": "number", "default": 300},
            "doppler_baseline": {"label": "多普勒基线阈值", "type": "number", "default": 100},
            "az_looks": {"label": "方位向多视", "type": "number", "default": 3, "min": 1},
            "rg_looks": {"label": "距离向多视", "type": "number", "default": 3, "min": 1},
            "da_threshold": {"label": "DA 阈值", "type": "number", "default": 0.25, "min": 0, "max": 1},
            "network_type": {"label": "网络类型", "type": "select", "default": 0, "options": [{"value": 0, "label": "Delaunay"}, {"value": 1, "label": "Star"}, {"value": 2, "label": "Free"}]},
            "solve_method": {"label": "解算方法", "type": "select", "default": 0, "options": [{"value": 0, "label": "Periodogram"}, {"value": 1, "label": "LSM"}]},
            "gen_vector_map": {"label": "生成矢量图", "type": "boolean", "default": False},
            "gen_post_raster": {"label": "生成编码后栅格", "type": "boolean", "default": True},
        }

    def check_available(self) -> dict[str, Any]:
        engine_availability = self._engine.check_available()
        console_path = self._engine._console_exe
        home = self._engine._home
        search_dirs = _path_search_dirs(os.path.dirname(_norm_path(console_path)), home)
        dll_checks = []
        for name in _SBAS_EXTRA_DLLS:
            path = _find_dll(name, search_dirs)
            dll_checks.append(
                {
                    "name": name,
                    "ok": bool(path),
                    "detail": path or "missing",
                    "optional": name != "SAR_InSAR_MTInSARModel.dll",
                }
            )
        required_extra_ok = all(item["ok"] or item.get("optional") for item in dll_checks)
        enabled = bool(settings.LANDSAR_SBAS_ENABLED)
        available = enabled and bool(engine_availability.available) and required_extra_ok
        checks = [
            {"name": "LANDSAR_SBAS_ENABLED", "ok": enabled, "detail": str(enabled).lower()},
            *engine_availability.checks,
            *dll_checks,
        ]
        message = "LandSAR SBAS console is available." if available else engine_availability.message
        if bool(engine_availability.available) and not required_extra_ok:
            missing = [item["name"] for item in dll_checks if not item["ok"] and not item.get("optional")]
            message = f"LandSAR SBAS dependencies are missing: {', '.join(missing)}"
        if not enabled:
            message = "LandSAR SBAS is disabled."
        return {
            "available": available,
            "status": "ok" if available else "unavailable",
            "message": message,
            "checks": checks,
        }

    def normalize_params(self, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        raw_extra = dict(extra or {})
        normalized = dict(_DEFAULT_PARAMS)
        for key, value in raw_extra.items():
            if key in {"dem_path", "project_name"}:
                continue
            if key not in normalized:
                continue
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if key in _INT_PARAM_KEYS:
                try:
                    normalized[key] = int(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be an integer.") from exc
            elif key in _FLOAT_PARAM_KEYS:
                try:
                    normalized[key] = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be a number.") from exc
            else:
                normalized[key] = value
        for key in ("gen_vector_map", "gen_pre_raster", "gen_post_raster", "do_los_output"):
            if key in raw_extra:
                normalized[key] = 1 if _coerce_bool(raw_extra.get(key)) else 0
        return normalized

    def _iter_candidate_task_dirs(self, root_dir: str) -> list[str]:
        root = _norm_path(root_dir)
        if not os.path.isdir(root):
            return []
        if os.path.basename(root).lower() == "input_data":
            parent = os.path.dirname(root)
            return [_norm_path(parent)] if parent else []
        if os.path.isdir(os.path.join(root, "Input_Data")):
            return [root]
        return [
            _norm_path(entry.path)
            for entry in sorted(os.scandir(root), key=lambda item: item.name.lower())
            if entry.is_dir()
            and entry.name.lower().startswith("task_")
            and os.path.isdir(os.path.join(entry.path, "Input_Data"))
        ]

    def validate_root_dir(
        self,
        root_dir: str,
        *,
        min_scenes: int | None = None,
        num_to_process: int = 0,
        rerun_mode: str = "rerun_all",
    ) -> dict[str, Any]:
        normalized_root = _norm_path(root_dir)
        if not normalized_root or not os.path.isdir(normalized_root):
            raise ValueError(f"LandSAR SBAS root_dir does not exist or is not a directory: {root_dir}")
        min_count = max(3, int(min_scenes or settings.LANDSAR_SBAS_MIN_SCENES or 3))
        candidates = self._iter_candidate_task_dirs(normalized_root)
        valid: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        skipped_completed = 0
        for task_dir in candidates:
            task_name = os.path.basename(task_dir)
            input_dir = os.path.join(task_dir, "Input_Data")
            slc_count, pairs = count_landsar_slc_files(input_dir)
            if slc_count < min_count:
                invalid.append(
                    {
                        "task_name": task_name,
                        "task_dir": task_dir,
                        "reason": f"Input_Data SLC pair count is {slc_count}, expected >= {min_count}",
                    }
                )
                continue
            if rerun_mode == "unfinished_only" and self._has_completed_output(os.path.join(task_dir, "Output_Data")):
                skipped_completed += 1
                continue
            dates = [item.get("date") for item in pairs if item.get("date")]
            valid.append(
                {
                    "task_name": task_name,
                    "task_dir": task_dir,
                    "input_data_dir": input_dir,
                    "slc_count": slc_count,
                    "dates": dates,
                    "date_start": dates[0] if dates else None,
                    "date_end": dates[-1] if dates else None,
                    "scenes": pairs,
                }
            )
        if num_to_process and num_to_process > 0:
            valid = valid[: int(num_to_process)]
        return {
            "schema": "insar.landsar-sbas-task-discovery/v1",
            "root_dir": normalized_root,
            "min_scenes": min_count,
            "candidate_count": len(candidates),
            "task_count": len(valid),
            "selected_task_count": len(valid),
            "skipped_completed_count": skipped_completed,
            "invalid_candidates": invalid,
            "items": valid,
        }

    def discover_tasks(
        self,
        *,
        root_dir: str | None = None,
        min_scenes: int | None = None,
        num_to_process: int = 0,
        rerun_mode: str = "rerun_all",
    ) -> dict[str, Any]:
        root_text = str(root_dir or "").strip()
        roots = [root_text] if root_text else _split_config_paths(settings.LANDSAR_SBAS_SOURCE_ROOTS)
        items: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for root in roots:
            try:
                result = self.validate_root_dir(
                    root,
                    min_scenes=min_scenes,
                    num_to_process=num_to_process,
                    rerun_mode=rerun_mode,
                )
                items.extend(result.get("items") or [])
                invalid.extend(result.get("invalid_candidates") or [])
            except ValueError as exc:
                errors.append({"root_dir": root, "error": str(exc)})
        if num_to_process and num_to_process > 0:
            items = items[: int(num_to_process)]
        return {
            "schema": "insar.landsar-sbas-task-discovery/v1",
            "generated_at": _utc_text(),
            "root_dir": root_text,
            "source_roots": roots,
            "min_scenes": max(3, int(min_scenes or settings.LANDSAR_SBAS_MIN_SCENES or 3)),
            "task_count": len(items),
            "items": items,
            "invalid_candidates": invalid,
            "errors": errors,
        }

    def materialize_stack(
        self,
        *,
        source_dirs: Any,
        dest_root: str | None = None,
        task_name: str | None = None,
        min_scenes: int | None = None,
        max_scenes: int = 0,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        sources = _normalize_path_items(source_dirs)
        if not sources:
            raise ValueError("LandSAR SBAS stack materialization requires at least one source directory.")
        target_root_text = _norm_path(dest_root or (_split_config_paths(settings.LANDSAR_SBAS_SOURCE_ROOTS)[:1] or [self.work_root])[0])
        if not target_root_text:
            raise ValueError("LandSAR SBAS stack materialization requires dest_root.")
        min_count = max(3, int(min_scenes or settings.LANDSAR_SBAS_MIN_SCENES or 3))
        scenes, scan_errors, duplicate_count, missing_tif_count = self._collect_slc_scenes(sources)
        if max_scenes and int(max_scenes) > 0:
            scenes = scenes[: int(max_scenes)]
        if len(scenes) < min_count:
            raise ValueError(f"Only {len(scenes)} valid LT1 SLC scenes found, expected >= {min_count}.")

        dates = [scene.get("date") for scene in scenes if scene.get("date")]
        date_start = dates[0] if dates else None
        date_end = dates[-1] if dates else None
        default_name = "Task_LandSAR_SBAS"
        if date_start and date_end:
            default_name = f"Task_{date_start}_{date_end}_SBAS"
        normalized_task_name = _safe_name(task_name or default_name, default_name)
        if not normalized_task_name.lower().startswith("task_"):
            normalized_task_name = f"Task_{normalized_task_name}"

        target_root = Path(target_root_text)
        task_dir = target_root / normalized_task_name
        input_dir = task_dir / "Input_Data"
        existing_files = list(input_dir.glob("*")) if input_dir.is_dir() else []
        if existing_files and not overwrite and not dry_run:
            raise ValueError(f"Target Input_Data is not empty: {input_dir}. Enable overwrite to reuse it.")

        copied_files: list[dict[str, str]] = []
        skipped_existing = 0
        scene_records: list[dict[str, Any]] = []
        if not dry_run:
            input_dir.mkdir(parents=True, exist_ok=True)

        for scene in scenes:
            target_files: list[str] = []
            for source_file in scene["files"]:
                target_file = input_dir / source_file.name
                if not dry_run:
                    if target_file.exists() and not overwrite:
                        skipped_existing += 1
                    else:
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source_file, target_file)
                        copied_files.append({"source": str(source_file), "target": str(target_file)})
                target_files.append(str(target_file))
            scene_records.append(
                {
                    "date": scene.get("date"),
                    "name": scene.get("name"),
                    "source_dir": scene.get("source_dir"),
                    "source_xml": scene.get("xml"),
                    "source_tif": scene.get("tif"),
                    "target_files": target_files,
                }
            )

        slc_count = len(scenes) if dry_run else count_landsar_slc_files(str(input_dir))[0]
        task_item = {
            "task_name": normalized_task_name,
            "task_dir": str(task_dir),
            "input_data_dir": str(input_dir),
            "slc_count": slc_count,
            "dates": dates,
            "date_start": date_start,
            "date_end": date_end,
            "scenes": scene_records,
        }
        manifest = {
            "schema": "insar.landsar-sbas-stack-materialization/v1",
            "generated_at": _utc_text(),
            "dry_run": dry_run,
            "source_dirs": sources,
            "dest_root": str(target_root),
            "task": task_item,
            "min_scenes": min_count,
            "max_scenes": int(max_scenes or 0),
            "overwrite": bool(overwrite),
            "duplicate_scene_count": duplicate_count,
            "missing_tif_count": missing_tif_count,
            "scan_errors": scan_errors,
            "copied_file_count": len(copied_files),
            "skipped_existing_count": skipped_existing,
            "copied_files": copied_files[:200],
        }
        if not dry_run:
            _write_json(task_dir / "landsar_sbas_stack_manifest.json", manifest)
        return {
            "schema": "insar.landsar-sbas-stack-materialization-result/v1",
            "ready": slc_count >= min_count,
            "task": task_item,
            "task_name": normalized_task_name,
            "task_dir": str(task_dir),
            "input_data_dir": str(input_dir),
            "slc_count": slc_count,
            "copied_file_count": len(copied_files),
            "duplicate_scene_count": duplicate_count,
            "missing_tif_count": missing_tif_count,
            "scan_errors": scan_errors,
            "manifest_path": None if dry_run else str(task_dir / "landsar_sbas_stack_manifest.json"),
        }

    def _collect_slc_scenes(self, source_dirs: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, str]], int, int]:
        scenes_by_key: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        duplicate_count = 0
        missing_tif_count = 0
        for source_dir in source_dirs:
            if not os.path.isdir(source_dir):
                errors.append({"source_dir": source_dir, "error": "source directory does not exist"})
                continue
            for current_dir, _, filenames in os.walk(source_dir):
                for filename in filenames:
                    upper = filename.upper()
                    if not (upper.startswith("LT1") and upper.endswith("_SLC.XML")):
                        continue
                    xml_path = Path(current_dir) / filename
                    tif_path = xml_path.with_name(f"{xml_path.stem}.tif")
                    if not tif_path.is_file():
                        tif_path = xml_path.with_name(f"{xml_path.stem}.tiff")
                    if not tif_path.is_file():
                        missing_tif_count += 1
                        continue
                    key = xml_path.stem.lower()
                    if key in scenes_by_key:
                        duplicate_count += 1
                        continue
                    files = sorted(
                        [path for path in xml_path.parent.glob(f"{xml_path.stem}.*") if path.is_file()],
                        key=lambda item: item.name.lower(),
                    )
                    scenes_by_key[key] = {
                        "date": _extract_date(xml_path.name),
                        "name": xml_path.stem,
                        "source_dir": str(xml_path.parent),
                        "xml": str(xml_path),
                        "tif": str(tif_path),
                        "files": files,
                    }
        return (
            sorted(scenes_by_key.values(), key=lambda item: (item.get("date") or "", item.get("name") or "")),
            errors,
            duplicate_count,
            missing_tif_count,
        )

    def import_stack_scenes(
        self,
        *,
        scenes: list[dict[str, Any]],
        dest_root: str | None = None,
        task_name: str | None = None,
        min_scenes: int | None = None,
        sat_mode: str = "MONO",
        overwrite: bool = False,
        timeout_seconds: int | None = None,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        min_count = max(3, int(min_scenes or settings.LANDSAR_SBAS_MIN_SCENES or 3))
        normalized_scenes: list[dict[str, Any]] = []
        seen_dirs: set[str] = set()
        for scene in scenes:
            scene_dir = self._resolve_import_scene_dir(scene)
            if not scene_dir:
                continue
            if scene_dir in seen_dirs:
                continue
            seen_dirs.add(scene_dir)
            normalized_scenes.append({**scene, "scene_dir": scene_dir})
        normalized_scenes.sort(key=lambda item: str(item.get("date") or item.get("imaging_date") or ""))
        if len(normalized_scenes) < min_count:
            raise ValueError(f"Only {len(normalized_scenes)} database LT-1 scenes selected, expected >= {min_count}.")

        target_root_text = _norm_path(dest_root or (_split_config_paths(settings.LANDSAR_SBAS_SOURCE_ROOTS)[:1] or [self.work_root])[0])
        dates = [str(scene.get("date") or scene.get("imaging_date") or "")[:8] for scene in normalized_scenes if str(scene.get("date") or scene.get("imaging_date") or "").strip()]
        date_start = dates[0] if dates else None
        date_end = dates[-1] if dates else None
        default_name = f"Task_{date_start}_{date_end}_SBAS" if date_start and date_end else "Task_LandSAR_DB_SBAS"
        normalized_task_name = _safe_name(task_name or default_name, default_name)
        if not normalized_task_name.lower().startswith("task_"):
            normalized_task_name = f"Task_{normalized_task_name}"

        task_dir = Path(target_root_text) / normalized_task_name
        input_dir = task_dir / "Input_Data"
        output_dir = task_dir / "Output_Data"
        if input_dir.is_dir() and any(input_dir.iterdir()) and not overwrite:
            existing_count, _ = count_landsar_slc_files(str(input_dir))
            if existing_count >= min_count:
                return self._db_import_result(
                    task_dir=task_dir,
                    input_dir=input_dir,
                    scenes=normalized_scenes,
                    status="LANDSAR_SBAS_INPUT_READY",
                    returncode=0,
                    stdout_text="",
                    error="",
                    command=[],
                    param_file="",
                    skipped=True,
                    min_scenes=min_count,
                    sat_mode=sat_mode,
                )
            raise ValueError(f"Target Input_Data exists but is incomplete: {input_dir}. Enable overwrite to retry.")
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        availability = self.check_available()
        if not availability["available"]:
            raise ValueError(f"LandSAR import is not available: {availability['message']}")
        console_path = self._engine._console_exe
        home = self._engine._home
        config_ok, config_detail = self._engine._ensure_config_csv()
        if not config_ok:
            raise ValueError(f"LandSAR config.csv is not ready: {config_detail}")
        auth_ok, auth_detail = self._engine._start_auth_server_if_needed()
        if not auth_ok:
            raise ValueError(f"LandSAR network license server is not ready: {auth_detail}")

        timeout = max(60, int(timeout_seconds or settings.LANDSAR_SBAS_TIMEOUT_SECONDS or 172800))
        param_file = _generate_lt1_multiscene_import_param_file(
            str(input_dir / f"{IMPORT_PROID}_timeseries.txt"),
            scene_dirs=[scene["scene_dir"] for scene in normalized_scenes],
            export_dir=str(input_dir),
            sat_mode=str(sat_mode or "MONO").strip() or "MONO",
        )
        command = [console_path, param_file]
        self._emit(progress_callback, "INFO", f"LandSAR 100016 import started: {normalized_task_name}, scenes={len(normalized_scenes)}")
        rc, stdout_text, timed_out = self._run_console(
            command,
            cwd=home if os.path.isdir(home) else os.path.dirname(console_path),
            log_path=str(input_dir / f"{IMPORT_PROID}_timeseries_console.log"),
            timeout=timeout,
            progress_callback=progress_callback,
            task_name=normalized_task_name,
        )
        slc_count, _ = count_landsar_slc_files(str(input_dir))
        success = rc == 0 and slc_count >= min_count and self._has_completed_import(str(input_dir))
        error = ""
        if timed_out:
            error = f"LandSAR import timed out after {timeout}s."
            success = False
        elif rc != 0:
            error = _summarize_landsar_failure(stdout_text, "LandSAR import", rc)
        elif slc_count < min_count:
            error = f"LandSAR import produced {slc_count} SLC scenes, expected >= {min_count}."
        elif not self._has_completed_import(str(input_dir)):
            error = "LandSAR import success marker is missing."

        status = "LANDSAR_SBAS_INPUT_READY" if success else "LANDSAR_SBAS_IMPORT_FAILED"
        result = self._db_import_result(
            task_dir=task_dir,
            input_dir=input_dir,
            scenes=normalized_scenes,
            status=status,
            returncode=rc,
            stdout_text=stdout_text,
            error=error,
            command=command,
            param_file=param_file,
            skipped=False,
            min_scenes=min_count,
            sat_mode=sat_mode,
        )
        if not success:
            raise RuntimeError(error or "LandSAR import failed.")
        self._emit(progress_callback, "INFO", f"LandSAR 100016 import completed: {normalized_task_name}, SLC={slc_count}")
        return result

    def _create_run_from_gamma_stack(
        self,
        stack_id: str,
        *,
        sensor_family: str = "LT1",
        run_label: str | None = None,
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int | None = None,
        require_orbits: bool = False,
        discovery_mode: str = "strict",
        admin_region: str | None = None,
        aoi_bbox: dict[str, Any] | None = None,
        min_aoi_coverage_ratio: float = 0.01,
        min_common_overlap_ratio: float | None = None,
        dem_path: str | None = None,
        timeout_seconds: int | None = None,
        import_timeout_seconds: int | None = None,
        params: Optional[dict[str, Any]] = None,
        task_name: str | None = None,
        dest_root: str | None = None,
        overwrite_input: bool = False,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        if not bool(settings.LANDSAR_SBAS_ENABLED):
            raise ValueError("LandSAR SBAS is disabled.")
        normalized_sensor = str(sensor_family or "LT1").strip().upper()
        if normalized_sensor != "LT1":
            raise ValueError("LandSAR SBAS currently supports LT-1 stacks only.")

        normalized_min = max(3, int(min_scenes or settings.LANDSAR_SBAS_MIN_SCENES or 3))
        min_common_overlap_ratio = _effective_min_common_overlap_ratio(min_common_overlap_ratio)
        params_payload = self.normalize_params(params or {})
        normalized_dem = _norm_path(dem_path or self.default_dem_path)
        if not normalized_dem or not os.path.isfile(normalized_dem):
            raise ValueError(f"LandSAR SBAS DEM file is missing: {normalized_dem or '<empty>'}")

        from .sbas_insar_production_service import sbas_insar_production_service

        audit = sbas_insar_production_service.audit_stack(
            stack_id,
            sensor_family="LT1",
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=normalized_min,
            require_orbits=bool(require_orbits),
            discovery_mode=discovery_mode,
            admin_region=admin_region,
            aoi_bbox=aoi_bbox,
            min_aoi_coverage_ratio=min_aoi_coverage_ratio,
            min_common_overlap_ratio=min_common_overlap_ratio,
        )
        stack_manifest = dict(audit.get("manifest") or {})
        if stack_manifest.get("status") == "BLOCKED":
            blockers = "; ".join(str(item) for item in (stack_manifest.get("blockers") or []))
            raise ValueError(f"stack manifest is not ready for LandSAR input import: {blockers or 'blocked'}")
        selected_scenes = self._normalize_stack_scenes_for_import(stack_manifest.get("scenes") or [])
        if len(selected_scenes) < normalized_min:
            raise ValueError(f"Only {len(selected_scenes)} LT-1 scenes selected, expected >= {normalized_min}.")

        dates = sorted({
            str(scene.get("date") or scene.get("imaging_date") or "")[:8]
            for scene in selected_scenes
            if str(scene.get("date") or scene.get("imaging_date") or "").strip()
        })
        created_at = datetime.utcnow()
        safe_stack = _safe_name(stack_id, "stack")
        run_id = f"landsar_sbas_{created_at.strftime('%Y%m%dT%H%M%S%fZ')}_{safe_stack}"
        run_dir = Path(self.get_run_root()) / run_id
        work_run_root = self._allocate_work_run_root(run_id, created_at)
        native_root = work_run_root / "n"
        input_task_root = work_run_root / "i"
        publish_root = run_dir / "publish" / "landsar"
        for path in (native_root, input_task_root, publish_root):
            path.mkdir(parents=True, exist_ok=True)

        default_task_name = (
            f"Task_{dates[0]}_{dates[-1]}_SBAS"
            if dates
            else f"Task_{safe_stack}_SBAS"
        )
        normalized_task_name = _safe_name(task_name or default_task_name, default_task_name)
        if not normalized_task_name.lower().startswith("task_"):
            normalized_task_name = f"Task_{normalized_task_name}"

        import_dest_root = _norm_path(dest_root or input_task_root)
        discovery_params = {
            "sensor_family": "LT1",
            "source_roots": source_roots,
            "orbit_roots": orbit_roots,
            "min_scenes": normalized_min,
            "require_orbits": bool(require_orbits),
            "discovery_mode": discovery_mode,
            "admin_region": admin_region,
            "aoi_bbox": aoi_bbox,
            "min_aoi_coverage_ratio": min_aoi_coverage_ratio,
            "min_common_overlap_ratio": min_common_overlap_ratio,
        }
        manifest = {
            "schema": "insar.landsar-sbas-run/v1",
            "run_id": run_id,
            "run_label": run_label or f"LandSAR SBAS {format_stack_label(stack_manifest, stack_id)}",
            "workflow_code": WORKFLOW_CODE,
            "processor_code": PROCESSOR_CODE,
            "profile_code": PROFILE_CODE,
            "engine_code": ENGINE_CODE,
            "proid": SBAS_PROID,
            "process_name": SBAS_PROCESS_NAME,
            "execution_mode": "landsar_stack_selection_import_then_sbas",
            "source_mode": "gamma_production_area_stack_selection",
            "status": "LANDSAR_SBAS_INPUT_PENDING",
            "created_at": _utc_text(created_at),
            "created_by": created_by,
            "root_dir": import_dest_root,
            "run_dir": str(run_dir),
            "work_root": str(work_run_root),
            "work_root_strategy": "short_landsar_execution_path",
            "native_root": str(native_root),
            "input_task_root": import_dest_root,
            "publish_root": str(publish_root),
            "dem_path": normalized_dem,
            "params": params_payload,
            "timeout_seconds": max(60, int(timeout_seconds or settings.LANDSAR_SBAS_TIMEOUT_SECONDS or 172800)),
            "import_timeout_seconds": max(60, int(import_timeout_seconds or timeout_seconds or settings.LANDSAR_SBAS_TIMEOUT_SECONDS or 172800)),
            "min_scenes": normalized_min,
            "scene_count": len(selected_scenes),
            "task_count": 0,
            "pair_count": None,
            "date_start": dates[0] if dates else None,
            "date_end": dates[-1] if dates else None,
            "dates": dates,
            "tasks": [],
            "next_stage": "import_landsar_input",
            "input_import": {
                "status": "PENDING",
                "dest_root": import_dest_root,
                "task_name": normalized_task_name,
                "overwrite": bool(overwrite_input),
                "sat_mode": "MONO",
            },
            "source_stack": {
                "stack_id": stack_id,
                "source_system": "gamma_sbas_production_stack_discovery",
                "audit_status": audit.get("status"),
                "audit_manifest_path": audit.get("manifest_path"),
                "pair_network_path": audit.get("pair_network_path"),
                "discovery_params": discovery_params,
                "stack": stack_manifest.get("stack") or {},
                "geographic_coverage": stack_manifest.get("geographic_coverage"),
                "common_overlap_ratio": stack_manifest.get("common_overlap_ratio"),
                "scenes": selected_scenes,
                "scene_count": len(selected_scenes),
                "dates": dates,
                "warnings": stack_manifest.get("warnings") or [],
            },
            "geographic_coverage": stack_manifest.get("geographic_coverage"),
        }
        _write_json(run_dir / "run_manifest.json", manifest)
        _write_json(
            run_dir / "stack_manifest.json",
            {
                "schema": "insar.landsar-sbas-stack/v1",
                "run_id": run_id,
                "status": "READY_FOR_LANDSAR_INPUT_IMPORT",
                "source": "gamma_production_area_stack_selection",
                "stack_id": stack_id,
                "audit_manifest_path": audit.get("manifest_path"),
                "dates": dates,
                "scene_count": len(selected_scenes),
                "geographic_coverage": stack_manifest.get("geographic_coverage"),
                "scenes": selected_scenes,
            },
        )
        _write_json(
            run_dir / "workflow_summary.json",
            {
                "schema": "insar.landsar-sbas-workflow-summary/v1",
                "run_id": run_id,
                "process_name": SBAS_PROCESS_NAME,
                "ready": False,
                "status": "INPUT_PENDING",
                "task_count": 0,
                "completed_count": 0,
                "failed_count": 0,
            },
        )
        return self.get_run_detail(run_id)

    def select_best_stack_for_area(
        self,
        *,
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int | None = None,
        discovery_mode: str = "strict",
        admin_region: str | None = None,
        aoi_bbox: dict[str, Any] | None = None,
        min_aoi_coverage_ratio: float = 0.01,
        min_common_overlap_ratio: float | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        normalized_min = max(3, int(min_scenes or settings.LANDSAR_SBAS_MIN_SCENES or 3))
        min_common_overlap_ratio = _effective_min_common_overlap_ratio(min_common_overlap_ratio)
        from .sbas_insar_production_service import sbas_insar_production_service

        discovery = sbas_insar_production_service.discover_stacks(
            sensor_family="LT1",
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=normalized_min,
            require_orbits=False,
            include_scenes=False,
            limit=0,
            discovery_mode=discovery_mode,
            admin_region=admin_region,
            aoi_bbox=aoi_bbox,
            min_aoi_coverage_ratio=min_aoi_coverage_ratio,
            min_common_overlap_ratio=min_common_overlap_ratio,
        )
        candidates = list(discovery.get("items") or [])
        viable = [
            item for item in candidates
            if item.get("status") == "READY"
            and int(item.get("usable_scene_count") or item.get("scene_count") or 0) >= normalized_min
        ]
        if not viable:
            blockers = []
            for item in candidates[:5]:
                blockers.extend(str(blocker) for blocker in (item.get("blockers") or []) if blocker)
            region_label = admin_region or (discovery.get("aoi") or {}).get("name") or "<all configured roots>"
            detail = "; ".join(sorted(set(blockers))) if blockers else "no READY LT-1 stack matched the selected production area"
            raise ValueError(f"No LandSAR SBAS-ready LT-1 stack found for {region_label}: {detail}")

        selected = max(viable, key=self._landsar_stack_candidate_score)
        ranked = sorted(viable, key=self._landsar_stack_candidate_score, reverse=True)
        return {
            "schema": "insar.landsar-sbas-auto-selection/v1",
            "generated_at": _utc_text(),
            "selection_strategy": "gamma_production_area_max_scene_landsar_lt1_stack",
            "source_system": "gamma_sbas_production_stack_discovery",
            "processor_code": PROCESSOR_CODE,
            "sensor_family": "LT1",
            "min_scenes": normalized_min,
            "requested_limit": int(limit or 0),
            "discovery_limit": 0,
            "discovery_mode": discovery.get("discovery_mode") or discovery_mode,
            "admin_region": admin_region,
            "aoi": discovery.get("aoi"),
            "candidate_count": len(candidates),
            "viable_count": len(viable),
            "selected_stack_id": selected.get("stack_id"),
            "selected_stack": selected,
            "ranked_candidates": ranked[:10],
            "discovery_snapshot_path": discovery.get("snapshot_path"),
            "warnings": discovery.get("warnings") or [],
        }

    def create_run_from_best_stack(
        self,
        *,
        run_label: str | None = None,
        source_roots: list[str] | None = None,
        orbit_roots: list[str] | None = None,
        min_scenes: int | None = None,
        discovery_mode: str = "strict",
        admin_region: str | None = None,
        aoi_bbox: dict[str, Any] | None = None,
        min_aoi_coverage_ratio: float = 0.01,
        min_common_overlap_ratio: float | None = None,
        limit: int = 30,
        dem_path: str | None = None,
        timeout_seconds: int | None = None,
        import_timeout_seconds: int | None = None,
        params: Optional[dict[str, Any]] = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        min_common_overlap_ratio = _effective_min_common_overlap_ratio(min_common_overlap_ratio)
        selection = self.select_best_stack_for_area(
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=min_scenes,
            discovery_mode=discovery_mode,
            admin_region=admin_region,
            aoi_bbox=aoi_bbox,
            min_aoi_coverage_ratio=min_aoi_coverage_ratio,
            min_common_overlap_ratio=min_common_overlap_ratio,
            limit=limit,
        )
        selected_stack_id = str(selection.get("selected_stack_id") or "").strip()
        if not selected_stack_id:
            raise ValueError("LandSAR auto stack selection did not return a stack id.")
        selected_stack = selection.get("selected_stack") or {}
        auto_label = run_label or f"LandSAR SBAS {format_stack_label({'stack': selected_stack, 'dates': selected_stack.get('dates') or []}, selected_stack_id)}"
        detail = self._create_run_from_gamma_stack(
            selected_stack_id,
            sensor_family="LT1",
            run_label=auto_label,
            source_roots=source_roots,
            orbit_roots=orbit_roots,
            min_scenes=min_scenes,
            require_orbits=False,
            discovery_mode=discovery_mode,
            admin_region=admin_region,
            aoi_bbox=aoi_bbox,
            min_aoi_coverage_ratio=min_aoi_coverage_ratio,
            min_common_overlap_ratio=min_common_overlap_ratio,
            dem_path=dem_path,
            timeout_seconds=timeout_seconds,
            import_timeout_seconds=import_timeout_seconds,
            params=params,
            created_by=created_by,
        )
        run_id = (detail.get("run") or {}).get("run_id") or (detail.get("manifest") or {}).get("run_id")
        if run_id:
            run_dir = self._resolve_run_dir(str(run_id))
            manifest_path = run_dir / "run_manifest.json"
            manifest = _read_json(manifest_path)
            manifest["auto_selection"] = self._stack_selection_manifest(selection)
            manifest["source_mode"] = "gamma_production_area_stack_selection"
            source_stack = dict(manifest.get("source_stack") or {})
            source_stack["selection_strategy"] = selection.get("selection_strategy")
            source_stack["selected_by"] = "system"
            source_stack["source_system"] = "gamma_sbas_production_stack_discovery"
            manifest["source_stack"] = source_stack
            _write_json(manifest_path, manifest)
            detail = self.get_run_detail(str(run_id))
        detail["selection"] = selection
        return detail

    def _db_import_result(
        self,
        *,
        task_dir: Path,
        input_dir: Path,
        scenes: list[dict[str, Any]],
        status: str,
        returncode: int,
        stdout_text: str,
        error: str,
        command: list[str],
        param_file: str,
        skipped: bool,
        min_scenes: int,
        sat_mode: str,
    ) -> dict[str, Any]:
        slc_count, pairs = count_landsar_slc_files(str(input_dir))
        dates = [item.get("date") for item in pairs if item.get("date")]
        task = {
            "task_name": task_dir.name,
            "task_dir": str(task_dir),
            "input_data_dir": str(input_dir),
            "slc_count": slc_count,
            "dates": dates,
            "date_start": dates[0] if dates else None,
            "date_end": dates[-1] if dates else None,
            "scenes": pairs,
        }
        manifest = {
            "schema": "insar.landsar-sbas-db-import/v1",
            "generated_at": _utc_text(),
            "status": status,
            "ready": status == "LANDSAR_SBAS_INPUT_READY",
            "task": task,
            "min_scenes": min_scenes,
            "sat_mode": sat_mode,
            "skipped": skipped,
            "returncode": returncode,
            "error": error,
            "command": " ".join(command),
            "param_file": param_file,
            "stdout_tail": _collect_tail(stdout_text or "", 4000),
            "source_scenes": [
                {
                    "radar_data_id": scene.get("radar_data_id") or scene.get("id"),
                    "unique_id": scene.get("unique_id"),
                    "date": scene.get("date") or scene.get("imaging_date"),
                    "scene_dir": scene.get("scene_dir"),
                    "file_path": scene.get("file_path"),
                    "relative_orbit": scene.get("relative_orbit"),
                    "orbit_direction": scene.get("orbit_direction"),
                    "polarization": scene.get("polarization"),
                }
                for scene in scenes
            ],
        }
        _write_json(task_dir / "landsar_sbas_db_import_manifest.json", manifest)
        return {
            "schema": "insar.landsar-sbas-db-import-result/v1",
            "ready": manifest["ready"],
            "status": status,
            "task": task,
            "task_name": task["task_name"],
            "task_dir": task["task_dir"],
            "input_data_dir": task["input_data_dir"],
            "slc_count": slc_count,
            "manifest_path": str(task_dir / "landsar_sbas_db_import_manifest.json"),
            "error": error,
        }

    def _has_completed_import(self, output_dir: str) -> bool:
        if not output_dir or not os.path.isdir(output_dir):
            return False
        log_candidates = [
            os.path.join(output_dir, f"{IMPORT_PROID}.log"),
            os.path.join(output_dir, f"{IMPORT_PROID}_timeseries_console.log"),
            os.path.join(output_dir, f"{IMPORT_PROID}_console.log"),
            *[str(path) for path in Path(output_dir).glob(f"*{IMPORT_PROID}*.log")],
        ]
        for log_path in log_candidates:
            if not os.path.isfile(log_path):
                continue
            try:
                content = Path(log_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lowered = content.lower()
            if "console success" in lowered:
                return True
            if "lt-1" in lowered and _SUCCESS_RE.search(content):
                return True
            if "数据导入" in content and _SUCCESS_RE.search(content):
                return True
        return False

    def _resolve_import_scene_dir(self, scene: dict[str, Any]) -> str:
        for key in (
            "scene_dir",
            "scene_dir_windows",
            "file_path",
            "tiff_windows",
            "meta_windows",
            "source_dir",
        ):
            raw_value = scene.get(key)
            if not raw_value:
                continue
            try:
                resolved = self.resolve_lt1_scene_dir(raw_value)
            except Exception:
                resolved = ""
            if resolved:
                return resolved
        return ""

    def _normalize_stack_scenes_for_import(self, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen_dirs: set[str] = set()
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            scene_dir = self._resolve_import_scene_dir(scene)
            if not scene_dir or scene_dir in seen_dirs:
                continue
            seen_dirs.add(scene_dir)
            normalized.append(
                {
                    **scene,
                    "scene_dir": scene_dir,
                    "file_path": scene.get("file_path") or scene_dir,
                    "unique_id": scene.get("unique_id") or scene.get("scene_name"),
                }
            )
        normalized.sort(key=lambda item: str(item.get("date") or item.get("imaging_date") or ""))
        return normalized

    @staticmethod
    def _landsar_stack_candidate_score(candidate: dict[str, Any]) -> tuple[int, float, float, int, str]:
        common_overlap = float(candidate.get("common_overlap_ratio") or 0.0)
        aoi_overlap = float(candidate.get("aoi_overlap_ratio_mean") or 0.0)
        usable_count = int(candidate.get("usable_scene_count") or candidate.get("scene_count") or 0)
        temporal_gap = int(candidate.get("max_temporal_gap_days") or 9999)
        date_start = str(candidate.get("date_start") or "")
        return (usable_count, common_overlap, aoi_overlap, -temporal_gap, date_start)

    @staticmethod
    def _stack_selection_manifest(selection: dict[str, Any]) -> dict[str, Any]:
        ranked = []
        for item in list(selection.get("ranked_candidates") or [])[:10]:
            ranked.append(
                {
                    "stack_id": item.get("stack_id"),
                    "status": item.get("status"),
                    "satellite": item.get("satellite"),
                    "relative_orbit": item.get("relative_orbit"),
                    "orbit_direction": item.get("orbit_direction"),
                    "scene_count": item.get("scene_count"),
                    "usable_scene_count": item.get("usable_scene_count"),
                    "date_start": item.get("date_start"),
                    "date_end": item.get("date_end"),
                    "common_overlap_ratio": item.get("common_overlap_ratio"),
                    "aoi_overlap_ratio_mean": item.get("aoi_overlap_ratio_mean"),
                    "max_temporal_gap_days": item.get("max_temporal_gap_days"),
                }
            )
        selected = selection.get("selected_stack") or {}
        return {
            "schema": selection.get("schema"),
            "generated_at": selection.get("generated_at"),
            "selection_strategy": selection.get("selection_strategy"),
            "selected_stack_id": selection.get("selected_stack_id"),
            "selected_stack": ranked[0] if ranked else {
                "stack_id": selected.get("stack_id"),
                "status": selected.get("status"),
            },
            "candidate_count": selection.get("candidate_count"),
            "viable_count": selection.get("viable_count"),
            "requested_limit": selection.get("requested_limit"),
            "discovery_limit": selection.get("discovery_limit"),
            "ranked_candidates": ranked,
            "discovery_snapshot_path": selection.get("discovery_snapshot_path"),
            "admin_region": selection.get("admin_region"),
            "aoi": selection.get("aoi"),
            "warnings": selection.get("warnings") or [],
        }

    def _ensure_tasks_for_execution(
        self,
        *,
        run_dir: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        tasks = list(manifest.get("tasks") or [])
        if tasks:
            return manifest, tasks

        source_stack = manifest.get("source_stack") or {}
        scenes = self._normalize_stack_scenes_for_import(source_stack.get("scenes") or [])
        if not scenes:
            raise ValueError("LandSAR SBAS run has no selected tasks or source stack scenes.")

        input_import = dict(manifest.get("input_import") or {})
        manifest["status"] = "LANDSAR_SBAS_INPUT_IMPORTING"
        manifest["next_stage"] = "import_landsar_input"
        input_import["status"] = "RUNNING"
        input_import["started_at"] = _utc_text()
        manifest["input_import"] = input_import
        _write_json(manifest_path, manifest)

        try:
            result = self.import_stack_scenes(
                scenes=scenes,
                dest_root=input_import.get("dest_root") or manifest.get("input_task_root"),
                task_name=input_import.get("task_name"),
                min_scenes=manifest.get("min_scenes"),
                sat_mode=input_import.get("sat_mode") or "MONO",
                overwrite=bool(input_import.get("overwrite")),
                timeout_seconds=manifest.get("import_timeout_seconds") or manifest.get("timeout_seconds"),
                progress_callback=progress_callback,
            )
        except Exception as exc:
            input_import.update(
                {
                    "status": "FAILED",
                    "ended_at": _utc_text(),
                    "error": str(exc),
                }
            )
            manifest["status"] = "LANDSAR_SBAS_IMPORT_FAILED"
            manifest["next_stage"] = "inspect_landsar_import_logs"
            manifest["input_import"] = input_import
            _write_json(manifest_path, manifest)
            raise

        task = result.get("task") or {}
        tasks = [task] if task else []
        dates = [date for date in (task.get("dates") or []) if date]
        input_import.update(
            {
                "status": result.get("status") or "LANDSAR_SBAS_INPUT_READY",
                "ready": bool(result.get("ready")),
                "ended_at": _utc_text(),
                "result": result,
                "manifest_path": result.get("manifest_path"),
                "task_dir": result.get("task_dir"),
                "input_data_dir": result.get("input_data_dir"),
                "slc_count": result.get("slc_count"),
            }
        )
        manifest.update(
            {
                "status": "LANDSAR_SBAS_QUEUED",
                "next_stage": "execute_landsar_sbas",
                "root_dir": result.get("task_dir") or manifest.get("root_dir"),
                "tasks": tasks,
                "task_count": len(tasks),
                "scene_count": int(result.get("slc_count") or len(scenes)),
                "dates": dates or manifest.get("dates") or [],
                "date_start": (dates or manifest.get("dates") or [None])[0],
                "date_end": (dates or manifest.get("dates") or [None])[-1],
                "input_import": input_import,
            }
        )
        _write_json(manifest_path, manifest)
        _write_json(
            run_dir / "stack_manifest.json",
            {
                "schema": "insar.landsar-sbas-stack/v1",
                "run_id": manifest.get("run_id"),
                "status": "READY_FOR_LANDSAR_SBAS",
                "source": "sbas_stack_selection_import",
                "stack_id": source_stack.get("stack_id"),
                "task": task,
                "tasks": tasks,
                "dates": dates or manifest.get("dates") or [],
                "scene_count": manifest.get("scene_count"),
                "source_scenes": scenes,
            },
        )
        return manifest, tasks

    def create_run(
        self,
        *,
        root_dir: str,
        run_label: str | None = None,
        num_to_process: int = 0,
        min_scenes: int | None = None,
        rerun_mode: str = "rerun_all",
        timeout_seconds: int | None = None,
        extra: Optional[dict[str, Any]] = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        if not bool(settings.LANDSAR_SBAS_ENABLED):
            raise ValueError("LandSAR SBAS is disabled.")
        params = self.normalize_params(extra or {})
        dem_path = _norm_path((extra or {}).get("dem_path") or self.default_dem_path)
        if not dem_path or not os.path.isfile(dem_path):
            raise ValueError(f"LandSAR SBAS DEM file is missing: {dem_path or '<empty>'}")
        validation = self.validate_root_dir(
            root_dir,
            min_scenes=min_scenes,
            num_to_process=num_to_process,
            rerun_mode=rerun_mode,
        )
        tasks = list(validation.get("items") or [])
        if not tasks:
            raise ValueError("No valid LandSAR SBAS Task_* directories selected.")

        created_at = datetime.utcnow()
        run_id = f"landsar_sbas_{created_at.strftime('%Y%m%dT%H%M%S%fZ')}_{_safe_name(tasks[0].get('task_name'), 'task')}"
        run_dir = Path(self.get_run_root()) / run_id
        work_run_root = self._allocate_work_run_root(run_id, created_at)
        native_root = work_run_root / "n"
        publish_root = run_dir / "publish" / "landsar"
        native_root.mkdir(parents=True, exist_ok=True)
        publish_root.mkdir(parents=True, exist_ok=True)

        all_dates = sorted({date for task in tasks for date in (task.get("dates") or []) if date})
        scene_count = sum(int(task.get("slc_count") or 0) for task in tasks)
        manifest = {
            "schema": "insar.landsar-sbas-run/v1",
            "run_id": run_id,
            "run_label": run_label or f"LandSAR SBAS {tasks[0].get('task_name')}",
            "workflow_code": WORKFLOW_CODE,
            "processor_code": PROCESSOR_CODE,
            "profile_code": PROFILE_CODE,
            "engine_code": ENGINE_CODE,
            "proid": SBAS_PROID,
            "process_name": SBAS_PROCESS_NAME,
            "execution_mode": "landsar_console_sbas_process",
            "status": "LANDSAR_SBAS_QUEUED",
            "created_at": _utc_text(created_at),
            "created_by": created_by,
            "root_dir": _norm_path(root_dir),
            "run_dir": str(run_dir),
            "work_root": str(work_run_root),
            "work_root_strategy": "short_landsar_execution_path",
            "native_root": str(native_root),
            "publish_root": str(publish_root),
            "dem_path": dem_path,
            "params": params,
            "timeout_seconds": max(60, int(timeout_seconds or settings.LANDSAR_SBAS_TIMEOUT_SECONDS or 172800)),
            "min_scenes": validation.get("min_scenes"),
            "scene_count": scene_count,
            "task_count": len(tasks),
            "pair_count": None,
            "date_start": all_dates[0] if all_dates else None,
            "date_end": all_dates[-1] if all_dates else None,
            "dates": all_dates,
            "tasks": tasks,
            "next_stage": "execute_landsar_sbas",
        }
        _write_json(run_dir / "run_manifest.json", manifest)
        _write_json(
            run_dir / "stack_manifest.json",
            {
                "schema": "insar.landsar-sbas-stack/v1",
                "run_id": run_id,
                "status": "READY_FOR_LANDSAR_SBAS",
                "source": "Task_*/Input_Data",
                "tasks": tasks,
                "dates": all_dates,
                "scene_count": scene_count,
            },
        )
        _write_json(
            run_dir / "workflow_summary.json",
            {
                "schema": "insar.landsar-sbas-workflow-summary/v1",
                "run_id": run_id,
                "process_name": SBAS_PROCESS_NAME,
                "ready": False,
                "status": "QUEUED",
                "task_count": len(tasks),
                "completed_count": 0,
                "failed_count": 0,
            },
        )
        return self.get_run_detail(run_id)

    def list_runs(self) -> dict[str, Any]:
        run_root = Path(self.get_run_root())
        items: list[dict[str, Any]] = []
        for manifest_path in sorted(run_root.glob("*/run_manifest.json")):
            try:
                manifest = _read_json(manifest_path)
                items.append(self._build_run_card(manifest_path.parent, manifest))
            except Exception as exc:
                items.append({"run_id": manifest_path.parent.name, "status": "RUN_MANIFEST_UNREADABLE", "error": str(exc)})
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"items": items, "count": len(items), "run_root": str(run_root)}

    def get_run_detail(self, run_id: str) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest = _read_json(run_dir / "run_manifest.json")
        return {
            "run": self._build_run_card(run_dir, manifest),
            "manifest": manifest,
            "command_manifest": self._read_optional_json(run_dir / "landsar_command_manifest.json"),
            "workflow_manifest": self._read_optional_json(run_dir / "workflow_summary.json"),
            "workflow_state": self._read_optional_json(run_dir / "workflow_summary.json"),
            "geographic_coverage": manifest.get("geographic_coverage") or self._build_geographic_coverage(run_dir),
            "artifacts": self._build_run_artifacts(run_dir),
        }

    @staticmethod
    def _classify_sbas_runtime_failure(stdout_text: str, returncode: int) -> tuple[str, str]:
        if _UNSUPPORTED_PROID_RE.search(stdout_text or ""):
            return (
                "unsupported_proid",
                (
                    f"LandSAR InSAR_Console does not support SBAS proID {SBAS_PROID}. "
                    f"Input import succeeded, but this LandSAR installation did not accept process '{SBAS_PROCESS_NAME}'."
                ),
            )
        return "console_failure", _summarize_landsar_failure(stdout_text, "LandSAR SBAS", returncode)

    def execute_run(
        self,
        run_id: str,
        *,
        timeout_seconds: int | None = None,
        progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> dict[str, Any]:
        run_dir = self._resolve_run_dir(run_id)
        manifest_path = run_dir / "run_manifest.json"
        manifest = _read_json(manifest_path)
        availability = self.check_available()
        if not availability["available"]:
            raise ValueError(f"LandSAR SBAS is not available: {availability['message']}")

        console_path = self._engine._console_exe
        home = self._engine._home
        config_ok, config_detail = self._engine._ensure_config_csv()
        if not config_ok:
            raise ValueError(f"LandSAR config.csv is not ready: {config_detail}")
        auth_ok, auth_detail = self._engine._start_auth_server_if_needed()
        if not auth_ok:
            raise ValueError(f"LandSAR network license server is not ready: {auth_detail}")

        timeout = max(60, int(timeout_seconds or manifest.get("timeout_seconds") or settings.LANDSAR_SBAS_TIMEOUT_SECONDS or 172800))
        manifest, tasks = self._ensure_tasks_for_execution(
            run_dir=run_dir,
            manifest_path=manifest_path,
            manifest=manifest,
            progress_callback=progress_callback,
        )
        if not tasks:
            raise ValueError("LandSAR SBAS run has no selected tasks.")

        started_at = _utc_text()
        manifest["status"] = "LANDSAR_SBAS_RUNNING"
        manifest["started_at"] = started_at
        manifest["next_stage"] = "execute_landsar_sbas"
        _write_json(manifest_path, manifest)

        task_results: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        skipped_count = 0
        primary_published = False

        for index, task in enumerate(tasks, start=1):
            task_name = str(task.get("task_name") or f"Task_{index}")
            task_alias = _safe_name(task_name, f"task_{index}")
            input_dir = _norm_path(task.get("input_data_dir") or os.path.join(str(task.get("task_dir") or ""), "Input_Data"))
            native_root = Path(str(manifest.get("native_root") or run_dir / "native"))
            native_output_dir = native_root / task_alias / "Output_Data"
            native_output_dir.mkdir(parents=True, exist_ok=True)
            project_name = str((manifest.get("params") or {}).get("project_name") or task_alias).strip() or task_alias
            param_file = _generate_sbas_param_file(
                str(native_output_dir / f"{SBAS_PROID}.txt"),
                slc_folder=input_dir,
                dem_path=str(manifest.get("dem_path") or ""),
                output_dir=str(native_output_dir),
                project_name=project_name,
                params=dict(manifest.get("params") or {}),
            )
            command = [console_path, param_file]
            self._emit(progress_callback, "INFO", f"[{index}/{len(tasks)}] LandSAR SBAS {task_name} started")
            rc, stdout_text, timed_out = self._run_console(
                command,
                cwd=home if os.path.isdir(home) else os.path.dirname(console_path),
                log_path=str(native_output_dir / f"{SBAS_PROID}_console.log"),
                timeout=timeout,
                progress_callback=progress_callback,
                task_name=task_name,
            )
            log_publish_result = self._copy_native_logs(
                run_dir=run_dir,
                task_alias=task_alias,
                native_output_dir=native_output_dir,
            )
            success = rc == 0 and self._has_completed_output(str(native_output_dir))
            error = ""
            failure_kind = ""
            if timed_out:
                error = f"LandSAR SBAS timed out after {timeout}s."
                failure_kind = "timeout"
                success = False
            elif rc != 0:
                failure_kind, error = self._classify_sbas_runtime_failure(stdout_text, rc)
            elif not success:
                error = "LandSAR SBAS success marker or core output is missing."
                failure_kind = "missing_outputs"

            publish_result: dict[str, Any] = {}
            if success:
                success_count += 1
                publish_result = self._publish_task_outputs(
                    run_dir=run_dir,
                    task_alias=task_alias,
                    native_output_dir=native_output_dir,
                    make_primary=not primary_published,
                )
                primary_published = primary_published or bool(publish_result.get("primary_published"))
                self._emit(progress_callback, "INFO", f"[{index}/{len(tasks)}] LandSAR SBAS {task_name} completed")
            else:
                failed_count += 1
                self._emit(progress_callback, "ERROR", f"[{index}/{len(tasks)}] LandSAR SBAS {task_name} failed: {error}")

            task_results.append(
                {
                    "task_name": task_name,
                    "task_alias": task_alias,
                    "input_data_dir": input_dir,
                    "native_output_dir": str(native_output_dir),
                    "param_file": param_file,
                    "process_name": SBAS_PROCESS_NAME,
                    "command": " ".join(command),
                    "returncode": rc,
                    "success": success,
                    "timed_out": timed_out,
                    "failure_kind": failure_kind,
                    "error": error,
                    "stdout_tail": _collect_tail(stdout_text, 4000),
                    "native_logs": log_publish_result,
                    "publish": publish_result,
                }
            )

        unsupported_count = sum(1 for item in task_results if item.get("failure_kind") == "unsupported_proid")
        if success_count > 0 and failed_count == 0:
            status = "LANDSAR_SBAS_COMPLETED"
            next_stage = "review_landsar_products"
        elif success_count > 0:
            status = "LANDSAR_SBAS_PARTIAL"
            next_stage = "review_landsar_products"
        elif failed_count > 0 and unsupported_count == failed_count:
            status = "LANDSAR_SBAS_RUNTIME_UNSUPPORTED"
            next_stage = "configure_landsar_sbas_runtime"
        else:
            status = "LANDSAR_SBAS_FAILED"
            next_stage = "inspect_landsar_logs"

        coverage = self._build_geographic_coverage(run_dir)
        quality_summary = self._build_quality_summary(run_dir)
        workflow_summary = {
            "schema": "insar.landsar-sbas-workflow-summary/v1",
            "run_id": run_id,
            "process_name": SBAS_PROCESS_NAME,
            "status": status,
            "ready": status in {"LANDSAR_SBAS_COMPLETED", "LANDSAR_SBAS_PARTIAL"},
            "started_at": started_at,
            "ended_at": _utc_text(),
            "task_count": len(tasks),
            "completed_count": success_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "unsupported_proid_count": unsupported_count,
            "task_results": task_results,
        }
        product_summary = {
            "schema": "insar.landsar-sbas-product-summary/v1",
            "run_id": run_id,
            "processor_code": PROCESSOR_CODE,
            "engine_code": ENGINE_CODE,
            "proid": SBAS_PROID,
            "process_name": SBAS_PROCESS_NAME,
            "default_los_product": "los_timeseries",
            "los_sign_convention": "LandSAR LOS output; sign and rate/cumulative semantics require algorithm confirmation.",
            "output_semantics_note": "Do not label LandSAR *.los.tif as annual velocity until verified by the algorithm owner.",
            "task_count": len(tasks),
            "completed_task_count": success_count,
            "unsupported_proid_count": unsupported_count,
            "primary_asset": "publish/landsar/los_timeseries.tif" if (run_dir / "publish" / "landsar" / "los_timeseries.tif").is_file() else None,
        }
        command_manifest = {
            "schema": "insar.landsar-sbas-command-manifest/v1",
            "run_id": run_id,
            "engine": ENGINE_CODE,
            "processor_code": PROCESSOR_CODE,
            "proid": SBAS_PROID,
            "process_name": SBAS_PROCESS_NAME,
            "console_path": console_path,
            "home": home,
            "work_root": manifest.get("work_root"),
            "work_root_strategy": manifest.get("work_root_strategy"),
            "timeout_seconds": timeout,
            "params": manifest.get("params") or {},
            "availability": availability,
            "expected_outputs": [
                "publish/landsar/los_timeseries.tif",
                "publish/landsar/post_raster.tif",
                "publish/landsar/preview.png",
            ],
        }
        _write_json(run_dir / "workflow_summary.json", workflow_summary)
        _write_json(run_dir / "product_summary.json", product_summary)
        _write_json(run_dir / "quality_summary.json", quality_summary)
        _write_json(run_dir / "landsar_command_manifest.json", command_manifest)

        manifest.update(
            {
                "status": status,
                "ended_at": workflow_summary["ended_at"],
                "next_stage": next_stage,
                "task_results": task_results,
                "completed_task_count": success_count,
                "failed_task_count": failed_count,
                "geographic_coverage": coverage,
                "workflow": {"status": status, "summary": workflow_summary},
                "product_summary_path": str(run_dir / "product_summary.json"),
            }
        )
        _write_json(manifest_path, manifest)
        return self.get_run_detail(run_id)

    def resolve_artifact(self, run_id: str, relative_path: str) -> Path:
        run_dir = self._resolve_run_dir(run_id)
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        target = (run_dir / normalized).resolve()
        root = run_dir.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("artifact path escapes run root") from exc
        if not target.is_file():
            raise FileNotFoundError(f"artifact not found: {normalized}")
        return target

    def _run_console(
        self,
        command: list[str],
        *,
        cwd: str,
        log_path: str,
        timeout: int,
        progress_callback: Optional[Callable[[dict[str, Any]], None]],
        task_name: str,
    ) -> tuple[int, str, bool]:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8", errors="replace") as log_fp:
            log_fp.write(f"\n[{_utc_text()}] command: {' '.join(command)}\n")
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=_landsar_process_env(command[0], self._engine._home),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output_lines: list[str] = []
            line_queue: queue.Queue[Any] = queue.Queue()
            sentinel = object()

            def _reader() -> None:
                try:
                    if process.stdout is None:
                        return
                    for raw_line in iter(process.stdout.readline, b""):
                        line_queue.put(raw_line)
                finally:
                    line_queue.put(sentinel)

            reader = threading.Thread(target=_reader, name="landsar-sbas-console-reader", daemon=True)
            reader.start()
            started = time.monotonic()
            timed_out = False
            stdout_closed = False
            while True:
                try:
                    raw_item = line_queue.get(timeout=1)
                except queue.Empty:
                    raw_item = None
                if raw_item is sentinel:
                    stdout_closed = True
                elif raw_item:
                    line = _decode_line(raw_item)
                    output_lines.append(line)
                    log_fp.write(line + "\n")
                    log_fp.flush()
                    if line.strip():
                        self._emit(progress_callback, "INFO", f"{task_name}: {line.strip()}")
                if process.poll() is not None and stdout_closed:
                    break
                if time.monotonic() - started > timeout:
                    timed_out = True
                    process.kill()
                    break
            if timed_out:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            reader.join(timeout=5)
            while True:
                try:
                    raw_item = line_queue.get_nowait()
                except queue.Empty:
                    break
                if raw_item is sentinel or not raw_item:
                    continue
                line = _decode_line(raw_item)
                output_lines.append(line)
                log_fp.write(line + "\n")
            if process.poll() is None:
                timed_out = True
                process.kill()
            rc = int(process.poll() if process.poll() is not None else -9)
            if timed_out:
                log_fp.write(f"[{_utc_text()}] timeout after {timeout}s\n")
            log_fp.write(f"[{_utc_text()}] returncode={rc}\n")
        return rc, "\n".join(output_lines), timed_out

    def _has_completed_output(self, output_dir: str) -> bool:
        if not output_dir or not os.path.isdir(output_dir):
            return False
        if not (self._select_los_file(output_dir) or self._select_raster_file(output_dir)):
            return False
        log_candidates = [
            os.path.join(output_dir, f"{SBAS_PROID}.log"),
            os.path.join(output_dir, f"{SBAS_PROID}_console.log"),
            *[str(path) for path in Path(output_dir).glob(f"*{SBAS_PROID}*.log")],
        ]
        for log_path in log_candidates:
            if not os.path.isfile(log_path):
                continue
            try:
                content = Path(log_path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lowered = content.lower()
            if "console success" in lowered:
                return True
            if "sbas" in lowered and _SUCCESS_RE.search(content):
                return True
        return False

    def _select_los_file(self, output_dir: str) -> str:
        return self._first_matching_file(output_dir, ["*.los.tif", "*.los.tiff", "*los*.tif", "*los*.tiff"])

    def _select_raster_file(self, output_dir: str) -> str:
        return self._first_matching_file(output_dir, ["*.raster.tif", "*.raster.tiff", "*raster*.tif", "*raster*.tiff"])

    @staticmethod
    def _first_matching_file(output_dir: str, patterns: list[str]) -> str:
        root = Path(output_dir)
        if not root.is_dir():
            return ""
        for pattern in patterns:
            for path in sorted(root.rglob(pattern), key=lambda item: str(item).lower()):
                if path.is_file():
                    return _norm_path(path)
        return ""

    def _copy_native_logs(self, *, run_dir: Path, task_alias: str, native_output_dir: Path) -> dict[str, Any]:
        native_logs = run_dir / "native_logs" / task_alias
        native_logs.mkdir(parents=True, exist_ok=True)
        copied: list[dict[str, str]] = []

        def copy_named(src: str, target: Path) -> str:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied.append({"source": src, "target": str(target)})
            return str(target)

        for log_path in native_output_dir.glob(f"*{SBAS_PROID}*.log"):
            if log_path.is_file():
                copy_named(str(log_path), native_logs / log_path.name)
        param_path = native_output_dir / f"{SBAS_PROID}.txt"
        if param_path.is_file():
            copy_named(str(param_path), native_logs / param_path.name)
        return {"native_logs_dir": str(native_logs), "copied": copied}

    def _publish_task_outputs(self, *, run_dir: Path, task_alias: str, native_output_dir: Path, make_primary: bool) -> dict[str, Any]:
        publish_dir = run_dir / "publish" / "landsar"
        task_publish_dir = publish_dir / task_alias
        native_logs = run_dir / "native_logs" / task_alias
        task_publish_dir.mkdir(parents=True, exist_ok=True)

        los_src = self._select_los_file(str(native_output_dir))
        raster_src = self._select_raster_file(str(native_output_dir))
        copied: list[dict[str, str]] = []

        def copy_named(src: str, target: Path) -> str:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            copied.append({"source": src, "target": str(target)})
            return str(target)

        task_los = copy_named(los_src, task_publish_dir / "los_timeseries.tif") if los_src else ""
        task_raster = copy_named(raster_src, task_publish_dir / "post_raster.tif") if raster_src else ""
        vector_dir = native_output_dir / "vector"
        if vector_dir.is_dir():
            shutil.copytree(vector_dir, task_publish_dir / "vector", dirs_exist_ok=True)

        primary_published = False
        preview_path = ""
        if make_primary:
            if task_los:
                copy_named(task_los, publish_dir / "los_timeseries.tif")
                primary_published = True
            if task_raster:
                copy_named(task_raster, publish_dir / "post_raster.tif")
                primary_published = True
            preview_source = task_los or task_raster
            if preview_source:
                preview_path = self._build_preview_png(preview_source, str(publish_dir / "preview.png")) or ""
                if preview_path:
                    copied.append({"source": preview_source, "target": preview_path})
        return {
            "task_publish_dir": str(task_publish_dir),
            "native_logs_dir": str(native_logs),
            "los_timeseries": task_los,
            "post_raster": task_raster,
            "preview": preview_path,
            "primary_published": primary_published,
            "copied": copied,
        }

    def _build_preview_png(self, source: str, target: str) -> str | None:
        try:
            import numpy as np
            import rasterio
            from PIL import Image
            from rasterio.enums import Resampling

            with rasterio.open(source) as src:
                scale = max(src.width / 1200, src.height / 1200, 1)
                out_w = max(1, int(src.width / scale))
                out_h = max(1, int(src.height / scale))
                data = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.bilinear).astype("float32")
                mask = src.dataset_mask(out_shape=(out_h, out_w)) > 0
                nodata = src.nodata
                if nodata is not None:
                    mask &= data != nodata
                finite = np.isfinite(data)
                mask &= finite
                if not np.any(mask):
                    return None
                values = data[mask]
                p2, p98 = np.nanpercentile(values, [2, 98])
                if not np.isfinite(p2) or not np.isfinite(p98) or p98 <= p2:
                    p2 = float(np.nanmin(values))
                    p98 = float(np.nanmax(values))
                if p98 <= p2:
                    norm = np.zeros_like(data, dtype="float32")
                else:
                    norm = np.clip((data - p2) / (p98 - p2), 0, 1)
                gray = (norm * 255).astype("uint8")
                rgba = np.dstack([gray, gray, gray, np.where(mask, 255, 0).astype("uint8")])
                image = Image.fromarray(rgba, mode="RGBA")
                image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
                out_path = Path(target)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(out_path)
                return str(out_path)
        except Exception:
            return None

    def _build_quality_summary(self, run_dir: Path) -> dict[str, Any]:
        primary = run_dir / "publish" / "landsar" / "los_timeseries.tif"
        stats = self._raster_stats(primary) if primary.is_file() else {}
        return {
            "schema": "insar.landsar-sbas-quality-summary/v1",
            "generated_at": _utc_text(),
            "primary_geotiff": stats,
        }

    @staticmethod
    def _raster_stats(path: Path) -> dict[str, Any]:
        try:
            import numpy as np
            import rasterio
            from rasterio.enums import Resampling

            with rasterio.open(path) as src:
                scale = max(src.width / 2048, src.height / 2048, 1)
                out_w = max(1, int(src.width / scale))
                out_h = max(1, int(src.height / scale))
                data = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.nearest).astype("float64")
                mask = src.dataset_mask(out_shape=(out_h, out_w)) > 0
                if src.nodata is not None:
                    mask &= data != src.nodata
                values = data[mask & np.isfinite(data)]
                if values.size == 0:
                    return {"exists": True, "valid_count": 0}
                return {
                    "exists": True,
                    "sampled": scale > 1,
                    "width": src.width,
                    "height": src.height,
                    "crs": str(src.crs) if src.crs else None,
                    "valid_count": int(values.size),
                    "min": float(np.nanmin(values)),
                    "p05": float(np.nanpercentile(values, 5)),
                    "median": float(np.nanmedian(values)),
                    "p95": float(np.nanpercentile(values, 95)),
                    "max": float(np.nanmax(values)),
                    "mean": float(np.nanmean(values)),
                    "std": float(np.nanstd(values)),
                }
        except Exception as exc:
            return {"exists": path.is_file(), "error": str(exc)}

    def _build_geographic_coverage(self, run_dir: Path) -> dict[str, Any]:
        primary = run_dir / "publish" / "landsar" / "los_timeseries.tif"
        if not primary.is_file():
            primary = run_dir / "publish" / "landsar" / "post_raster.tif"
        bbox = None
        crs = None
        try:
            import rasterio
            from rasterio.warp import transform_bounds

            with rasterio.open(primary) as src:
                crs = str(src.crs) if src.crs else None
                bounds = src.bounds
                if src.crs:
                    west, south, east, north = transform_bounds(src.crs, "EPSG:4326", *bounds, densify_pts=21)
                else:
                    west, south, east, north = bounds.left, bounds.bottom, bounds.right, bounds.top
                bbox = {
                    "min_lon": float(west),
                    "min_lat": float(south),
                    "max_lon": float(east),
                    "max_lat": float(north),
                }
        except Exception:
            bbox = None
        center = None
        if bbox:
            center = {
                "lon": (bbox["min_lon"] + bbox["max_lon"]) / 2,
                "lat": (bbox["min_lat"] + bbox["max_lat"]) / 2,
            }
        return {
            "schema": "insar.landsar-sbas-geographic-coverage/v1",
            "bbox": bbox,
            "bbox_intersection": bbox,
            "center": center,
            "crs": crs,
            "source": str(primary) if primary.is_file() else None,
        }

    def _build_run_card(self, run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        coverage = manifest.get("geographic_coverage") or self._build_geographic_coverage(run_dir)
        center = (coverage or {}).get("center")
        return {
            "run_id": manifest.get("run_id") or run_dir.name,
            "run_label": manifest.get("run_label"),
            "status": manifest.get("status") or "UNKNOWN",
            "created_at": manifest.get("created_at"),
            "workflow_code": manifest.get("workflow_code") or WORKFLOW_CODE,
            "processor_code": manifest.get("processor_code") or PROCESSOR_CODE,
            "engine_code": manifest.get("engine_code") or ENGINE_CODE,
            "sensor_family": "LT1",
            "profile_code": manifest.get("profile_code") or PROFILE_CODE,
            "execution_enabled": True,
            "stack_id": manifest.get("stack_id") or manifest.get("run_id"),
            "scene_count": manifest.get("scene_count"),
            "pair_count": manifest.get("pair_count"),
            "task_count": manifest.get("task_count"),
            "next_stage": manifest.get("next_stage"),
            "platform": "LT1",
            "reference_date": manifest.get("date_start"),
            "date_start": manifest.get("date_start"),
            "date_end": manifest.get("date_end"),
            "center": center,
            "run_dir": str(run_dir),
        }

    def _build_run_artifacts(self, run_dir: Path) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(run_dir)).replace("\\", "/")
            if path.stat().st_size <= 0:
                continue
            role = "artifact"
            if rel == "run_manifest.json":
                role = "run_manifest"
            elif rel.endswith("_console.log") or rel.endswith(".log"):
                role = "native_log"
            elif rel.endswith(f"{SBAS_PROID}.txt"):
                role = "parameter_file"
            elif rel.endswith(".tif") or rel.endswith(".tiff"):
                role = "primary_geotiff" if rel == "publish/landsar/los_timeseries.tif" else "geotiff"
            elif rel.endswith(".png"):
                role = "primary_preview"
            artifacts.append(
                {
                    "key": _safe_name(Path(rel).stem),
                    "label": rel,
                    "role": role,
                    "relative_path": rel,
                    "size_bytes": path.stat().st_size,
                }
            )
        return artifacts

    def _resolve_run_dir(self, run_id: str) -> Path:
        clean_id = str(run_id or "").strip()
        if not clean_id or Path(clean_id).name != clean_id:
            raise ValueError("invalid LandSAR SBAS run id")
        run_dir = (Path(self.get_run_root()) / clean_id).resolve()
        root = Path(self.get_run_root()).resolve()
        try:
            run_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError("run id escapes LandSAR SBAS run root") from exc
        if not run_dir.is_dir():
            raise FileNotFoundError(f"LandSAR SBAS run not found: {clean_id}")
        return run_dir

    @staticmethod
    def _read_optional_json(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        return _read_json(path)

    @staticmethod
    def _emit(callback: Optional[Callable[[dict[str, Any]], None]], level: str, message: str) -> None:
        if not callable(callback):
            return
        try:
            callback({"level": level, "message": message, "timestamp": _utc_text()})
        except Exception:
            return


landsar_sbas_service = LandsarSbasService()
