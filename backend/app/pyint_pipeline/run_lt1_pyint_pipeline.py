#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import shutil
import stat
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


LT1_INPUT_GLOBS = ("LT1*.tar.gz", "LT1*.tiff")
PAIR_META_FILENAME = ".dinsar_pair.json"
DEFAULT_DEM_RESOLUTION_M = 30.0
DEFAULT_DEM_OVERSAMPLING = 1.0
DEM_OVERSAMPLING_MIN = 0.25
DEM_OVERSAMPLING_MAX = 16.0
MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS = 1
DEFAULT_UNWRAP_COH_THRESHOLD = 0.05
DEFAULT_COHERENCE_MASK_THRESHOLD = 0.20
DEFAULT_REFERENCE_MODE = "none"
DEFAULT_REFERENCE_COH_THRESHOLD = 0.30
DEFAULT_DERAMP_MODE = "none"
DEFAULT_DERAMP_COH_THRESHOLD = 0.30
DEFAULT_REFLATTEN_MODEL = "plane"
DEFAULT_REFLATTEN_COH_THRESHOLD = 0.70
DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD = 0.20
DEFAULT_REFLATTEN_RANGE_STEP = 32
DEFAULT_REFLATTEN_AZIMUTH_STEP = 32
QUALITY_COHERENCE_THRESHOLDS = (0.20, 0.30, 0.40, 0.50)
GRID_MISMATCH_TOLERANCE = 0.25


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
    parser.add_argument("--target-grid-size-m", type=int, default=0, help="Optional requested grid size recorded for reporting; it does not resample Gamma products.")
    parser.add_argument("--range-looks", type=int, default=2)
    parser.add_argument("--azimuth-looks", type=int, default=2)
    parser.add_argument(
        "--dem-resolution-m",
        type=float,
        default=DEFAULT_DEM_RESOLUTION_M,
        help="Source DEM resolution in meters, used to derive Gamma DEM oversampling.",
    )
    parser.add_argument(
        "--dem-lat-ovr",
        type=float,
        default=0.0,
        help="Gamma DEM latitude oversampling. Defaults to the PyINT/Gamma native setting.",
    )
    parser.add_argument(
        "--dem-lon-ovr",
        type=float,
        default=0.0,
        help="Gamma DEM longitude oversampling. Defaults to the PyINT/Gamma native setting.",
    )
    parser.add_argument(
        "--unwrap-coh-threshold",
        type=float,
        default=DEFAULT_UNWRAP_COH_THRESHOLD,
        help="Minimum coherence used by Gamma rascc_mask/mcf during unwrapping.",
    )
    parser.add_argument(
        "--coherence-mask-threshold",
        type=float,
        default=DEFAULT_COHERENCE_MASK_THRESHOLD,
        help="Minimum coherence reported in Gamma native product quality support metrics.",
    )
    parser.add_argument(
        "--reference-mode",
        default=DEFAULT_REFERENCE_MODE,
        choices={"none", "coh_median"},
        help="Compatibility option; Python does not reference-correct Gamma displacement products.",
    )
    parser.add_argument(
        "--reference-coh-threshold",
        type=float,
        default=DEFAULT_REFERENCE_COH_THRESHOLD,
        help="Minimum coherence used when selecting pixels for reference correction.",
    )
    parser.add_argument(
        "--deramp-mode",
        default=DEFAULT_DERAMP_MODE,
        choices={"none", "plane"},
        help="Compatibility option; Python does not deramp Gamma displacement products.",
    )
    parser.add_argument(
        "--deramp-coh-threshold",
        type=float,
        default=DEFAULT_DERAMP_COH_THRESHOLD,
        help="Minimum coherence used when selecting pixels for deramp fitting.",
    )
    parser.add_argument(
        "--gamma-nodata-value",
        type=float,
        default=-9999.0,
        help="NoData value passed to Gamma data2geotiff exports.",
    )
    parser.add_argument(
        "--geo-interp",
        default="1",
        choices={"0", "1"},
        help="Gamma geocode_back interpolation mode: 0=nearest, 1=bicubic spline.",
    )
    parser.add_argument("--atmcor", dest="atmcor", action="store_true", help="Enable PyINT/Gamma atmcor_all stage.")
    parser.add_argument("--no-atmcor", dest="atmcor", action="store_false")
    parser.add_argument(
        "--atmcor-use-for-disp",
        dest="atmcor_use_for_disp",
        action="store_true",
        help="Use atmcor unwrapped phase as the Gamma dispmap source when atmcor output exists.",
    )
    parser.add_argument("--no-atmcor-use-for-disp", dest="atmcor_use_for_disp", action="store_false")
    parser.add_argument(
        "--reflatten",
        dest="reflatten",
        action="store_true",
        help="Fit and remove a residual unwrapped-phase trend after PyINT/Gamma unwrapping.",
    )
    parser.add_argument("--no-reflatten", dest="reflatten", action="store_false")
    parser.add_argument(
        "--reflatten-model",
        default=DEFAULT_REFLATTEN_MODEL,
        choices={"plane", "linear", "quadratic"},
        help="Gamma quad_fit model for reflattening. linear is accepted as an alias for plane.",
    )
    parser.add_argument(
        "--reflatten-coh-threshold",
        type=float,
        default=DEFAULT_REFLATTEN_COH_THRESHOLD,
        help="Coherence threshold used to build the reflatten fit mask.",
    )
    parser.add_argument(
        "--reflatten-fallback-coh-threshold",
        type=float,
        default=DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD,
        help="Fallback coherence threshold used if the primary reflatten fit fails.",
    )
    parser.add_argument(
        "--reflatten-range-step",
        type=int,
        default=DEFAULT_REFLATTEN_RANGE_STEP,
        help="Range sample spacing passed to Gamma quad_fit.",
    )
    parser.add_argument(
        "--reflatten-azimuth-step",
        type=int,
        default=DEFAULT_REFLATTEN_AZIMUTH_STEP,
        help="Azimuth sample spacing passed to Gamma quad_fit.",
    )
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
    parser.set_defaults(atmcor=False, atmcor_use_for_disp=False, reflatten=True)
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


def clamp_float(value: float, minimum: float, maximum: float) -> float:
    return min(float(maximum), max(float(minimum), float(value)))


def validate_unit_interval(value: float, name: str) -> float:
    parsed = float(value)
    if parsed < 0.0 or parsed > 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0.")
    return parsed


