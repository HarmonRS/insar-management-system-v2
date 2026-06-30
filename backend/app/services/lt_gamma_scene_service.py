"""LT single-scene Gamma preprocessing service."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..config import settings
from ..models import RadarDataORM, SARSceneGeoORM
from .pyint_service import (
    DEFAULT_AZIMUTH_LOOKS,
    DEFAULT_RANGE_LOOKS,
    quote_shell,
    resolve_gamma_env_script,
    to_wsl_path,
)
from .wsl_service import run_wsl_exec


def _safe_token(value: Any, *, default: str = "scene") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", text).strip("._-")
    return text or default


def _scene_date(radar: RadarDataORM) -> str:
    for value in (radar.imaging_date, radar.file_path, radar.unique_id):
        text = str(value or "")
        match = re.search(r"(20\d{6})", re.sub(r"\D", "", text))
        if match:
            return match.group(1)
        match = re.search(r"(20\d{6})", text)
        if match:
            return match.group(1)
    raise ValueError(f"Cannot infer LT scene date for radar_data id={radar.id}")


def _prepared_dem_path() -> str:
    if str(settings.PYINT_DEM_MODE or "").strip().lower() == "prepared_file":
        return (
            settings.PYINT_PREPARED_DEM_PATH
            or settings.ISCE2_DEM_PATH
            or settings.IDL_DINSAR_DEM_BASE_FILE
            or ""
        )
    return ""


def _build_shell_command(parts: list[str]) -> str:
    return " ".join(quote_shell(part) for part in parts)


def _runner_script() -> Path:
    return Path(settings.PROJECT_ROOT) / "backend" / "app" / "pyint_pipeline" / "run_gamma_scene_preprocess.py"


def run_lt_gamma_scene_preprocess(
    *,
    radar: RadarDataORM,
    scene: SARSceneGeoORM,
    job_id: str | None = None,
) -> dict[str, Any]:
    if not settings.PYINT_ENABLED:
        raise RuntimeError("PYINT_ENABLED=false; LT Gamma scene preprocessing is disabled")
    if not radar.file_path:
        raise ValueError(f"RadarDataORM id={radar.id} has no file_path")

    date = _scene_date(radar)
    token = _safe_token(radar.unique_id or Path(str(radar.file_path)).stem or f"radar_{radar.id}")
    run_name = _safe_token(f"lt_{date}_{token}_scene_{scene.id}_{job_id or 'manual'}")
    work_dir = Path(settings.SAR_ANALYSIS_WORK_ROOT) / "lt_gamma" / run_name
    output_dir = work_dir / "output"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    pyint_home = settings.PYINT_HOME
    if not pyint_home:
        raise RuntimeError("PYINT_HOME is not configured")
    pyint_python = settings.PYINT_WSL_PYTHON or settings.WSL_SHARED_PYTHON
    if not pyint_python:
        raise RuntimeError("PYINT_WSL_PYTHON is not configured")

    runner = _runner_script()
    if not runner.is_file():
        raise FileNotFoundError(f"Gamma scene runner not found: {runner}")

    analysis_dem_path = (
        settings.SAR_ANALYSIS_DEM_PATH
        or settings.GAMMA_SBAS_DEM_PATH
        or settings.PYINT_PREPARED_DEM_PATH
        or _prepared_dem_path()
    )
    if not analysis_dem_path:
        raise RuntimeError("SAR_ANALYSIS_DEM_PATH is not configured for LT analysis GeoTIFF production")

    args = [
        pyint_python,
        to_wsl_path(str(runner)),
        "--source-path",
        to_wsl_path(str(radar.file_path)),
        "--output-dir",
        to_wsl_path(str(output_dir)),
        "--work-dir",
        to_wsl_path(str(work_dir)),
        "--pyint-home",
        to_wsl_path(str(pyint_home)),
        "--dem-root",
        to_wsl_path(str(settings.PYINT_DEM_ROOT)),
        "--prepared-dem-path",
        to_wsl_path(str(analysis_dem_path)),
        "--dem-resolution-m",
        str(float(settings.SAR_ANALYSIS_DEM_RESOLUTION_M or 30.0)),
        "--target-grid-size-m",
        str(float(settings.SAR_ANALYSIS_TARGET_GRID_SIZE_M or 30.0)),
        "--project-name",
        run_name,
        "--date",
        date,
        "--satellite-family",
        "LT1",
        "--range-looks",
        str(int(settings.SAR_ANALYSIS_RANGE_LOOKS or DEFAULT_RANGE_LOOKS)),
        "--azimuth-looks",
        str(int(settings.SAR_ANALYSIS_AZIMUTH_LOOKS or DEFAULT_AZIMUTH_LOOKS)),
        "--speckle-filter-method",
        str(settings.SAR_ANALYSIS_SPECKLE_FILTER_METHOD or "none"),
        "--speckle-filter-size",
        str(int(settings.SAR_ANALYSIS_SPECKLE_FILTER_SIZE or 5)),
        "--geo-interp",
        str(settings.PYINT_GEO_INTERP or "1"),
        "--nodata-value",
        str(float(settings.SAR_ANALYSIS_NODATA_VALUE)),
        "--to-db",
    ]

    gamma_env_script = resolve_gamma_env_script(settings.PYINT_GAMMA_ENV_SCRIPT)
    prefix = ""
    if gamma_env_script:
        prefix = f". {quote_shell(to_wsl_path(gamma_env_script))} >/dev/null 2>&1 || exit 1; "
    command = prefix + f"export PYTHONPATH={quote_shell(to_wsl_path(str(pyint_home)))}:$PYTHONPATH; " + _build_shell_command(args)

    rc, stdout, stderr = run_wsl_exec(
        ["bash", "-lc", command],
        distro=settings.PYINT_WSL_DISTRO or settings.WSL_DISTRO,
        timeout=int(settings.PYINT_DEFAULT_TIMEOUT_SECONDS or 43200),
    )
    if rc != 0:
        detail = (stderr or stdout or "").strip()
        raise RuntimeError(f"LT Gamma scene preprocessing failed rc={rc}: {detail}")

    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"LT Gamma scene preprocessing produced no manifest: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Cannot read LT Gamma manifest: {manifest_path}: {exc}") from exc

    analysis_tif_path = manifest.get("analysis_tif_path")
    if analysis_tif_path and str(analysis_tif_path).startswith("/mnt/"):
        # The service registers the Windows-side path below.
        manifest["analysis_tif_path_wsl"] = analysis_tif_path
        manifest["analysis_tif_path"] = str(output_dir / "analysis_ready.tif")
    manifest["service"] = {
        "work_dir": str(work_dir),
        "output_dir": str(output_dir),
        "job_id": job_id,
        "stdout": stdout[-4000:] if stdout else "",
        "stderr": stderr[-4000:] if stderr else "",
    }
    return manifest
