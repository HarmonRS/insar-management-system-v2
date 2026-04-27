from __future__ import annotations

import argparse
import json
import os
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
    _append_optional(argv, "--bbox-margin", params.get("bbox_margin"))
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
    env_root = Path(sys.executable).resolve().parents[1]
    proj_data = env_root / "share" / "proj"
    if proj_data.exists():
        env["PROJ_DATA"] = proj_data.as_posix()
        env["PROJ_LIB"] = proj_data.as_posix()
    return env


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

    argv = _build_pipeline_argv(payload, dry_run=bool(args.dry_run))
    print(json.dumps({"pipeline_argv": argv}, ensure_ascii=False))
    completed = subprocess.run(
        argv,
        env=_build_child_env(),
        check=False,
    )
    return int(completed.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main())
