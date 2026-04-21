#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


LT1_INPUT_GLOBS = ("LT1*.tar.gz", "LT1*.tiff")
PAIR_META_FILENAME = ".dinsar_pair.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize a PyINT LT-1 workspace from an existing Task_xxx pair directory."
    )
    parser.add_argument("task_dir", help="Task directory containing master/ and slave/ subdirectories.")
    parser.add_argument("--project-dir", required=True, help="Workspace directory for the generated PyINT project.")
    parser.add_argument("--template-root", required=True, help="Directory where the generated template will be written.")
    parser.add_argument("--output-dir", required=True, help="Directory where normalized native outputs will be copied.")
    parser.add_argument("--pyint-home", required=True, help="PyINT repository root inside WSL.")
    parser.add_argument("--pyint-app-script", required=True, help="pyintApp.py path inside WSL.")
    parser.add_argument("--python", required=True, help="Python interpreter used to run PyINT inside WSL.")
    parser.add_argument("--dem-root", required=True, help="DEMDIR root used by PyINT.")
    parser.add_argument("--dem-mode", default="local_fabdem", help="DEM strategy used for this run.")
    parser.add_argument("--fabdem-root", default="", help="Optional FABDEM tile root inside WSL.")
    parser.add_argument("--prepared-dem-path", default="", help="Optional existing DEM path inside WSL.")
    parser.add_argument("--opentopo-dem-type", default="SRTMGL1", help="DEM type when using OpenTopography.")
    parser.add_argument("--opentopo-api-key", default="", help="Optional OpenTopography API key.")
    parser.add_argument("--project-name", required=True, help="Unique PyINT project name for this run.")
    parser.add_argument("--gamma-env-script", default="", help="Optional shell script used to expose GAMMA commands.")
    parser.add_argument("--pair-key", default="", help="Pair key recorded into the run summary.")
    parser.add_argument("--task-alias", default="", help="Task alias recorded into the run summary.")
    parser.add_argument("--orbit-policy", default="require_txt", help="Orbit governance policy recorded into the run summary.")
    parser.add_argument("--input-assets-dir", default="", help="Optional input_assets directory for this run.")
    parser.add_argument("--input-assets-json", default="", help="Optional task_manifest.json path for this run.")
    parser.add_argument("--master-date", default="", help="Master date in YYYYMMDD format.")
    parser.add_argument("--slave-date", default="", help="Slave date in YYYYMMDD format.")
    parser.add_argument("--time-baseline-days", type=int, default=0, help="Time baseline to record in ifgram_list.txt.")
    parser.add_argument("--range-looks", type=int, default=2)
    parser.add_argument("--azimuth-looks", type=int, default=2)
    parser.add_argument("--parallel-workers", type=int, default=1)
    parser.add_argument("--lt1-precise-orbit-enabled", default="true", help="Enable LT-1 precise orbit bridge.")
    parser.add_argument("--lt1-precise-orbit-mode", default="replace", help="LT-1 precise orbit bridge mode.")
    parser.add_argument("--lt1-precise-orbit-strict", default="true", help="Fail the run if precise orbit bridge fails.")
    parser.add_argument(
        "--lt1-precise-orbit-validate-with-orb-filt",
        default="false",
        help="Run ORB_filt_spline.py on a validation copy after rewriting state vectors.",
    )
    parser.add_argument("--lt1-precise-orbit-backup", default="true", help="Backup original .slc.par before rewrite.")
    parser.add_argument("--lt1-precise-orbit-orb-filt-degree", type=int, default=5)
    parser.add_argument("--unwrap", dest="unwrap", action="store_true")
    parser.add_argument("--no-unwrap", dest="unwrap", action="store_false")
    parser.add_argument("--geocode", dest="geocode", action="store_true")
    parser.add_argument("--no-geocode", dest="geocode", action="store_false")
    parser.add_argument("--force", action="store_true", help="Delete an existing run root before rebuilding it.")
    parser.set_defaults(unwrap=True, geocode=True)
    return parser.parse_args()


