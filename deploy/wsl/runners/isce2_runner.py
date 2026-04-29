from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _pipeline_script() -> Path:
    return _repo_root() / "backend" / "app" / "isce2_pipeline" / "run_lt1_dinsar_pipeline.py"


def _load_manifest(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require_text(container: Mapping[str, Any], key: str) -> str:
    value = str(container.get(key) or "").strip()
    if not value:
        raise ValueError(f"Manifest is missing required text field: {key}")
    return value


def _append_optional(argv: List[str], flag: str, value: Any) -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    argv.extend([flag, text])


def _build_pipeline_argv(payload: Mapping[str, Any], *, dry_run: bool = False) -> List[str]:
    paths = payload.get("paths") or {}
    params = payload.get("params") or {}
    pair_meta = payload.get("pair_meta") or {}

    argv: List[str] = [
        sys.executable,
        _pipeline_script().as_posix(),
        _require_text(paths, "task_dir_wsl"),
        "--task-name",
        _require_text(payload, "task_alias"),
        "--output-prefix",
        _require_text(payload, "task_alias"),
        "--work-dir",
        _require_text(paths, "work_dir_wsl"),
        "--output-dir",
        _require_text(paths, "output_dir_wsl"),
        "--orbit-output-dir",
        _require_text(paths, "orbit_output_dir_wsl"),
    ]

    _append_optional(argv, "--orbit-root", paths.get("orbit_root_wsl"))
    _append_optional(argv, "--dem", paths.get("dem_path_wsl"))
    if bool(params.get("force")):
        argv.append("--force")
    _append_optional(argv, "--target-grid-size-m", params.get("target_grid_size_m"))
    _append_optional(argv, "--bbox", params.get("bbox"))
    _append_optional(argv, "--coh-threshold", params.get("coh_threshold"))
    _append_optional(argv, "--reference-mode", params.get("reference_mode"))
    _append_optional(argv, "--reference-coh-threshold", params.get("reference_coh_threshold"))
    _append_optional(argv, "--deramp-mode", params.get("deramp_mode"))
    _append_optional(argv, "--deramp-coh-threshold", params.get("deramp_coh_threshold"))
    _append_optional(argv, "--bbox-margin", params.get("bbox_margin"))
    if not bool(params.get("ionosphere_correction", True)):
        argv.append("--no-ionosphere-correction")
    if bool(params.get("dense_offsets")):
        argv.append("--dense-offsets")
    if bool(params.get("rubbersheet_range")):
        argv.append("--rubbersheet-range")
    if bool(params.get("rubbersheet_azimuth")):
        argv.append("--rubbersheet-azimuth")
    _append_optional(argv, "--rubber-sheet-snr-threshold", params.get("rubber_sheet_snr_threshold"))
    _append_optional(argv, "--rubber-sheet-filter-size", params.get("rubber_sheet_filter_size"))
    _append_optional(argv, "--dense-window-width", params.get("dense_window_width"))
    _append_optional(argv, "--dense-window-height", params.get("dense_window_height"))
    _append_optional(argv, "--dense-search-width", params.get("dense_search_width"))
    _append_optional(argv, "--dense-search-height", params.get("dense_search_height"))
    _append_optional(argv, "--dense-skip-width", params.get("dense_skip_width"))
    _append_optional(argv, "--dense-skip-height", params.get("dense_skip_height"))
    _append_optional(argv, "--wavelength", params.get("wavelength"))
    _append_optional(argv, "--orbit-margin-sec", params.get("orbit_margin_sec"))
    _append_optional(argv, "--resume-from", params.get("resume_from"))
    _append_optional(argv, "--reference-satellite", pair_meta.get("master_satellite"))
    _append_optional(argv, "--secondary-satellite", pair_meta.get("slave_satellite"))
    if bool(params.get("full_geocode")):
        argv.append("--full-geocode")
    if dry_run:
        argv.append("--dry-run")
    return argv


def _build_child_env() -> Dict[str, str]:
    env = os.environ.copy()
    env_bin = Path(sys.executable).resolve().parent
    path_prefixes = [env_bin.as_posix()]
    isce_spec = importlib.util.find_spec("isce")
    if isce_spec and isce_spec.origin:
        isce_app_dir = Path(isce_spec.origin).resolve().parent / "applications"
        if isce_app_dir.exists():
            path_prefixes.append(isce_app_dir.as_posix())

    current_path = str(env.get("PATH") or "")
    env["PATH"] = ":".join(path_prefixes + ([current_path] if current_path else []))
    env_root = Path(sys.executable).resolve().parents[1]
    proj_data = env_root / "share" / "proj"
    if proj_data.exists():
        env["PROJ_DATA"] = proj_data.as_posix()
        env["PROJ_LIB"] = proj_data.as_posix()
    return env


def _find_python_module(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _validate_runtime_dependencies(payload: Mapping[str, Any], child_env: Mapping[str, str]) -> str:
    params = payload.get("params") or {}
    errors: List[str] = []
    ionosphere_correction = bool(params.get("ionosphere_correction", True))

    if ionosphere_correction:
        missing_ionosphere_modules: List[str] = []
        if not _find_python_module("cv2"):
            missing_ionosphere_modules.append("cv2")
        if not _find_python_module("scipy"):
            missing_ionosphere_modules.append("scipy")
        if missing_ionosphere_modules:
            errors.append(
                "Missing WSL Python dependencies for the ISCE2 stripmap ionosphere step: "
                + ", ".join(missing_ionosphere_modules)
                + ". The managed LT-1 workflow enables split-spectrum dispersive correction before geocode. "
                "Install the missing packages in insar_wsl_v1, for example: "
                "conda install -n insar_wsl_v1 -c conda-forge opencv scipy."
            )

    needs_rubbersheet = bool(params.get("rubbersheet_range")) or bool(
        params.get("rubbersheet_azimuth")
    )
    if needs_rubbersheet and not _find_python_module("astropy.convolution"):
        errors.append(
            "Missing WSL Python dependency 'astropy.convolution'. "
            "The managed LT-1 ISCE2 profile enables rubbersheeting, and ISCE2 "
            "imports astropy.convolution while running runRubbersheetRange.py. "
            "Install astropy in insar_wsl_v1 before running production."
        )

    if ionosphere_correction and not shutil.which("imageMath.py", path=str(child_env.get("PATH") or "")):
        errors.append(
            "Missing WSL CLI dependency 'imageMath.py' on PATH. "
            "The ISCE2 stripmap ionosphere step shells out to imageMath.py late in the run. "
            "Make sure the active conda env bin directory is exported into PATH before launching production."
        )

    return "\n".join(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="ISCE2 runtime V1 runner.")
    parser.add_argument("--manifest", required=True, help="WSL path to the staged job manifest.")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run through to the pipeline.")
    args = parser.parse_args()

    manifest = _load_manifest(args.manifest)
    payload = manifest.get("payload") or {}
    runner_summary = {
        "runner": "isce2_runtime_v1",
        "manifest": args.manifest,
        "job_id": manifest.get("job_id"),
        "operation": manifest.get("operation"),
        "task_alias": payload.get("task_alias"),
        "pair_key": payload.get("pair_key"),
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(runner_summary, ensure_ascii=False))

    pipeline_path = _pipeline_script()
    if not pipeline_path.exists():
        raise FileNotFoundError(f"ISCE2 pipeline script not found: {pipeline_path}")

    child_env = _build_child_env()
    dependency_error = _validate_runtime_dependencies(payload, child_env)
    if dependency_error:
        print(
            json.dumps({"runtime_dependency_error": dependency_error}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2

    argv = _build_pipeline_argv(payload, dry_run=bool(args.dry_run))
    print(json.dumps({"pipeline_argv": argv}, ensure_ascii=False))
    completed = subprocess.run(
        argv,
        env=child_env,
        check=False,
    )
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