def format_gamma_number(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def calculate_dem_oversampling(
    *,
    dem_resolution_m: float,
    target_grid_size_m: float,
    dem_lat_ovr: float = 0.0,
    dem_lon_ovr: float = 0.0,
) -> Dict[str, Any]:
    dem_resolution = float(dem_resolution_m or DEFAULT_DEM_RESOLUTION_M)
    target_grid = float(target_grid_size_m or 0.0)
    if dem_resolution <= 0:
        dem_resolution = DEFAULT_DEM_RESOLUTION_M

    raw_factor = dem_resolution / target_grid if target_grid > 0 else None
    lat_factor = clamp_float(float(dem_lat_ovr or DEFAULT_DEM_OVERSAMPLING), DEM_OVERSAMPLING_MIN, DEM_OVERSAMPLING_MAX)
    lon_factor = clamp_float(float(dem_lon_ovr or DEFAULT_DEM_OVERSAMPLING), DEM_OVERSAMPLING_MIN, DEM_OVERSAMPLING_MAX)
    average_factor = (lat_factor + lon_factor) / 2.0
    actual_grid = dem_resolution / average_factor if average_factor > 0 else dem_resolution
    mismatch_ratio = abs(actual_grid - target_grid) / target_grid if target_grid > 0 else None
    return {
        "mode": "gamma_dem_oversampling",
        "dem_resolution_m": dem_resolution,
        "target_grid_size_m": target_grid,
        "raw_oversampling": raw_factor,
        "dem_lat_ovr": lat_factor,
        "dem_lon_ovr": lon_factor,
        "actual_grid_size_m": actual_grid,
        "mismatch_ratio": mismatch_ratio,
        "min_oversampling": DEM_OVERSAMPLING_MIN,
        "max_oversampling": DEM_OVERSAMPLING_MAX,
    }


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
    target_grid_size_m: int,
    dem_lat_ovr: float,
    dem_lon_ovr: float,
    unwrap_coh_threshold: float,
    geo_interp: str,
    atmcor: bool,
    atmcor_use_for_disp: bool,
    reflatten: bool,
    reflatten_model: str,
    reflatten_coh_threshold: float,
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
        f"target_grid_size_m={int(target_grid_size_m or 0)}",
        f"dem_lat_ovr={format_gamma_number(dem_lat_ovr)}",
        f"dem_lon_ovr={format_gamma_number(dem_lon_ovr)}",
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
        f"unwrapThreshold={format_gamma_number(unwrap_coh_threshold)}",
        "make_mask=1",
        "auto_unw=1",
        "r_refer=-",
        "a_refer=-",
        f"atmcor_all={1 if atmcor else 0}",
        f"atmcor_all_parallel={int(parallel_workers)}",
        f"atmcor_use_for_disp={1 if (atmcor and atmcor_use_for_disp) else 0}",
        f"reflatten={1 if reflatten else 0}",
        f"reflatten_model={str(reflatten_model or DEFAULT_REFLATTEN_MODEL).strip().lower()}",
        f"reflatten_coh_threshold={format_gamma_number(reflatten_coh_threshold)}",
        f"geocode_all={1 if geocode else 0}",
        f"geocode_all_parallel={int(parallel_workers)}",
        f"geo_interp={str(geo_interp or '0').strip()}",
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


def load_shell_environment(script_path: str, base_env: Dict[str, str]) -> Dict[str, str]:
    text = str(script_path or "").strip()
    if not text:
        return {}
    path = Path(text).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Gamma environment script not found: {path}")

    command = f". {shlex.quote(str(path))} >/dev/null 2>&1; env -0"
    result = subprocess.run(
        ["bash", "-lc", command],
        env=base_env,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to source Gamma environment script {path}: {detail}")

    values: Dict[str, str] = {}
    for chunk in result.stdout.split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        key, value = chunk.split(b"=", 1)
        values[key.decode("utf-8", errors="replace")] = value.decode("utf-8", errors="replace")
    return values


def run_logged(
    command: List[str],
    *,
    env: Dict[str, str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    mirror_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    ensure_directory(stdout_path.parent)
    ensure_directory(stderr_path.parent)
    stdout_parts: List[str] = []
    stderr_parts: List[str] = []

    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def _drain(stream: Any, target_path: Path, parts: List[str], mirror: Any) -> None:
        with target_path.open("w", encoding="utf-8") as fp:
            for line in iter(stream.readline, ""):
                parts.append(line)
                fp.write(line)
                fp.flush()
                if mirror_output:
                    mirror.write(line)
                    mirror.flush()

    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, stdout_path, stdout_parts, sys.stdout), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, stderr_path, stderr_parts, sys.stderr), daemon=True),
    ]
    for thread in threads:
        thread.start()
    returncode = proc.wait()
    for thread in threads:
        thread.join()

    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def require_task_layout(task_dir: Path) -> None:
    missing = [name for name in ("master", "slave") if not (task_dir / name).is_dir()]
    if missing:
        raise FileNotFoundError(f"Task directory is missing required subdirectories: {', '.join(missing)}")