def normalize_date_text(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8 and digits.startswith("20"):
        return digits[:8]
    return ""


def normalize_bool_text(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve()
    if len(resolved.parts) < 4:
        raise RuntimeError(f"Refusing to remove an unsafe path: {resolved}")
    shutil.rmtree(resolved)


def load_pair_meta(task_dir: Path) -> Dict[str, Any]:
    path = task_dir / PAIR_META_FILENAME
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_json_file(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def discover_lt1_archives(scene_dir: Path) -> List[Path]:
    if not scene_dir.is_dir():
        return []
    items: List[Path] = []
    for pattern in LT1_INPUT_GLOBS:
        items.extend(path.resolve() for path in scene_dir.rglob(pattern) if path.is_file())
    return sorted(set(items))


def infer_scene_date(paths: Iterable[Path]) -> str:
    dates = {
        normalize_date_text(path.name)
        for path in paths
        if normalize_date_text(path.name)
    }
    if len(dates) == 1:
        return next(iter(dates))
    return ""


def hardlink_or_copy(src: Path, dst: Path) -> str:
    ensure_directory(dst.parent)
    if dst.exists():
        return "skipped"
    try:
        os.link(src, dst)
        return "hardlinked"
    except OSError:
        pass
    try:
        dst.symlink_to(src)
        return "symlinked"
    except OSError:
        pass
    shutil.copy2(src, dst)
    return "copied"


def collect_related_lt1_input_files(path: Path) -> List[Path]:
    resolved = path.resolve()
    if resolved.suffix.lower() != ".tiff":
        return [resolved]

    stem = resolved.stem
    files = [
        candidate.resolve()
        for candidate in resolved.parent.iterdir()
        if candidate.is_file() and (candidate.name == resolved.name or candidate.name.startswith(stem))
    ]
    return sorted(set(files))


def write_text(path: Path, content: str) -> Path:
    ensure_directory(path.parent)
    path.write_text(content, encoding="utf-8")
    return path


def inspect_prepared_dem_path(path_text: str) -> Dict[str, str]:
    text = str(path_text or "").strip()
    if not text:
        return {
            "path": "",
            "kind": "",
            "direct_dem_path": "",
            "source_dem_path": "",
            "source_dem_open_path": "",
        }

    path = Path(text)
    try:
        resolved_path = path.resolve()
    except Exception:
        resolved_path = path

    gamma_par_path = Path(str(resolved_path) + ".par")
    vrt_path = Path(str(resolved_path) + ".vrt")
    xml_path = Path(str(resolved_path) + ".xml")
    hdr_path = Path(str(resolved_path) + ".hdr")

    if resolved_path.is_file() and gamma_par_path.is_file():
        return {
            "path": str(resolved_path),
            "kind": "gamma_ready",
            "direct_dem_path": str(resolved_path),
            "source_dem_path": "",
            "source_dem_open_path": "",
        }

    if resolved_path.is_file() and (vrt_path.is_file() or xml_path.is_file() or hdr_path.is_file()):
        return {
            "path": str(resolved_path),
            "kind": "source_dem",
            "direct_dem_path": "",
            "source_dem_path": str(resolved_path),
            "source_dem_open_path": str(vrt_path if vrt_path.is_file() else resolved_path),
        }

    if resolved_path.suffix.lower() == ".vrt" and resolved_path.is_file():
        return {
            "path": str(resolved_path),
            "kind": "source_dem",
            "direct_dem_path": "",
            "source_dem_path": str(resolved_path),
            "source_dem_open_path": str(resolved_path),
        }

    return {
        "path": str(resolved_path),
        "kind": "",
        "direct_dem_path": "",
        "source_dem_path": "",
        "source_dem_open_path": "",
    }


def build_template_text(
    *,
    project_name: str,
    master_date: str,
    range_looks: int,
    azimuth_looks: int,
    parallel_workers: int,
    unwrap: bool,
    geocode: bool,
    dem_mode: str,
    fabdem_root: str,
    prepared_dem_path: str,
    opentopo_dem_type: str,
    opentopo_api_key: str,
) -> str:
    prepared_dem = inspect_prepared_dem_path(prepared_dem_path) if dem_mode == "prepared_file" else {}
    lines = [
        f"# Auto-generated for {project_name}",
        "satelite=LT",
        f"masterDate={master_date}",
        f"range_looks={int(range_looks)}",
        f"azimuth_looks={int(azimuth_looks)}",
        "download_data=0",
        "raw2slc_all=1",
        f"raw2slc_all_parallel={int(parallel_workers)}",
        "extract_burst_all=0",
        f"extract_all_parallel={int(parallel_workers)}",
        "coreg_all=1",
        f"coreg_all_parallel={int(parallel_workers)}",
        "select_pairs=0",
        "diff_all=1",
        f"diff_all_parallel={int(parallel_workers)}",
        "pot_all=0",
        f"pot_all_parallel={int(parallel_workers)}",
        f"unwrap_all={1 if unwrap else 0}",
        f"unwrap_all_parallel={int(parallel_workers)}",
        "atmcor_all=0",
        f"atmcor_all_parallel={int(parallel_workers)}",
        f"geocode_all={1 if geocode else 0}",
        f"geocode_all_parallel={int(parallel_workers)}",
        "gacos_correction=0",
        "load_data=0",
        "geocode_products=hyp3,licsbas",
    ]
    if dem_mode == "local_fabdem" and fabdem_root:
        lines.append(f"fabdem_dir={fabdem_root}")
    else:
        lines.append("fabdem_dir=-")
    if dem_mode == "prepared_file" and prepared_dem.get("kind") == "gamma_ready":
        lines.append(f"DEM={prepared_dem['direct_dem_path']}")
    if dem_mode == "prepared_file" and prepared_dem.get("kind") == "source_dem":
        lines.append(f"prepared_dem_source={prepared_dem['source_dem_path']}")
    else:
        lines.append("prepared_dem_source=-")
    if dem_mode == "opentopo":
        lines.append(f"opentopo_dem_type={opentopo_dem_type or 'SRTMGL1'}")
        lines.append(f"opentopo_api_key={opentopo_api_key or '-'}")
    else:
        lines.append("opentopo_dem_type=-")
        lines.append("opentopo_api_key=-")
    return "\n".join(lines) + "\n"


def write_ifgram_list(path: Path, master_date: str, slave_date: str, time_baseline_days: int) -> Path:
    content = f"{master_date}-{slave_date}   {int(time_baseline_days)}   0.0\n"
    return write_text(path, content)


def write_wrapper_scripts(
    *,
    wrappers_dir: Path,
    pyint_home: Path,
    python_cmd: str,
    gamma_env_script: str,
) -> List[Path]:
    scripts_dir = pyint_home / "pyint"
    if not scripts_dir.is_dir():
        raise FileNotFoundError(f"PyINT scripts directory not found: {scripts_dir}")

    ensure_directory(wrappers_dir)
    created: List[Path] = []
    pyint_scripts = sorted(path for path in scripts_dir.glob("*.py") if path.is_file())
    for script_path in pyint_scripts:
        wrapper_path = wrappers_dir / script_path.name
        lines = [
            "#!/usr/bin/env bash",
            "set -e",
        ]
        if gamma_env_script:
            lines.append(f". '{gamma_env_script}' >/dev/null 2>&1")
        lines.extend(
            [
                f"export PATH='{wrappers_dir}':'{scripts_dir}':\"$PATH\"",
                f"export PYTHONPATH='{pyint_home}':\"${{PYTHONPATH:-}}\"",
                f"exec '{python_cmd}' '{script_path}' \"$@\"",
                "",
            ]
        )
        write_text(wrapper_path, "\n".join(lines))
        wrapper_path.chmod(wrapper_path.stat().st_mode | stat.S_IEXEC)
        created.append(wrapper_path)
    return created


def run_logged(command: List[str], *, env: Dict[str, str], cwd: Path, stdout_path: Path, stderr_path: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    write_text(stdout_path, result.stdout or "")
    write_text(stderr_path, result.stderr or "")
    return result


def require_task_layout(task_dir: Path) -> None:
    missing = [name for name in ("master", "slave") if not (task_dir / name).is_dir()]
    if missing:
        raise FileNotFoundError(f"Task directory is missing required subdirectories: {', '.join(missing)}")


def collect_expected_outputs(project_dir: Path, pair_name: str, range_looks: int) -> Dict[str, str]:
    pair_dir = project_dir / "ifgrams" / pair_name
    look_text = f"{int(range_looks)}rlks"
    return {
        "pair_dir": str(pair_dir),
        "diff_filt": str(pair_dir / f"{pair_name}_{look_text}.diff_filt"),
        "coh": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.cor"),
        "unw": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.unw"),
        "geo_unw": str(pair_dir / f"geo_{pair_name}_{look_text}.diff_filt.unw"),
        "geo_los": str(pair_dir / f"geo_{pair_name}_{look_text}.los_disp"),
    }


def assert_required_outputs(outputs: Dict[str, str], *, unwrap: bool, geocode: bool) -> None:
    required = ["pair_dir", "diff_filt", "coh"]
    if unwrap:
        required.append("unw")
    if geocode:
        required.append("geo_unw")
    missing = [name for name in required if not Path(outputs[name]).exists()]
    if missing:
        raise RuntimeError(f"PyINT run finished but required outputs are missing: {', '.join(missing)}")


def is_binary_all_zero(path: Path, *, chunk_size: int = 1024 * 1024) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return True
            if any(chunk):
                return False


def collect_output_sanity_checks(
    outputs: Dict[str, str],
    *,
    unwrap: bool,
    geocode: bool,
) -> List[Dict[str, Any]]:
    targets = [
        ("diff_filt", "wrapped differential interferogram"),
        ("coh", "coherence"),
    ]
    if unwrap:
        targets.append(("unw", "unwrapped interferogram"))
    if geocode:
        targets.extend(
            [
                ("geo_unw", "geocoded unwrapped interferogram"),
                ("geo_los", "geocoded LOS displacement"),
            ]
        )

    checks: List[Dict[str, Any]] = []
    for name, label in targets:
        path = Path(outputs[name])
        exists = path.exists()
        size_bytes = path.stat().st_size if exists else 0
        all_zero = exists and is_binary_all_zero(path)
        checks.append(
            {
                "name": name,
                "label": label,
                "path": str(path),
                "exists": exists,
                "size_bytes": int(size_bytes),
                "all_zero": bool(all_zero),
                "ok": bool(exists and size_bytes > 0 and not all_zero),
            }
        )
    return checks


def assert_output_sanity(checks: List[Dict[str, Any]]) -> None:
    failed = [item for item in checks if not item.get("ok")]
    if not failed:
        return
    detail = ", ".join(f"{item['name']}={item['path']}" for item in failed)
    raise RuntimeError(f"PyINT run produced invalid all-zero binary outputs: {detail}")


def collect_stage_error_logs(project_dir: Path) -> Dict[str, str]:
    logs: Dict[str, str] = {}
    for filename in (
        "coreg_gamma_all.err",
        "diff_gamma_all.err",
        "unwrap_gamma_all.err",
        "geocode_gamma_all.err",
    ):
        path = project_dir / filename
        if path.is_file():
            logs[filename] = str(path)
    return logs


def copy_native_outputs(
    *,
    project_dir: Path,
    output_dir: Path,
    pair_name: str,
    template_path: Path,
    ifgram_list_path: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> Dict[str, str]:
    ensure_directory(output_dir)
    native_pair_dir = project_dir / "ifgrams" / pair_name
    target_pair_dir = output_dir / "ifgrams" / pair_name
    if native_pair_dir.is_dir():
        shutil.copytree(native_pair_dir, target_pair_dir, dirs_exist_ok=True)

    target_template = output_dir / template_path.name
    shutil.copy2(template_path, target_template)
    target_ifgram_list = output_dir / ifgram_list_path.name
    shutil.copy2(ifgram_list_path, target_ifgram_list)
    target_stdout = output_dir / stdout_path.name
    target_stderr = output_dir / stderr_path.name
    shutil.copy2(stdout_path, target_stdout)
    shutil.copy2(stderr_path, target_stderr)

    return {
        "pair_dir": str(target_pair_dir),
        "template_path": str(target_template),
        "ifgram_list_path": str(target_ifgram_list),
        "stdout_path": str(target_stdout),
        "stderr_path": str(target_stderr),
    }


def collect_orbit_bridge_summaries(project_dir: Path) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    slc_root = project_dir / "SLC"
    if not slc_root.is_dir():
        return summaries

    for summary_path in sorted(slc_root.glob("*/orbit_bridge_summary.json")):
        payload = load_json_file(summary_path)
        operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
        failed_operations = [item for item in operations if not item.get("ok")]
        summaries.append(
            {
                "path": str(summary_path),
                "date_dir": summary_path.parent.name,
                "ok": bool(payload.get("ok", not failed_operations)),
                "operation_count": len(operations),
                "failed_operation_count": len(failed_operations),
                "operations": operations,
                "payload": payload,
            }
        )
    return summaries


def copy_orbit_bridge_summaries(summaries: List[Dict[str, Any]], output_dir: Path) -> Dict[str, str]:
    if not summaries:
        return {}

    target_dir = ensure_directory(output_dir / "orbit_bridge")
    copied: Dict[str, str] = {}
    for item in summaries:
        source_path = Path(item["path"])
        target_path = target_dir / f"{item['date_dir']}_orbit_bridge_summary.json"
        shutil.copy2(source_path, target_path)
        copied[item["date_dir"]] = str(target_path)
    return copied


def assert_orbit_bridge_ok(
    *,
    enabled: bool,
    strict: bool,
    summaries: List[Dict[str, Any]],
    expected_dates: Iterable[str],
) -> None:
    if not enabled:
        return
    if not strict:
        return

    expected = {str(item).strip() for item in expected_dates if str(item).strip()}
    found = {str(item.get("date_dir") or "").strip() for item in summaries if str(item.get("date_dir") or "").strip()}
    missing = sorted(expected - found)
    if missing:
        raise RuntimeError(f"LT-1 precise orbit bridge summary is missing for: {', '.join(missing)}")

    failed = [item for item in summaries if not item.get("ok")]
    if failed:
        failed_dates = ", ".join(sorted(str(item.get("date_dir") or "") for item in failed))
        raise RuntimeError(f"LT-1 precise orbit bridge reported failures for: {failed_dates}")


def main() -> int:
    args = parse_args()

    task_dir = Path(args.task_dir).resolve()
    project_dir = Path(args.project_dir).resolve()
    run_root = project_dir.parent
    template_root = Path(args.template_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    pyint_home = Path(args.pyint_home).resolve()
    pyint_app_script = Path(args.pyint_app_script).resolve()
    dem_root = Path(args.dem_root).resolve()
    input_assets_dir = Path(args.input_assets_dir).resolve() if args.input_assets_dir else None
    input_assets_json = Path(args.input_assets_json).resolve() if args.input_assets_json else None
    input_assets_payload = load_json_file(input_assets_json)
    precise_orbit_enabled = normalize_bool_text(args.lt1_precise_orbit_enabled, True)
    precise_orbit_strict = normalize_bool_text(args.lt1_precise_orbit_strict, True)
    precise_orbit_validate_with_orb_filt = normalize_bool_text(
        args.lt1_precise_orbit_validate_with_orb_filt,
        False,
    )
    precise_orbit_backup = normalize_bool_text(args.lt1_precise_orbit_backup, True)
    precise_orbit_mode = str(args.lt1_precise_orbit_mode or "replace").strip().lower() or "replace"
    precise_orbit_helper = (Path(__file__).resolve().parent / "apply_lt1_precise_orbit.py").resolve()
    dem_mode = str(args.dem_mode or "local_fabdem").strip().lower() or "local_fabdem"
    prepared_dem_info = inspect_prepared_dem_path(args.prepared_dem_path) if dem_mode == "prepared_file" else {}

    require_task_layout(task_dir)
    if not pyint_app_script.is_file():
        raise FileNotFoundError(f"pyintApp.py not found: {pyint_app_script}")
    if precise_orbit_enabled and not precise_orbit_helper.is_file():
        raise FileNotFoundError(f"Precise orbit bridge helper not found: {precise_orbit_helper}")
    if precise_orbit_enabled and input_assets_json is None:
        raise RuntimeError("LT-1 precise orbit bridge requires --input-assets-json.")
    if dem_mode == "prepared_file" and not prepared_dem_info.get("kind"):
        raise RuntimeError(
            "Prepared DEM mode requires either a Gamma DEM with .par, "
            "or a source DEM with .xml/.hdr/.vrt sidecars."
        )

    if args.force:
        safe_rmtree(run_root)
        safe_rmtree(template_root)
        safe_rmtree(output_dir)

    if run_root.exists():
        raise RuntimeError(f"PyINT run root already exists, rerun with --force: {run_root}")

    pair_meta = load_pair_meta(task_dir)
    master_archives = discover_lt1_archives(task_dir / "master")
    slave_archives = discover_lt1_archives(task_dir / "slave")
    if not master_archives:
        raise FileNotFoundError(f"No LT1 archives found under: {task_dir / 'master'}")
    if not slave_archives:
        raise FileNotFoundError(f"No LT1 archives found under: {task_dir / 'slave'}")

    master_date = normalize_date_text(args.master_date) or normalize_date_text(pair_meta.get("master_imaging_date")) or infer_scene_date(master_archives)
    slave_date = normalize_date_text(args.slave_date) or normalize_date_text(pair_meta.get("slave_imaging_date")) or infer_scene_date(slave_archives)
    if not master_date or not slave_date:
        raise RuntimeError("Unable to determine master/slave dates from pair metadata or archive names.")

    pair_name = f"{master_date}-{slave_date}"
    task_alias = str(args.task_alias or pair_meta.get("task_alias") or task_dir.name).strip() or task_dir.name
    pair_key = str(args.pair_key or pair_meta.get("pair_key") or "").strip()
    time_baseline_days = int(args.time_baseline_days or pair_meta.get("time_baseline_days") or 0)

    ensure_directory(run_root)
    ensure_directory(template_root)
    ensure_directory(output_dir)
    ensure_directory(dem_root)

    pyint_scripts_dir = pyint_home / "pyint"
    wrappers_dir = ensure_directory(run_root / "wrappers")
    write_wrapper_scripts(
        wrappers_dir=wrappers_dir,
        pyint_home=pyint_home,
        python_cmd=args.python,
        gamma_env_script=args.gamma_env_script,
    )

    template_path = write_text(
        template_root / f"{args.project_name}.template",
        build_template_text(
            project_name=args.project_name,
            master_date=master_date,
            range_looks=args.range_looks,
            azimuth_looks=args.azimuth_looks,
            parallel_workers=args.parallel_workers,
            unwrap=bool(args.unwrap),
            geocode=bool(args.geocode),
            dem_mode=dem_mode,
            fabdem_root=str(args.fabdem_root or "").strip(),
            prepared_dem_path=str(args.prepared_dem_path or "").strip(),
            opentopo_dem_type=str(args.opentopo_dem_type or "SRTMGL1").strip(),
            opentopo_api_key=str(args.opentopo_api_key or "").strip(),
        ),
    )

    scratch_root = ensure_directory(project_dir.parent)
    archive_materialization: List[Dict[str, str]] = []
    env = os.environ.copy()
    env.update(
        {
            "SCRATCHDIR": str(scratch_root),
            "TEMPLATEDIR": str(template_root),
            "DEMDIR": str(dem_root),
            "PATH": f"{wrappers_dir}:{pyint_scripts_dir}:{env.get('PATH', '')}",
            "PYTHONPATH": f"{pyint_home}:{env.get('PYTHONPATH', '')}",
            "PYINT_LT1_PRECISE_ORBIT_ENABLED": "true" if precise_orbit_enabled else "false",
            "PYINT_LT1_PRECISE_ORBIT_MODE": precise_orbit_mode,
            "PYINT_LT1_PRECISE_ORBIT_STRICT": "true" if precise_orbit_strict else "false",
            "PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT": "true" if precise_orbit_validate_with_orb_filt else "false",
            "PYINT_LT1_PRECISE_ORBIT_BACKUP": "true" if precise_orbit_backup else "false",
            "PYINT_LT1_PRECISE_ORBIT_ORB_FILT_DEGREE": str(int(args.lt1_precise_orbit_orb_filt_degree)),
            "PYINT_LT1_PRECISE_ORBIT_HELPER": str(precise_orbit_helper),
            "PYINT_LT1_PRECISE_ORBIT_MANIFEST": str(input_assets_json) if input_assets_json else "",
        }
    )

    generate_stdout = run_root / "pyint_generate.stdout.log"
    generate_stderr = run_root / "pyint_generate.stderr.log"
    generate_result = run_logged(
        [str(wrappers_dir / "pyintApp.py"), "-g", args.project_name],
        env=env,
        cwd=scratch_root,
        stdout_path=generate_stdout,
        stderr_path=generate_stderr,
    )
    if generate_result.returncode != 0:
        raise RuntimeError(
            f"pyintApp.py -g failed with rc={generate_result.returncode}: "
            f"{(generate_result.stderr or generate_result.stdout or '').strip()}"
        )

    pyint_project_dir = project_dir
    download_dir = ensure_directory(pyint_project_dir / "DOWNLOAD")
    ifgram_list_path = write_ifgram_list(pyint_project_dir / "ifgram_list.txt", master_date, slave_date, time_baseline_days)
    for role, archives in (("master", master_archives), ("slave", slave_archives)):
        for src_path in archives:
            related_files = collect_related_lt1_input_files(src_path)
            for related_path in related_files:
                target_path = download_dir / related_path.name
                op = hardlink_or_copy(related_path, target_path)
                archive_materialization.append(
                    {
                        "role": role,
                        "source": str(related_path),
                        "group_source": str(src_path),
                        "target": str(target_path),
                        "operation": op,
                    }
                )

    run_stdout = run_root / "pyint.stdout.log"
    run_stderr = run_root / "pyint.stderr.log"
    run_started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    run_result = run_logged(
        [str(wrappers_dir / "pyintApp.py"), args.project_name],
        env=env,
        cwd=scratch_root,
        stdout_path=run_stdout,
        stderr_path=run_stderr,
    )
    if run_result.returncode != 0:
        stage_error_logs = collect_stage_error_logs(pyint_project_dir)
        detail_text = (run_result.stderr or run_result.stdout or "").strip()
        if stage_error_logs:
            log_text = ", ".join(f"{name}={path}" for name, path in stage_error_logs.items())
            detail_text = f"{detail_text}\nStage logs: {log_text}" if detail_text else f"Stage logs: {log_text}"
        raise RuntimeError(
            f"pyintApp.py failed with rc={run_result.returncode}: "
            f"{detail_text}"
        )

    orbit_bridge_summaries = collect_orbit_bridge_summaries(pyint_project_dir)
    assert_orbit_bridge_ok(
        enabled=precise_orbit_enabled,
        strict=precise_orbit_strict,
        summaries=orbit_bridge_summaries,
        expected_dates=(master_date, slave_date),
    )

    outputs = collect_expected_outputs(pyint_project_dir, pair_name, args.range_looks)
    assert_required_outputs(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
    output_sanity_checks = collect_output_sanity_checks(
        outputs,
        unwrap=bool(args.unwrap),
        geocode=bool(args.geocode),
    )
    assert_output_sanity(output_sanity_checks)
    stage_error_logs = collect_stage_error_logs(pyint_project_dir)

    copied_paths = copy_native_outputs(
        project_dir=pyint_project_dir,
        output_dir=output_dir,
        pair_name=pair_name,
        template_path=template_path,
        ifgram_list_path=ifgram_list_path,
        stdout_path=run_stdout,
        stderr_path=run_stderr,
    )
    copied_orbit_bridge_paths = copy_orbit_bridge_summaries(orbit_bridge_summaries, output_dir)

    summary = {
        "ok": True,
        "task_dir": str(task_dir),
        "task_alias": task_alias,
        "pair_key": pair_key,
        "project_name": args.project_name,
        "project_dir": str(pyint_project_dir),
        "run_root": str(run_root),
        "template_root": str(template_root),
        "output_dir": str(output_dir),
        "pyint_home": str(pyint_home),
        "pyint_app_script": str(pyint_app_script),
        "gamma_env_script": args.gamma_env_script,
        "dem": {
            "mode": dem_mode,
            "dem_root": str(dem_root),
            "fabdem_root": str(args.fabdem_root or "").strip(),
            "prepared_dem_path": str(args.prepared_dem_path or "").strip(),
            "prepared_dem_kind": str(prepared_dem_info.get("kind") or ""),
            "prepared_dem_direct_path": str(prepared_dem_info.get("direct_dem_path") or ""),
            "prepared_dem_source_path": str(prepared_dem_info.get("source_dem_path") or ""),
            "prepared_dem_open_path": str(prepared_dem_info.get("source_dem_open_path") or ""),
            "opentopo_dem_type": str(args.opentopo_dem_type or "SRTMGL1").strip(),
            "opentopo_api_key_configured": bool(str(args.opentopo_api_key or "").strip()),
        },
        "orbit_policy": str(args.orbit_policy or "require_txt").strip().lower(),
        "precise_orbit_bridge": {
            "enabled": precise_orbit_enabled,
            "mode": precise_orbit_mode,
            "strict": precise_orbit_strict,
            "validate_with_orb_filt": precise_orbit_validate_with_orb_filt,
            "backup": precise_orbit_backup,
            "orb_filt_degree": int(args.lt1_precise_orbit_orb_filt_degree),
            "helper_path": str(precise_orbit_helper),
            "manifest_json": str(input_assets_json) if input_assets_json else "",
            "summaries": [
                {
                    "path": item["path"],
                    "date_dir": item["date_dir"],
                    "ok": item["ok"],
                    "operation_count": item["operation_count"],
                    "failed_operation_count": item["failed_operation_count"],
                    "copied_summary_path": copied_orbit_bridge_paths.get(item["date_dir"], ""),
                }
                for item in orbit_bridge_summaries
            ],
        },
        "input_assets_dir": str(input_assets_dir) if input_assets_dir else "",
        "input_assets_json": str(input_assets_json) if input_assets_json else "",
        "input_assets": input_assets_payload,
        "master_date": master_date,
        "slave_date": slave_date,
        "pair_name": pair_name,
        "time_baseline_days": time_baseline_days,
        "range_looks": int(args.range_looks),
        "azimuth_looks": int(args.azimuth_looks),
        "parallel_workers": int(args.parallel_workers),
        "unwrap": bool(args.unwrap),
        "geocode": bool(args.geocode),
        "archives": {
            "master": [str(path) for path in master_archives],
            "slave": [str(path) for path in slave_archives],
        },
        "archive_materialization": archive_materialization,
        "workspace_outputs": outputs,
        "output_sanity_checks": output_sanity_checks,
        "copied_outputs": copied_paths,
        "copied_orbit_bridge_paths": copied_orbit_bridge_paths,
        "logs": {
            "generate_stdout": str(generate_stdout),
            "generate_stderr": str(generate_stderr),
            "run_stdout": str(run_stdout),
            "run_stderr": str(run_stderr),
            "stage_error_logs": stage_error_logs,
        },
        "started_at": run_started_at,
        "finished_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }

    summary_path = output_dir / "pyint_run_summary.json"
    write_text(summary_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