def collect_expected_outputs(project_dir: Path, pair_name: str, range_looks: int) -> Dict[str, str]:
    pair_dir = project_dir / "ifgrams" / pair_name
    look_text = f"{int(range_looks)}rlks"
    master_date = pair_name.split("-", 1)[0]
    return {
        "pair_dir": str(pair_dir),
        "diff_filt": str(pair_dir / f"{pair_name}_{look_text}.diff_filt"),
        "coh": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.cor"),
        "unw": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.unw"),
        "reflat_unw": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.reflat.unw"),
        "reflat_trend": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.reflat.trend"),
        "reflat_mask": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.reflat.mask.bmp"),
        "reflat_diff_par": str(pair_dir / f"{pair_name}_{look_text}.reflat.diff_par"),
        "geo_coh": str(pair_dir / f"geo_{master_date}_{look_text}.diff_filt.cor"),
        "geo_unw": str(pair_dir / f"geo_{pair_name}_{look_text}.diff_filt.unw"),
        "geo_reflat_unw": str(pair_dir / f"geo_{pair_name}_{look_text}.diff_filt.reflat.unw"),
        "atmcor_unw": str(pair_dir / f"{pair_name}_{look_text}.diff_filt.atmcor.unw"),
        "geo_atmcor_unw": str(pair_dir / f"geo_{pair_name}_{look_text}.diff_filt.atmcor.unw"),
        "geo_los": str(pair_dir / f"geo_{pair_name}_{look_text}.los_disp"),
        "reflat_los": str(pair_dir / f"{pair_name}_{look_text}.reflat.los_disp"),
        "geo_reflat_los": str(pair_dir / f"geo_{pair_name}_{look_text}.reflat.los_disp"),
        "reflat_vert": str(pair_dir / f"{pair_name}_{look_text}.reflat.vert_disp"),
        "geo_reflat_vert": str(pair_dir / f"geo_{pair_name}_{look_text}.reflat.vert_disp"),
        "geo_atmcor_los": str(pair_dir / f"geo_{pair_name}_{look_text}.atmcor.los_disp"),
        "geo_vert": str(pair_dir / f"geo_{pair_name}_{look_text}.vert_disp"),
        "geo_atmcor_vert": str(pair_dir / f"geo_{pair_name}_{look_text}.atmcor.vert_disp"),
        "geo_wrapped_phase": str(pair_dir / f"geo_{pair_name}_{look_text}.diff_filt.pha"),
        "look_vector_theta": str(pair_dir / "lv_theta"),
        "look_vector_phi": str(pair_dir / "lv_phi"),
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
        targets.append(("geo_unw", "geocoded unwrapped interferogram"))
    if outputs.get("reflat_unw") and Path(outputs["reflat_unw"]).is_file():
        targets.append(("reflat_unw", "reflattened unwrapped interferogram"))
    if outputs.get("geo_reflat_unw") and Path(outputs["geo_reflat_unw"]).is_file():
        targets.append(("geo_reflat_unw", "geocoded reflattened unwrapped interferogram"))

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
    details = []
    for item in failed:
        if not item.get("exists"):
            reason = "missing"
        elif int(item.get("size_bytes") or 0) <= 0:
            reason = "empty"
        elif item.get("all_zero"):
            reason = "all-zero"
        else:
            reason = "invalid"
        details.append(f"{item['name']}({reason})={item['path']}")
    raise RuntimeError(f"PyINT run produced invalid binary outputs: {', '.join(details)}")


def _gamma_reflatten_model_code(model: str) -> int:
    normalized = str(model or DEFAULT_REFLATTEN_MODEL).strip().lower()
    if normalized in {"plane", "linear"}:
        return 3
    if normalized == "quadratic":
        return 0
    raise ValueError("reflatten_model must be plane or quadratic.")


def _run_reflatten_command(
    *,
    command: List[str],
    stage: str,
    log_dir: Path,
    env: Dict[str, str],
    cwd: Path,
) -> Dict[str, Any]:
    stdout_path = log_dir / f"{stage}.stdout.log"
    stderr_path = log_dir / f"{stage}.stderr.log"
    result = run_logged(
        command,
        env=env,
        cwd=cwd,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        mirror_output=False,
    )
    info = {
        "stage": stage,
        "command": command,
        "returncode": int(result.returncode),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_tail": (result.stdout or "")[-2000:],
        "stderr_tail": (result.stderr or "")[-2000:],
    }
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Gamma reflatten stage {stage} failed with rc={result.returncode}: {detail}")
    return info


def run_gamma_reflatten(
    *,
    project_dir: Path,
    run_root: Path,
    output_dir: Path,
    outputs: Dict[str, str],
    pair_name: str,
    master_date: str,
    range_looks: int,
    env: Dict[str, str],
    model: str,
    coherence_threshold: float,
    fallback_coherence_threshold: float,
    range_step: int,
    azimuth_step: int,
    geo_interp: str,
) -> Dict[str, Any]:
    pair_dir = Path(outputs["pair_dir"])
    look_text = f"{int(range_looks)}rlks"
    unw = Path(outputs["unw"])
    atmcor_unw = Path(str(outputs.get("atmcor_unw") or ""))
    if atmcor_unw.is_file():
        unw = atmcor_unw
    coh = Path(outputs["coh"])
    amp = pair_dir / f"{master_date}_{look_text}.amp"
    amp_par = pair_dir / f"{master_date}_{look_text}.amp.par"
    source_amp = project_dir / "RSLC" / master_date / f"{master_date}_{look_text}.amp"
    source_amp_par = project_dir / "RSLC" / master_date / f"{master_date}_{look_text}.amp.par"
    source_diff_par = project_dir / "DEM" / f"{master_date}_{look_text}.diff_par"
    diff_par = Path(outputs["reflat_diff_par"])
    off_par = pair_dir / f"{pair_name}_{look_text}.off"
    utm_to_rdc = project_dir / "DEM" / f"{master_date}_{look_text}.UTM_TO_RDC"
    utm_dem_par = project_dir / "DEM" / f"{master_date}_{look_text}.utm.dem.par"
    rdc_dem = project_dir / "DEM" / f"{master_date}_{look_text}.rdc.dem"
    slc_par = project_dir / "SLC" / master_date / f"{master_date}.slc.par"

    if not amp.is_file() and source_amp.is_file():
        shutil.copy2(source_amp, amp)
    if not amp_par.is_file() and source_amp_par.is_file():
        shutil.copy2(source_amp_par, amp_par)

    required = {
        "unw": unw,
        "coh": coh,
        "amp": amp,
        "amp_par": amp_par,
        "source_diff_par": source_diff_par,
        "off_par": off_par,
        "utm_to_rdc": utm_to_rdc,
        "utm_dem_par": utm_dem_par,
        "slc_par": slc_par,
    }
    missing = [f"{name}={path}" for name, path in required.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Gamma reflatten requires missing native files: " + ", ".join(missing))
    shutil.copy2(source_diff_par, diff_par)

    values = read_gamma_par_file(amp_par)
    width = _gamma_par_int(values, "range_samples")
    nlines = _gamma_par_int(values, "azimuth_lines")
    if width <= 0 or nlines <= 0:
        raise RuntimeError(f"Cannot determine reflatten radar grid from: {amp_par}")
    dem_values = read_gamma_par_file(utm_dem_par)
    geo_width = _gamma_par_int(dem_values, "width")
    geo_nlines = _gamma_par_int(dem_values, "nlines")
    if geo_width <= 0 or geo_nlines <= 0:
        raise RuntimeError(f"Cannot determine reflatten geocoded grid from: {utm_dem_par}")

    log_dir = ensure_directory(run_root / "gamma_reflatten")
    primary_threshold = validate_unit_interval(coherence_threshold, "--reflatten-coh-threshold")
    fallback_threshold = validate_unit_interval(
        fallback_coherence_threshold,
        "--reflatten-fallback-coh-threshold",
    )
    thresholds = [primary_threshold]
    if fallback_threshold != primary_threshold:
        thresholds.append(fallback_threshold)

    model_code = _gamma_reflatten_model_code(model)
    command_results: List[Dict[str, Any]] = []
    last_error = ""
    selected_threshold = primary_threshold
    reflat_mask = Path(outputs["reflat_mask"])
    reflat_trend = Path(outputs["reflat_trend"])
    reflat_unw = Path(outputs["reflat_unw"])

    for attempt_index, threshold in enumerate(thresholds, start=1):
        selected_threshold = threshold
        suffix = "" if attempt_index == 1 else f".fallback{attempt_index}"
        mask_path = reflat_mask if attempt_index == 1 else Path(str(reflat_mask) + suffix + ".bmp")
        trend_path = reflat_trend if attempt_index == 1 else Path(str(reflat_trend) + suffix)
        try:
            command_results.append(
                _run_reflatten_command(
                    command=[
                        "rascc_mask",
                        str(coh),
                        str(amp),
                        str(width),
                        "1",
                        "1",
                        "0",
                        "1",
                        "1",
                        format_gamma_number(threshold),
                        "0.0",
                        "0.1",
                        "0.9",
                        "1.",
                        ".35",
                        "1",
                        str(mask_path),
                    ],
                    stage=f"rascc_mask_attempt{attempt_index}",
                    log_dir=log_dir,
                    env=env,
                    cwd=project_dir,
                )
            )
            command_results.append(
                _run_reflatten_command(
                    command=[
                        "quad_fit",
                        str(unw),
                        str(diff_par),
                        str(max(1, int(range_step))),
                        str(max(1, int(azimuth_step))),
                        str(mask_path),
                        "-",
                        str(model_code),
                        str(trend_path),
                    ],
                    stage=f"quad_fit_attempt{attempt_index}",
                    log_dir=log_dir,
                    env=env,
                    cwd=project_dir,
                )
            )
            if trend_path != reflat_trend:
                shutil.copy2(trend_path, reflat_trend)
            if mask_path != reflat_mask:
                shutil.copy2(mask_path, reflat_mask)
            last_error = ""
            break
        except Exception as exc:
            last_error = str(exc)
            print(f"[reflatten] attempt {attempt_index} failed with coherence threshold {threshold:g}: {last_error}")
    else:
        raise RuntimeError(f"Gamma reflatten failed for all thresholds: {last_error}")

    command_results.append(
        _run_reflatten_command(
            command=[
                "quad_sub",
                str(unw),
                str(diff_par),
                str(reflat_unw),
                "0",
                "0",
            ],
            stage="quad_sub",
            log_dir=log_dir,
            env=env,
            cwd=project_dir,
        )
    )
    if not reflat_unw.is_file() or reflat_unw.stat().st_size <= 0 or is_binary_all_zero(reflat_unw):
        raise RuntimeError(f"Gamma reflatten produced invalid output: {reflat_unw}")

    geo_reflat_unw = Path(outputs["geo_reflat_unw"])
    command_results.append(
        _run_reflatten_command(
            command=[
                "geocode_back",
                str(reflat_unw),
                str(width),
                str(utm_to_rdc),
                str(geo_reflat_unw),
                str(geo_width),
                str(geo_nlines),
                str(geo_interp or "0"),
                "0",
            ],
            stage="geocode_reflat_unw",
            log_dir=log_dir,
            env=env,
            cwd=project_dir,
        )
    )
    if not geo_reflat_unw.is_file() or geo_reflat_unw.stat().st_size <= 0 or is_binary_all_zero(geo_reflat_unw):
        raise RuntimeError(f"Gamma reflatten produced invalid geocoded unwrapped output: {geo_reflat_unw}")

    reflat_los = Path(outputs["reflat_los"])
    reflat_vert = Path(outputs["reflat_vert"])
    hgt_arg = str(rdc_dem) if rdc_dem.is_file() else "-"
    command_results.append(
        _run_reflatten_command(
            command=["dispmap", str(reflat_unw), hgt_arg, str(slc_par), str(off_par), str(reflat_los), "0"],
            stage="dispmap_reflat_los",
            log_dir=log_dir,
            env=env,
            cwd=project_dir,
        )
    )
    command_results.append(
        _run_reflatten_command(
            command=["dispmap", str(reflat_unw), hgt_arg, str(slc_par), str(off_par), str(reflat_vert), "1"],
            stage="dispmap_reflat_vert",
            log_dir=log_dir,
            env=env,
            cwd=project_dir,
        )
    )
    if not reflat_los.is_file() or reflat_los.stat().st_size <= 0 or is_binary_all_zero(reflat_los):
        raise RuntimeError(f"Gamma reflatten produced invalid LOS displacement output: {reflat_los}")
    if not reflat_vert.is_file() or reflat_vert.stat().st_size <= 0 or is_binary_all_zero(reflat_vert):
        raise RuntimeError(f"Gamma reflatten produced invalid vertical displacement output: {reflat_vert}")
    geo_reflat_los = Path(outputs["geo_reflat_los"])
    geo_reflat_vert = Path(outputs["geo_reflat_vert"])
    command_results.append(
        _run_reflatten_command(
            command=[
                "geocode_back",
                str(reflat_los),
                str(width),
                str(utm_to_rdc),
                str(geo_reflat_los),
                str(geo_width),
                str(geo_nlines),
                str(geo_interp or "0"),
                "0",
            ],
            stage="geocode_reflat_los",
            log_dir=log_dir,
            env=env,
            cwd=project_dir,
        )
    )
    if not geo_reflat_los.is_file() or geo_reflat_los.stat().st_size <= 0 or is_binary_all_zero(geo_reflat_los):
        raise RuntimeError(f"Gamma reflatten produced invalid geocoded LOS displacement output: {geo_reflat_los}")
    command_results.append(
        _run_reflatten_command(
            command=[
                "geocode_back",
                str(reflat_vert),
                str(width),
                str(utm_to_rdc),
                str(geo_reflat_vert),
                str(geo_width),
                str(geo_nlines),
                str(geo_interp or "0"),
                "0",
            ],
            stage="geocode_reflat_vert",
            log_dir=log_dir,
            env=env,
            cwd=project_dir,
        )
    )
    if not geo_reflat_vert.is_file() or geo_reflat_vert.stat().st_size <= 0 or is_binary_all_zero(geo_reflat_vert):
        raise RuntimeError(f"Gamma reflatten produced invalid geocoded vertical displacement output: {geo_reflat_vert}")

    native_copy_dir = ensure_directory(output_dir / "reflatten")
    copied: Dict[str, str] = {}
    for name in (
        "reflat_unw",
        "reflat_trend",
        "reflat_mask",
        "geo_reflat_unw",
        "reflat_los",
        "geo_reflat_los",
        "reflat_vert",
        "geo_reflat_vert",
    ):
        source_path = Path(outputs[name])
        if source_path.is_file():
            target_path = native_copy_dir / source_path.name
            shutil.copy2(source_path, target_path)
            copied[name] = str(target_path)

    return {
        "enabled": True,
        "applied": True,
        "model": str(model or DEFAULT_REFLATTEN_MODEL).strip().lower(),
        "model_code": model_code,
        "coherence_threshold": float(selected_threshold),
        "primary_coherence_threshold": float(primary_threshold),
        "fallback_coherence_threshold": float(fallback_threshold),
        "range_step": int(range_step),
        "azimuth_step": int(azimuth_step),
        "input_unwrapped_role": "atmcor_unw" if atmcor_unw.is_file() else "unw",
        "input_unwrapped": str(unw),
        "paths": {name: outputs[name] for name in outputs if name.startswith("reflat") or name.startswith("geo_reflat")},
        "copied": copied,
        "commands": command_results,
    }


def remove_pair_derived_outputs(*, project_dir: Path, pair_name: str, master_date: str, range_looks: int) -> List[str]:
    pair_dir = project_dir / "ifgrams" / pair_name
    if not pair_dir.is_dir():
        return []

    look_text = f"{int(range_looks)}rlks"
    patterns = [
        f"{pair_name}_{look_text}.diff*",
        f"{pair_name}_{look_text}.los_disp",
        f"{pair_name}_{look_text}.atmcor.los_disp",
        f"{pair_name}_{look_text}.vert_disp",
        f"{pair_name}_{look_text}.atmcor.vert_disp",
        f"geo_{pair_name}_{look_text}.diff*",
        f"geo_{pair_name}_{look_text}.los_disp",
        f"geo_{pair_name}_{look_text}.atmcor.los_disp",
        f"geo_{pair_name}_{look_text}.vert_disp",
        f"geo_{pair_name}_{look_text}.atmcor.vert_disp",
        f"geo_{master_date}_{look_text}.amp*",
        f"geo_{master_date}_{look_text}.diff_filt.cor",
        f"geo_{master_date}_{look_text}.hgt",
        "lv_phi",
        "lv_theta",
    ]
    removed: List[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in pair_dir.glob(pattern):
            if str(path) in seen or not path.is_file():
                continue
            seen.add(str(path))
            path.unlink()
            removed.append(str(path))
    return removed


def rerun_pair_product_stages(
    *,
    project_name: str,
    project_dir: Path,
    run_root: Path,
    scratch_root: Path,
    env: Dict[str, str],
    pair_name: str,
    master_date: str,
    slave_date: str,
    range_looks: int,
    unwrap: bool,
    atmcor: bool,
    geocode: bool,
) -> Dict[str, Any]:
    print(
        f"[repair] PyINT output sanity failed; repair attempt 1/{MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS}; "
        "deleting pair derived outputs and rerunning "
        "single-pair diff/unwrap/atmcor/geocode stages."
    )
    removed = remove_pair_derived_outputs(
        project_dir=project_dir,
        pair_name=pair_name,
        master_date=master_date,
        range_looks=range_looks,
    )

    commands: List[tuple[str, List[str]]] = [
        ("diff", ["diff_gamma.py", project_name, master_date, slave_date]),
    ]
    if unwrap:
        commands.append(("unwrap", ["unwrap_gamma.py", project_name, master_date, slave_date]))
    if atmcor:
        commands.append(("atmcor", ["atm_correction_gamma.py", project_name, master_date, slave_date]))
    if geocode:
        commands.append(("geocode", ["geocode_gamma.py", project_name, pair_name]))

    log_dir = ensure_directory(run_root / "repair_pair_products")
    stage_results: List[Dict[str, Any]] = []
    for stage, command in commands:
        stdout_path = log_dir / f"{stage}.stdout.log"
        stderr_path = log_dir / f"{stage}.stderr.log"
        print(
            f"[repair] attempt 1/{MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS} running {stage}: "
            f"{' '.join(command)}"
        )
        result = run_logged(
            command,
            env=env,
            cwd=scratch_root,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            mirror_output=False,
        )
        stdout_tail = (result.stdout or "")[-2000:]
        stderr_tail = (result.stderr or "")[-2000:]
        stage_info = {
            "stage": stage,
            "command": command,
            "returncode": int(result.returncode),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        }
        stage_results.append(stage_info)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                f"PyINT pair repair attempt 1/{MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS} stage {stage} "
                f"failed with rc={result.returncode}: {detail}"
            )
        print(
            f"[repair] attempt 1/{MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS} {stage} completed; "
            f"stdout={stdout_path}, stderr={stderr_path}"
        )
        if stderr_tail.strip():
            print(f"[repair] {stage} stderr tail:\n{stderr_tail.strip()}")

    return {
        "attempted": True,
        "attempt_count": 1,
        "max_attempts": MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS,
        "removed_outputs": removed,
        "stages": stage_results,
    }


def collect_stage_error_logs(project_dir: Path) -> Dict[str, str]:
    logs: Dict[str, str] = {}
    for filename in (
        "coreg_gamma_all.err",
        "diff_gamma_all.err",
        "unwrap_gamma_all.err",
        "atm_correction_gamma_all.err",
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


def read_gamma_par_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            key, value = parts
        values[key.strip()] = value.strip()
    return values


def _first_gamma_token(values: Dict[str, str], key: str) -> str:
    value = str(values.get(key) or "").strip()
    return value.split()[0] if value.split() else ""


def _gamma_par_int(values: Dict[str, str], key: str) -> int:
    token = _first_gamma_token(values, key)
    return int(float(token)) if token else 0


def _gamma_par_float(values: Dict[str, str], key: str) -> float | None:
    token = _first_gamma_token(values, key)
    if not token:
        return None
    try:
        return float(token)
    except ValueError:
        return None


def collect_gamma_grid_metadata(dem_par: Path, *, target_grid_size_m: int) -> Dict[str, Any]:
    values = read_gamma_par_file(dem_par)
    width = _gamma_par_int(values, "width")
    nlines = _gamma_par_int(values, "nlines")
    corner_lat = _gamma_par_float(values, "corner_lat")
    corner_lon = _gamma_par_float(values, "corner_lon")
    post_lat = _gamma_par_float(values, "post_lat")
    post_lon = _gamma_par_float(values, "post_lon")
    mid_lat = None
    if corner_lat is not None and post_lat is not None and nlines > 0:
        mid_lat = corner_lat + post_lat * (nlines - 1) / 2.0

    lat_spacing_m = abs(post_lat) * 111_320.0 if post_lat is not None else None
    lon_spacing_m = None
    if post_lon is not None:
        scale_lat = mid_lat if mid_lat is not None else corner_lat
        cos_lat = math.cos(math.radians(scale_lat or 0.0))
        lon_spacing_m = abs(post_lon) * 111_320.0 * max(abs(cos_lat), 0.01)

    average_spacing_m = None
    if lat_spacing_m is not None and lon_spacing_m is not None:
        average_spacing_m = (lat_spacing_m + lon_spacing_m) / 2.0
    elif lat_spacing_m is not None:
        average_spacing_m = lat_spacing_m
    elif lon_spacing_m is not None:
        average_spacing_m = lon_spacing_m

    mismatch_ratio = None
    if target_grid_size_m > 0 and average_spacing_m is not None:
        mismatch_ratio = abs(average_spacing_m - float(target_grid_size_m)) / float(target_grid_size_m)

    return {
        "dem_par": str(dem_par),
        "projection": _first_gamma_token(values, "DEM_projection"),
        "epsg": _first_gamma_token(values, "EPSG"),
        "data_format": _first_gamma_token(values, "data_format"),
        "width": width,
        "nlines": nlines,
        "corner_lat": corner_lat,
        "corner_lon": corner_lon,
        "post_lat_deg": post_lat,
        "post_lon_deg": post_lon,
        "mid_lat": mid_lat,
        "pixel_spacing_lat_m": lat_spacing_m,
        "pixel_spacing_lon_m": lon_spacing_m,
        "average_pixel_spacing_m": average_spacing_m,
        "target_grid_size_m": int(target_grid_size_m or 0),
        "target_grid_mismatch_ratio": mismatch_ratio,
    }


def _import_numpy() -> Any:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise RuntimeError("numpy is required for Gamma quality statistics.") from exc
    return np


def _select_gamma_float_array(
    path: Path,
    *,
    expected_count: int,
    kind: str,
) -> Tuple[Any, str, Dict[str, Any]]:
    if expected_count <= 0:
        raise RuntimeError(f"Cannot read Gamma float data without valid raster dimensions: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Gamma float source not found: {path}")
    expected_bytes = expected_count * 4
    actual_bytes = path.stat().st_size
    if actual_bytes < expected_bytes:
        raise RuntimeError(
            f"Gamma float source is smaller than expected: {path} "
            f"({actual_bytes} < {expected_bytes} bytes)"
        )

    np = _import_numpy()
    best_array: Any = None
    best_dtype = ""
    best_info: Dict[str, Any] = {}
    best_score = -1.0
    for dtype_text in (">f4", "<f4"):
        array = np.fromfile(str(path), dtype=np.dtype(dtype_text), count=expected_count)
        if array.size != expected_count:
            continue
        finite = np.isfinite(array)
        if kind == "coherence":
            plausible = finite & (array >= 0.0) & (array <= 1.0)
        else:
            plausible = finite & (np.abs(array) < 1000.0)
        score = float(np.count_nonzero(plausible)) / float(expected_count)
        info = {
            "dtype": dtype_text,
            "plausible_percent": score * 100.0,
            "finite_percent": (float(np.count_nonzero(finite)) / float(expected_count)) * 100.0,
            "size_bytes": int(actual_bytes),
        }
        if score > best_score:
            best_score = score
            best_array = array
            best_dtype = dtype_text
            best_info = info

    if best_array is None:
        raise RuntimeError(f"Unable to read Gamma float source with a plausible byte order: {path}")
    if kind == "coherence" and best_score < 0.80:
        raise RuntimeError(f"Gamma coherence source has implausible float values: {path}")
    if kind != "coherence" and best_score < 0.50:
        raise RuntimeError(f"Gamma displacement source has implausible float values: {path}")
    return best_array, best_dtype, best_info


def _float_stats(values: Any, *, total_count: int) -> Dict[str, Any]:
    np = _import_numpy()
    if values is None:
        values = np.asarray([], dtype=np.float32)
    finite_values = values[np.isfinite(values)]
    count = int(finite_values.size)
    result: Dict[str, Any] = {
        "count": count,
        "total_count": int(total_count),
        "valid_percent": (float(count) / float(total_count) * 100.0) if total_count > 0 else 0.0,
    }
    if count == 0:
        result.update(
            {
                "min": None,
                "max": None,
                "mean": None,
                "std": None,
                "p02": None,
                "p50": None,
                "p98": None,
            }
        )
        return result
    result.update(
        {
            "min": float(np.min(finite_values)),
            "max": float(np.max(finite_values)),
            "mean": float(np.mean(finite_values)),
            "std": float(np.std(finite_values)),
            "p02": float(np.percentile(finite_values, 2)),
            "p50": float(np.percentile(finite_values, 50)),
            "p98": float(np.percentile(finite_values, 98)),
        }
    )
    return result


def create_gamma_quality_report(
    *,
    disp_source: Path,
    coh_source: Path,
    dem_par: Path,
    pair_name: str,
    master_date: str,
    range_looks: int,
    azimuth_looks: int,
    target_grid_size_m: int,
    coherence_threshold: float,
    reference_mode: str,
    reference_coh_threshold: float,
    deramp_mode: str,
    deramp_coh_threshold: float,
    product_source_dir: Path,
) -> Dict[str, Any]:
    np = _import_numpy()
    grid = collect_gamma_grid_metadata(dem_par, target_grid_size_m=target_grid_size_m)
    width = int(grid.get("width") or 0)
    nlines = int(grid.get("nlines") or 0)
    total_count = width * nlines
    if total_count <= 0:
        raise RuntimeError(f"Gamma DEM parameter file does not contain usable width/nlines: {dem_par}")

    disp, disp_dtype, disp_read = _select_gamma_float_array(
        disp_source,
        expected_count=total_count,
        kind="displacement",
    )
    coh, coh_dtype, coh_read = _select_gamma_float_array(
        coh_source,
        expected_count=total_count,
        kind="coherence",
    )
    disp = disp.reshape((nlines, width))
    coh = coh.reshape((nlines, width))

    disp_valid = np.isfinite(disp) & (np.abs(disp) < 1000.0) & (disp != 0.0)
    coh_valid = np.isfinite(coh) & (coh > 0.0) & (coh <= 1.0)
    product_support = disp_valid & coh_valid & (coh >= float(coherence_threshold))
    coherence_valid_count = int(np.count_nonzero(coh_valid))
    threshold_support: Dict[str, Any] = {}
    for threshold in QUALITY_COHERENCE_THRESHOLDS:
        key = f"ge_{threshold:.2f}"
        count = int(np.count_nonzero(coh_valid & (coh >= threshold)))
        threshold_support[key] = {
            "count": count,
            "percent_of_valid_coherence": (
                (float(count) / float(coherence_valid_count)) * 100.0
                if coherence_valid_count > 0
                else 0.0
            ),
            "percent_of_raster": (float(count) / float(total_count)) * 100.0,
        }

    raw_disp_stats = _float_stats(disp[disp_valid], total_count=total_count)
    product_support_stats = _float_stats(disp[product_support], total_count=total_count)
    coherence_stats = _float_stats(coh[coh_valid], total_count=total_count)
    flags: List[Dict[str, Any]] = []
    coherence_mean = coherence_stats.get("mean")
    if isinstance(coherence_mean, (int, float)) and float(coherence_mean) < 0.40:
        flags.append(
            {
                "code": "low_mean_coherence",
                "level": "warning",
                "value": float(coherence_mean),
                "threshold": 0.40,
                "message": "Mean valid coherence is below the production review threshold.",
            }
        )
    if float(product_support_stats.get("valid_percent") or 0.0) < 40.0:
        flags.append(
            {
                "code": "low_coherence_support",
                "level": "warning",
                "value": float(product_support_stats.get("valid_percent") or 0.0),
                "threshold": 40.0,
                "message": "Less than 40 percent of raster pixels meet the configured coherence support threshold.",
            }
        )
    mismatch_ratio = grid.get("target_grid_mismatch_ratio")
    if isinstance(mismatch_ratio, (int, float)) and float(mismatch_ratio) > GRID_MISMATCH_TOLERANCE:
        flags.append(
            {
                "code": "geocoded_grid_mismatch",
                "level": "info",
                "value": float(mismatch_ratio),
                "threshold": GRID_MISMATCH_TOLERANCE,
                "message": "Actual Gamma geocoded spacing differs from the requested target grid.",
            }
        )
    if not str(grid.get("epsg") or "").strip():
        flags.append(
            {
                "code": "missing_epsg",
                "level": "info",
                "message": "Gamma DEM parameter file does not carry an EPSG code.",
            }
        )

    return {
        "pair_name": pair_name,
        "master_date": master_date,
        "production_mode": "gamma_native",
        "python_data_processing_applied": False,
        "range_looks": int(range_looks),
        "azimuth_looks": int(azimuth_looks),
        "target_grid_size_m": int(target_grid_size_m or 0),
        "coherence_quality_threshold": float(coherence_threshold),
        "coherence_support_threshold": float(coherence_threshold),
        "generated_sources": {},
        "sources": {
            "displacement": str(disp_source),
            "coherence": str(coh_source),
            "dem_par": str(dem_par),
        },
        "byte_order_detection": {
            "displacement": {**disp_read, "selected_dtype": disp_dtype},
            "coherence": {**coh_read, "selected_dtype": coh_dtype},
        },
        "grid": grid,
        "coherence": {
            "valid_stats": coherence_stats,
            "threshold_support": threshold_support,
        },
        "reference": {
            "mode": str(reference_mode or "none").strip().lower() or "none",
            "applied": False,
            "reason": "not_applied_in_python_layer",
            "selection_threshold": float(reference_coh_threshold),
        },
        "deramp": {
            "mode": str(deramp_mode or "none").strip().lower() or "none",
            "applied": False,
            "reason": "not_applied_in_python_layer",
            "selection_threshold": float(deramp_coh_threshold),
        },
        "displacement": {
            "raw_valid_stats": raw_disp_stats,
            "coherence_support_stats": product_support_stats,
            "raw_valid_percent": float(raw_disp_stats.get("valid_percent") or 0.0),
            "coherence_support_percent": float(product_support_stats.get("valid_percent") or 0.0),
        },
        "quality_flags": flags,
    }


def _run_data2geotiff(
    *,
    dem_par: Path,
    source_file: Path,
    target_file: Path,
    nodata_value: float,
    env: Dict[str, str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> Dict[str, Any]:
    if not dem_par.is_file():
        raise FileNotFoundError(f"Gamma DEM parameter file not found: {dem_par}")
    if not source_file.is_file():
        raise FileNotFoundError(f"Gamma geocoded source file not found: {source_file}")

    ensure_directory(target_file.parent)
    if target_file.exists():
        target_file.unlink()

    result = run_logged(
        [
            "data2geotiff",
            str(dem_par),
            str(source_file),
            "2",
            str(target_file),
            format_gamma_number(nodata_value),
        ],
        env=env,
        cwd=cwd,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"data2geotiff failed for {source_file.name} with rc={result.returncode}: {detail}"
        )
    if not target_file.is_file() or target_file.stat().st_size <= 0:
        raise RuntimeError(f"data2geotiff did not create a usable output: {target_file}")

    return {
        "source": str(source_file),
        "target": str(target_file),
        "dem_par": str(dem_par),
        "nodata_value": float(nodata_value),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "size_bytes": int(target_file.stat().st_size),
    }


def _write_gamma_zero_as_nodata_source(
    *,
    source_file: Path,
    target_file: Path,
    expected_count: int,
    nodata_value: float,
    kind: str,
) -> Dict[str, Any]:
    np = _import_numpy()
    array, dtype_text, read_info = _select_gamma_float_array(
        source_file,
        expected_count=expected_count,
        kind=kind,
    )
    mask = np.isfinite(array) & (array == 0.0)
    replacement_count = int(np.count_nonzero(mask))
    ensure_directory(target_file.parent)
    output = np.array(array, dtype=np.dtype(dtype_text), copy=True)
    output[mask] = np.array(nodata_value, dtype=np.dtype(dtype_text))
    output.tofile(str(target_file))
    return {
        "enabled": True,
        "source": str(source_file),
        "target": str(target_file),
        "dtype": dtype_text,
        "read": read_info,
        "zero_count": replacement_count,
        "total_count": int(expected_count),
        "zero_percent": (float(replacement_count) / float(expected_count)) * 100.0 if expected_count > 0 else 0.0,
        "nodata_value": float(nodata_value),
    }


def _run_gamma_native_geotiff_export(
    *,
    dem_par: Path,
    source_file: Path,
    target_file: Path,
    width: int,
    nlines: int,
    nodata_value: float,
    nodata_source_dir: Path,
    log_dir: Path,
    log_name: str,
    env: Dict[str, str],
    cwd: Path,
    replace_zero_with_nodata: bool = False,
) -> Dict[str, Any]:
    export_source = source_file
    zero_to_nodata: Dict[str, Any] = {
        "enabled": False,
        "reason": "disabled_for_product",
        "nodata_value": float(nodata_value),
    }
    if replace_zero_with_nodata:
        export_source = nodata_source_dir / f"{source_file.name}.zero_as_nodata"
        zero_to_nodata = _write_gamma_zero_as_nodata_source(
            source_file=source_file,
            target_file=export_source,
            expected_count=int(width) * int(nlines),
            nodata_value=nodata_value,
            kind="coherence" if "coh" in log_name.lower() else "displacement",
        )
    geotiff_result = _run_data2geotiff(
        dem_par=dem_par,
        source_file=export_source,
        target_file=target_file,
        nodata_value=nodata_value,
        env=env,
        cwd=cwd,
        stdout_path=log_dir / f"data2geotiff_{log_name}.stdout.log",
        stderr_path=log_dir / f"data2geotiff_{log_name}.stderr.log",
    )
    return {
        **geotiff_result,
        "production_mode": "gamma_native",
        "original_source": str(source_file),
        "export_source": str(export_source),
        "zero_to_nodata": zero_to_nodata,
    }


def _run_optional_gamma_native_geotiff_export(
    *,
    dem_par: Path,
    source_file: Path,
    target_file: Path,
    width: int,
    nlines: int,
    nodata_value: float,
    nodata_source_dir: Path,
    log_dir: Path,
    log_name: str,
    env: Dict[str, str],
    cwd: Path,
    replace_zero_with_nodata: bool = False,
) -> Dict[str, Any]:
    if not source_file.is_file():
        return {
            "enabled": False,
            "reason": "source_missing",
            "source": str(source_file),
            "target": str(target_file),
        }
    try:
        result = _run_gamma_native_geotiff_export(
            dem_par=dem_par,
            source_file=source_file,
            target_file=target_file,
            width=width,
            nlines=nlines,
            nodata_value=nodata_value,
            nodata_source_dir=nodata_source_dir,
            log_dir=log_dir,
            log_name=log_name,
            env=env,
            cwd=cwd,
            replace_zero_with_nodata=replace_zero_with_nodata,
        )
    except Exception as exc:
        return {
            "enabled": False,
            "reason": "export_failed",
            "error": str(exc),
            "source": str(source_file),
            "target": str(target_file),
        }
    return {"enabled": True, **result}


def _copy_product_alias(source: Dict[str, Any], target_file: Path, *, alias_of: str) -> Dict[str, Any]:
    source_target = Path(str(source.get("target") or ""))
    if not source_target.is_file():
        raise FileNotFoundError(f"Cannot create product alias, source GeoTIFF is missing: {source_target}")
    ensure_directory(target_file.parent)
    if target_file.exists():
        target_file.unlink()
    shutil.copy2(source_target, target_file)
    return {
        **source,
        "target": str(target_file),
        "size_bytes": int(target_file.stat().st_size),
        "compatibility_alias_of": alias_of,
    }


def export_standard_products(
    *,
    project_dir: Path,
    output_dir: Path,
    pair_name: str,
    master_date: str,
    range_looks: int,
    azimuth_looks: int,
    target_grid_size_m: int,
    coherence_mask_threshold: float,
    reference_mode: str,
    reference_coh_threshold: float,
    deramp_mode: str,
    deramp_coh_threshold: float,
    atmcor_enabled: bool,
    atmcor_use_for_disp: bool,
    reflatten_summary: Dict[str, Any],
    gamma_nodata_value: float,
    outputs: Dict[str, str],
    env: Dict[str, str],
    run_root: Path,
) -> Dict[str, Any]:
    run_dir = output_dir.parent if output_dir.name.lower() == "native" else output_dir
    assets_dir = run_dir / "assets"
    disp_path = assets_dir / "disp" / "disp.tif"
    disp_unmasked_path = assets_dir / "disp" / "disp_unmasked.tif"
    coh_path = assets_dir / "coh" / "coh.tif"
    look_text = f"{int(range_looks)}rlks"
    dem_par = project_dir / "DEM" / f"{master_date}_{look_text}.utm.dem.par"

    reflatten_applied = bool((reflatten_summary or {}).get("applied"))
    disp_source = Path(str(outputs.get("geo_los") or "")).resolve()
    disp_source_role = "geo_los"
    if reflatten_applied:
        reflat_los_source = Path(str(outputs.get("geo_reflat_los") or "")).resolve()
        reflat_unw_source = Path(str(outputs.get("geo_reflat_unw") or "")).resolve()
        if reflat_los_source.is_file():
            disp_source = reflat_los_source
            disp_source_role = "geo_reflat_los"
        elif reflat_unw_source.is_file():
            disp_source = reflat_unw_source
            disp_source_role = "geo_reflat_unw"
        else:
            raise FileNotFoundError(
                "Gamma reflatten was applied but no geocoded reflattened displacement or "
                "unwrapped phase source was found."
            )
    elif atmcor_enabled and atmcor_use_for_disp:
        atmcor_los_source = Path(str(outputs.get("geo_atmcor_los") or "")).resolve()
        atmcor_unw_source = Path(str(outputs.get("geo_atmcor_unw") or "")).resolve()
        if atmcor_los_source.is_file():
            disp_source = atmcor_los_source
            disp_source_role = "geo_atmcor_los"
        elif atmcor_unw_source.is_file():
            disp_source = atmcor_unw_source
            disp_source_role = "geo_atmcor_unw"
        else:
            raise FileNotFoundError(
                "atmcor_use_for_disp is enabled but no geocoded atmospheric-corrected "
                "PyINT/Gamma displacement or unwrapped source was found."
            )
    if not disp_source.is_file():
        disp_source = Path(str(outputs.get("geo_unw") or "")).resolve()
        disp_source_role = "geo_unw"
    coh_source = Path(str(outputs.get("geo_coh") or "")).resolve()

    if not disp_source.is_file():
        raise FileNotFoundError("No geocoded PyINT displacement source found for standard export.")
    if not coh_source.is_file():
        raise FileNotFoundError("No geocoded PyINT coherence source found for standard export.")

    log_dir = ensure_directory(run_root / "standard_products")
    nodata_source_dir = ensure_directory(output_dir / "ifgrams" / pair_name)
    grid = collect_gamma_grid_metadata(dem_par, target_grid_size_m=target_grid_size_m)
    width = int(grid.get("width") or 0)
    nlines = int(grid.get("nlines") or 0)
    if width <= 0 or nlines <= 0:
        raise RuntimeError(f"Gamma DEM parameter file does not contain usable width/nlines: {dem_par}")

    quality_report = create_gamma_quality_report(
        disp_source=disp_source,
        coh_source=coh_source,
        dem_par=dem_par,
        pair_name=pair_name,
        master_date=master_date,
        range_looks=range_looks,
        azimuth_looks=azimuth_looks,
        target_grid_size_m=target_grid_size_m,
        coherence_threshold=coherence_mask_threshold,
        reference_mode=reference_mode,
        reference_coh_threshold=reference_coh_threshold,
        deramp_mode=deramp_mode,
        deramp_coh_threshold=deramp_coh_threshold,
        product_source_dir=nodata_source_dir,
    )
    quality_report["gamma_nodata_value"] = float(gamma_nodata_value)
    quality_report["export_policy"] = {
        "mode": "gamma_native",
        "python_data_processing_applied": False,
        "gamma_reflatten_applied": bool(reflatten_applied),
        "zero_to_nodata_tool": "",
        "geotiff_tool": "data2geotiff",
        "primary": (
            "gamma_reflattened_geocoded_los_displacement"
            if disp_source_role == "geo_reflat_los"
            else "gamma_reflattened_geocoded_unwrapped_phase"
            if disp_source_role == "geo_reflat_unw"
            else "gamma_geocoded_los_displacement"
        ),
        "coherence_threshold_usage": "quality_support_only",
        "display_disp_zero_to_nodata": False,
        "raw_disp_unmasked_preserved": True,
    }
    quality_report["reflatten"] = {
        "enabled": bool((reflatten_summary or {}).get("enabled")),
        "applied": bool(reflatten_applied),
        "source_role": disp_source_role,
        "summary": reflatten_summary or {},
    }
    disp_unmasked_export = _run_gamma_native_geotiff_export(
        dem_par=dem_par,
        source_file=disp_source,
        target_file=disp_unmasked_path,
        width=width,
        nlines=nlines,
        nodata_value=gamma_nodata_value,
        nodata_source_dir=nodata_source_dir,
        log_dir=log_dir,
        log_name="disp_unmasked",
        env=env,
        cwd=project_dir,
        replace_zero_with_nodata=False,
    )
    disp_export = _run_gamma_native_geotiff_export(
        dem_par=dem_par,
        source_file=disp_source,
        target_file=disp_path,
        width=width,
        nlines=nlines,
        nodata_value=gamma_nodata_value,
        nodata_source_dir=nodata_source_dir,
        log_dir=log_dir,
        log_name="disp",
        env=env,
        cwd=project_dir,
        replace_zero_with_nodata=False,
    )
    coh_export = _run_gamma_native_geotiff_export(
        dem_par=dem_par,
        source_file=coh_source,
        target_file=coh_path,
        width=width,
        nlines=nlines,
        nodata_value=gamma_nodata_value,
        nodata_source_dir=nodata_source_dir,
        log_dir=log_dir,
        log_name="coh",
        env=env,
        cwd=project_dir,
        replace_zero_with_nodata=False,
    )
    optional_exports = {
        "disp_vertical": _run_optional_gamma_native_geotiff_export(
            dem_par=dem_par,
            source_file=Path(str(outputs.get("geo_vert") or "")).resolve(),
            target_file=assets_dir / "disp" / "disp_vertical.tif",
            width=width,
            nlines=nlines,
            nodata_value=gamma_nodata_value,
            nodata_source_dir=nodata_source_dir,
            log_dir=log_dir,
            log_name="disp_vertical",
            env=env,
            cwd=project_dir,
            replace_zero_with_nodata=False,
        ),
        "disp_vertical_atmcor": _run_optional_gamma_native_geotiff_export(
            dem_par=dem_par,
            source_file=Path(str(outputs.get("geo_atmcor_vert") or "")).resolve(),
            target_file=assets_dir / "disp" / "disp_vertical_atmcor.tif",
            width=width,
            nlines=nlines,
            nodata_value=gamma_nodata_value,
            nodata_source_dir=nodata_source_dir,
            log_dir=log_dir,
            log_name="disp_vertical_atmcor",
            env=env,
            cwd=project_dir,
            replace_zero_with_nodata=False,
        ),
        "wrapped_phase": _run_optional_gamma_native_geotiff_export(
            dem_par=dem_par,
            source_file=Path(str(outputs.get("geo_wrapped_phase") or "")).resolve(),
            target_file=assets_dir / "phase" / "wrapped_phase.tif",
            width=width,
            nlines=nlines,
            nodata_value=gamma_nodata_value,
            nodata_source_dir=nodata_source_dir,
            log_dir=log_dir,
            log_name="wrapped_phase",
            env=env,
            cwd=project_dir,
            replace_zero_with_nodata=False,
        ),
        "look_vector_theta": _run_optional_gamma_native_geotiff_export(
            dem_par=dem_par,
            source_file=Path(str(outputs.get("look_vector_theta") or "")).resolve(),
            target_file=assets_dir / "look_vector" / "theta.tif",
            width=width,
            nlines=nlines,
            nodata_value=gamma_nodata_value,
            nodata_source_dir=nodata_source_dir,
            log_dir=log_dir,
            log_name="look_vector_theta",
            env=env,
            cwd=project_dir,
            replace_zero_with_nodata=False,
        ),
        "look_vector_phi": _run_optional_gamma_native_geotiff_export(
            dem_par=dem_par,
            source_file=Path(str(outputs.get("look_vector_phi") or "")).resolve(),
            target_file=assets_dir / "look_vector" / "phi.tif",
            width=width,
            nlines=nlines,
            nodata_value=gamma_nodata_value,
            nodata_source_dir=nodata_source_dir,
            log_dir=log_dir,
            log_name="look_vector_phi",
            env=env,
            cwd=project_dir,
            replace_zero_with_nodata=False,
        ),
    }
    primary_kind_by_role = {
        "geo_reflat_los": "gamma_reflattened_geocoded_los_displacement",
        "geo_reflat_unw": "gamma_reflattened_geocoded_unwrapped_phase",
        "geo_los": "gamma_geocoded_los_displacement",
        "geo_atmcor_los": "gamma_atmcor_geocoded_los_displacement",
        "geo_atmcor_unw": "gamma_atmcor_geocoded_unwrapped_phase",
        "geo_unw": "gamma_geocoded_unwrapped_phase",
    }
    primary_kind = primary_kind_by_role.get(disp_source_role, "gamma_geocoded_unwrapped_phase")
    quality_report["export_policy"]["primary"] = primary_kind
    quality_report["export_policy"]["zero_to_nodata"] = disp_export.get("zero_to_nodata", {})
    quality_report["exports"] = {
        "disp": {
            "target": disp_export.get("target"),
            "source": disp_export.get("source"),
            "original_source": disp_export.get("original_source"),
            "export_source": disp_export.get("export_source"),
            "zero_to_nodata": disp_export.get("zero_to_nodata"),
        },
        "disp_unmasked": {
            "target": disp_unmasked_export.get("target"),
            "source": disp_unmasked_export.get("source"),
            "original_source": disp_unmasked_export.get("original_source"),
            "export_source": disp_unmasked_export.get("export_source"),
            "zero_to_nodata": disp_unmasked_export.get("zero_to_nodata"),
        },
        "coh": {
            "target": coh_export.get("target"),
            "source": coh_export.get("source"),
            "original_source": coh_export.get("original_source"),
            "export_source": coh_export.get("export_source"),
            "zero_to_nodata": coh_export.get("zero_to_nodata"),
        },
    }
    quality_dir = ensure_directory(run_dir / "quality")
    quality_report_path = write_text(
        quality_dir / "quality_report.json",
        json.dumps(quality_report, ensure_ascii=True, indent=2) + "\n",
    )

    return {
        "enabled": True,
        "run_dir": str(run_dir),
        "assets_dir": str(assets_dir),
        "production_mode": "gamma_native",
        "python_data_processing_applied": False,
        "gamma_reflatten_applied": bool(reflatten_applied),
        "primary": "disp",
        "primary_kind": primary_kind,
        "reflatten": reflatten_summary or {},
        "atmcor": {
            "enabled": bool(atmcor_enabled),
            "use_for_disp": bool(atmcor_use_for_disp),
            "source_role": disp_source_role,
        },
        "gamma_nodata_value": float(gamma_nodata_value),
        "grid": grid,
        "coherence_quality_threshold": float(coherence_mask_threshold),
        "coherence_support_threshold": float(coherence_mask_threshold),
        "masking": {
            "enabled": False,
            "reason": "not_applied_in_python_layer",
        },
        "reference": {"enabled": False, "reason": "not_applied_in_python_layer"},
        "deramp": {"enabled": False, "reason": "not_applied_in_python_layer"},
        "disp": disp_export,
        "disp_unmasked": disp_unmasked_export,
        "coh": coh_export,
        "optional": optional_exports,
        "quality_report": str(quality_report_path),
        "quality": quality_report,
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
    dem_oversampling = calculate_dem_oversampling(
        dem_resolution_m=float(args.dem_resolution_m),
        target_grid_size_m=float(args.target_grid_size_m or 0),
        dem_lat_ovr=float(args.dem_lat_ovr or 0.0),
        dem_lon_ovr=float(args.dem_lon_ovr or 0.0),
    )
    unwrap_coh_threshold = validate_unit_interval(args.unwrap_coh_threshold, "--unwrap-coh-threshold")
    coherence_mask_threshold = validate_unit_interval(args.coherence_mask_threshold, "--coherence-mask-threshold")
    reference_mode = str(args.reference_mode or DEFAULT_REFERENCE_MODE).strip().lower() or DEFAULT_REFERENCE_MODE
    deramp_mode = str(args.deramp_mode or DEFAULT_DERAMP_MODE).strip().lower() or DEFAULT_DERAMP_MODE
    reference_coh_threshold = validate_unit_interval(args.reference_coh_threshold, "--reference-coh-threshold")
    deramp_coh_threshold = validate_unit_interval(args.deramp_coh_threshold, "--deramp-coh-threshold")
    geo_interp = str(args.geo_interp or "1").strip()
    if geo_interp not in {"0", "1"}:
        raise ValueError("--geo-interp must be 0 or 1.")
    gamma_nodata_value = float(args.gamma_nodata_value)
    if not math.isfinite(gamma_nodata_value):
        raise ValueError("--gamma-nodata-value must be a finite number.")
    reflatten_model = str(args.reflatten_model or DEFAULT_REFLATTEN_MODEL).strip().lower()
    if reflatten_model == "linear":
        reflatten_model = "plane"
    if reflatten_model not in {"plane", "quadratic"}:
        raise ValueError("--reflatten-model must be plane or quadratic.")
    reflatten_coh_threshold = validate_unit_interval(
        args.reflatten_coh_threshold,
        "--reflatten-coh-threshold",
    )
    reflatten_fallback_coh_threshold = validate_unit_interval(
        args.reflatten_fallback_coh_threshold,
        "--reflatten-fallback-coh-threshold",
    )
    reflatten_range_step = max(1, int(args.reflatten_range_step or DEFAULT_REFLATTEN_RANGE_STEP))
    reflatten_azimuth_step = max(1, int(args.reflatten_azimuth_step or DEFAULT_REFLATTEN_AZIMUTH_STEP))

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
            target_grid_size_m=int(args.target_grid_size_m or 0),
            dem_lat_ovr=dem_oversampling["dem_lat_ovr"],
            dem_lon_ovr=dem_oversampling["dem_lon_ovr"],
            unwrap_coh_threshold=unwrap_coh_threshold,
            geo_interp=geo_interp,
            atmcor=bool(args.atmcor),
            atmcor_use_for_disp=bool(args.atmcor_use_for_disp),
            reflatten=bool(args.reflatten),
            reflatten_model=reflatten_model,
            reflatten_coh_threshold=reflatten_coh_threshold,
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
    if args.gamma_env_script:
        env.update(load_shell_environment(args.gamma_env_script, env))
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

    repair_summary: Dict[str, Any] = {
        "attempted": False,
        "attempt_count": 0,
        "max_attempts": MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS,
    }
    outputs = collect_expected_outputs(pyint_project_dir, pair_name, args.range_looks)
    try:
        assert_required_outputs(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
        output_sanity_checks = collect_output_sanity_checks(
            outputs,
            unwrap=bool(args.unwrap),
            geocode=bool(args.geocode),
        )
        assert_output_sanity(output_sanity_checks)
    except RuntimeError as exc:
        print(f"[repair] initial output check failed: {exc}")
        repair_summary = rerun_pair_product_stages(
            project_name=args.project_name,
            project_dir=pyint_project_dir,
            run_root=run_root,
            scratch_root=scratch_root,
            env=env,
            pair_name=pair_name,
            master_date=master_date,
            slave_date=slave_date,
            range_looks=args.range_looks,
            unwrap=bool(args.unwrap),
            atmcor=bool(args.atmcor),
            geocode=bool(args.geocode),
        )
        outputs = collect_expected_outputs(pyint_project_dir, pair_name, args.range_looks)
        assert_required_outputs(outputs, unwrap=bool(args.unwrap), geocode=bool(args.geocode))
        output_sanity_checks = collect_output_sanity_checks(
            outputs,
            unwrap=bool(args.unwrap),
            geocode=bool(args.geocode),
        )
        try:
            assert_output_sanity(output_sanity_checks)
        except RuntimeError as repair_exc:
            raise RuntimeError(
                f"{exc}; pair repair attempt 1/{MAX_PAIR_PRODUCT_REPAIR_ATTEMPTS} was attempted "
                f"but outputs are still invalid, no further automatic repair will be attempted: {repair_exc}"
            ) from repair_exc
    stage_error_logs = collect_stage_error_logs(pyint_project_dir)

    reflatten_summary: Dict[str, Any] = {
        "enabled": bool(args.reflatten),
        "applied": False,
        "reason": "",
        "model": reflatten_model,
        "coherence_threshold": reflatten_coh_threshold,
        "fallback_coherence_threshold": reflatten_fallback_coh_threshold,
        "range_step": reflatten_range_step,
        "azimuth_step": reflatten_azimuth_step,
    }
    if bool(args.reflatten) and bool(args.unwrap):
        print(
            "[reflatten] running Gamma residual phase reflattening "
            f"model={reflatten_model}, coh={reflatten_coh_threshold:g}"
        )
        reflatten_summary = run_gamma_reflatten(
            project_dir=pyint_project_dir,
            run_root=run_root,
            output_dir=output_dir,
            outputs=outputs,
            pair_name=pair_name,
            master_date=master_date,
            range_looks=args.range_looks,
            env=env,
            model=reflatten_model,
            coherence_threshold=reflatten_coh_threshold,
            fallback_coherence_threshold=reflatten_fallback_coh_threshold,
            range_step=reflatten_range_step,
            azimuth_step=reflatten_azimuth_step,
            geo_interp=geo_interp,
        )
        outputs = collect_expected_outputs(pyint_project_dir, pair_name, args.range_looks)
        output_sanity_checks = collect_output_sanity_checks(
            outputs,
            unwrap=bool(args.unwrap),
            geocode=bool(args.geocode),
        )
        assert_output_sanity(output_sanity_checks)
    elif bool(args.reflatten):
        reflatten_summary["reason"] = "unwrap disabled"
    else:
        reflatten_summary["reason"] = "disabled"

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
    standard_products = (
        export_standard_products(
            project_dir=pyint_project_dir,
            output_dir=output_dir,
            pair_name=pair_name,
            master_date=master_date,
            range_looks=args.range_looks,
            azimuth_looks=args.azimuth_looks,
            target_grid_size_m=int(args.target_grid_size_m or 0),
            coherence_mask_threshold=coherence_mask_threshold,
            reference_mode=reference_mode,
            reference_coh_threshold=reference_coh_threshold,
            deramp_mode=deramp_mode,
            deramp_coh_threshold=deramp_coh_threshold,
            atmcor_enabled=bool(args.atmcor),
            atmcor_use_for_disp=bool(args.atmcor_use_for_disp),
            reflatten_summary=reflatten_summary,
            gamma_nodata_value=gamma_nodata_value,
            outputs=outputs,
            env=env,
            run_root=run_root,
        )
        if bool(args.geocode)
        else {
            "enabled": False,
            "reason": "geocode disabled",
        }
    )

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
            "configured_resolution_m": float(args.dem_resolution_m),
            "oversampling": dem_oversampling,
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
        "target_grid_size_m": int(args.target_grid_size_m or 0),
        "range_looks": int(args.range_looks),
        "azimuth_looks": int(args.azimuth_looks),
        "dem_resolution_m": float(args.dem_resolution_m),
        "dem_oversampling": dem_oversampling,
        "unwrap_coh_threshold": unwrap_coh_threshold,
        "coherence_quality_threshold": coherence_mask_threshold,
        "reference_mode": reference_mode,
        "reference_coh_threshold": reference_coh_threshold,
        "deramp_mode": deramp_mode,
        "deramp_coh_threshold": deramp_coh_threshold,
        "gamma_nodata_value": gamma_nodata_value,
        "geo_interp": geo_interp,
        "atmcor": bool(args.atmcor),
        "atmcor_use_for_disp": bool(args.atmcor_use_for_disp),
        "reflatten": bool(args.reflatten),
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
            "reflatten_applied": bool(reflatten_summary.get("applied")),
        },
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
        "output_repair": repair_summary,
        "reflatten_summary": reflatten_summary,
        "copied_outputs": copied_paths,
        "copied_orbit_bridge_paths": copied_orbit_bridge_paths,
        "standard_products": standard_products,
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
    write_text(summary_path, json.dumps(summary, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
