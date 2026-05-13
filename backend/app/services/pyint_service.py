"""Helpers for integrating the external PyINT workflow."""
from __future__ import annotations

import os
import re
import shlex
import math
import defusedxml.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..config import get_env_text, read_bool_env, settings
from .dinsar_naming import PAIR_META_FILENAME, build_fallback_pair_key, find_json_sidecar
from ..utils import normalize_satellite_family
from .wsl_service import run_wsl_exec


LT1_INPUT_GLOBS = ("LT1*.tar.gz", "LT1*.tiff")
S1_INPUT_GLOBS = ("S1*.zip",)
DEFAULT_RANGE_LOOKS = 2
DEFAULT_AZIMUTH_LOOKS = 2
DEFAULT_DEM_RESOLUTION_M = 30.0
DEFAULT_UNWRAP_COH_THRESHOLD = 0.05
DEFAULT_PRODUCT_COH_THRESHOLD = 0.20
DEFAULT_REFERENCE_MODE = "none"
DEFAULT_REFERENCE_COH_THRESHOLD = 0.30
DEFAULT_DERAMP_MODE = "none"
DEFAULT_DERAMP_COH_THRESHOLD = 0.30
DEFAULT_GEO_INTERP = "1"
DEFAULT_ATMCOR_ENABLED = False
DEFAULT_ATMCOR_USE_FOR_DISP = False
DEFAULT_REFLATTEN_ENABLED = True
DEFAULT_REFLATTEN_MODEL = "plane"
DEFAULT_REFLATTEN_COH_THRESHOLD = 0.70
DEFAULT_REFLATTEN_FALLBACK_COH_THRESHOLD = 0.20
DEFAULT_REFLATTEN_RANGE_STEP = 32
DEFAULT_REFLATTEN_AZIMUTH_STEP = 32
DEM_OVERSAMPLING_MIN = 0.25
DEM_OVERSAMPLING_MAX = 16.0
REFERENCE_MODE_CHOICES = {"none", "coh_median"}
DERAMP_MODE_CHOICES = {"none", "plane"}
REFLATTEN_MODEL_CHOICES = {"plane", "linear", "quadratic"}


def _read_default_target_grid_size_m() -> int:
    for name in ("PYINT_DEFAULT_TARGET_GRID_SIZE_M",):
        text = str(get_env_text(name, "") or "").strip()
        if not text:
            continue
        try:
            value = float(text)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return int(value)
    return 0


def _read_float_env(names: Iterable[str], default: float) -> float:
    for name in names:
        text = str(get_env_text(name, "") or "").strip()
        if not text:
            continue
        try:
            value = float(text)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return float(default)


DEFAULT_TARGET_GRID_SIZE_M = _read_default_target_grid_size_m()
TARGET_GRID_SIZE_MIN_M = 0
TARGET_GRID_SIZE_MAX_M = 100
DEFAULT_DEM_RESOLUTION_M = _read_float_env(("PYINT_DEM_RESOLUTION_M",), DEFAULT_DEM_RESOLUTION_M)
DEFAULT_UNWRAP_COH_THRESHOLD = _read_float_env(
    ("PYINT_UNWRAP_COH_THRESHOLD",),
    DEFAULT_UNWRAP_COH_THRESHOLD,
)
DEFAULT_PRODUCT_COH_THRESHOLD = _read_float_env(
    ("PYINT_PRODUCT_COH_THRESHOLD", "PYINT_COHERENCE_MASK_THRESHOLD"),
    DEFAULT_PRODUCT_COH_THRESHOLD,
)
DEFAULT_REFERENCE_COH_THRESHOLD = _read_float_env(
    ("PYINT_REFERENCE_COH_THRESHOLD",),
    DEFAULT_REFERENCE_COH_THRESHOLD,
)
DEFAULT_DERAMP_COH_THRESHOLD = _read_float_env(
    ("PYINT_DERAMP_COH_THRESHOLD",),
    DEFAULT_DERAMP_COH_THRESHOLD,
)
DEFAULT_PARALLEL_WORKERS = 1
MAX_LOOKS = 32
MAX_PARALLEL_WORKERS = 16

_DATE_TOKEN_RE = re.compile(r"(20\d{6})")
_SAFE_TEXT_RE = re.compile(r"[^0-9A-Za-z._-]+")


@dataclass
class PyintCheck:
    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


@dataclass
class PyintEnvironmentReport:
    overall_ok: bool
    checks: List[PyintCheck] = field(default_factory=list)
    message: str = ""


def _read_env(name: str, default: str = "") -> str:
    return get_env_text(name, default) or default


def _read_bool_env(name: str, default: bool = False) -> bool:
    return read_bool_env(name, default)


def default_gamma_env_script_windows() -> str:
    return os.path.normpath(str(Path(settings.PROJECT_ROOT, "deploy", "wsl", "profiles", "gamma_env.sh")))


def resolve_gamma_env_script(gamma_env_script: Optional[str] = None) -> str:
    explicit = str(gamma_env_script or _read_env("PYINT_GAMMA_ENV_SCRIPT", "")).strip()
    if explicit:
        return os.path.normpath(explicit)

    candidate = default_gamma_env_script_windows()
    if os.path.isfile(candidate):
        return candidate
    return ""


def normalize_date_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = _DATE_TOKEN_RE.search(re.sub(r"\D", "", text))
    if match:
        return match.group(1)
    match = _DATE_TOKEN_RE.search(text)
    if match:
        return match.group(1)
    return ""


def _local_xml_tag_name(tag: Any) -> str:
    text = str(tag or "")
    return text.split("}")[-1] if "}" in text else text


def _read_xml_first_parameter(xml_file: str, names: Iterable[str]) -> Optional[str]:
    path = os.path.normpath(str(xml_file or "").strip())
    if not path or not os.path.isfile(path):
        return None
    wanted = {str(name or "").strip().lower() for name in names if str(name or "").strip()}
    if not wanted:
        return None
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception:
        return None
    for elem in root.iter():
        local_name = _local_xml_tag_name(elem.tag).lower()
        if local_name in wanted and elem.text and str(elem.text).strip():
            return str(elem.text).strip()
    return None


def _read_scene_geometry_metadata(metadata_path: str) -> Dict[str, Any]:
    source = os.path.normpath(str(metadata_path or "").strip())
    range_spacing = _read_xml_first_parameter(
        source,
        ("PixelSpacingRg", "columnSpacing", "slantRange", "range_pixel_spacing"),
    )
    azimuth_spacing = _read_xml_first_parameter(
        source,
        ("PixelSpacingAz", "rowSpacing", "projectedSpacingAzimuth", "azimuth_pixel_spacing"),
    )
    incidence_angle = _read_xml_first_parameter(
        source,
        ("IncidenceAngle", "incidence_angle"),
    )
    if not all((range_spacing, azimuth_spacing, incidence_angle)):
        raise ValueError(f"Cannot read range/azimuth spacing and incidence angle from: {source}")
    return {
        "source": source,
        "range_pixel_spacing_m": float(range_spacing),
        "azimuth_pixel_spacing_m": float(azimuth_spacing),
        "incidence_angle_deg": float(incidence_angle),
    }


def _scene_geometry_metadata_candidates(directory: str, patterns: Iterable[str]) -> List[str]:
    root = os.path.normpath(str(directory or "").strip())
    if not root or not os.path.isdir(root):
        return []
    candidates: List[str] = []
    for pattern in patterns:
        candidates.extend(str(path) for path in Path(root).glob(pattern) if path.is_file())
    return [
        os.path.normpath(path)
        for path in sorted(
            set(candidates),
            key=lambda item: (0 if item.lower().endswith(".sml") else 1, item.lower()),
        )
    ]


def resolve_scene_geometry_metadata_files(scene_dir: str) -> List[str]:
    return _scene_geometry_metadata_candidates(
        scene_dir,
        (
            "*.sml",
            "*.SML",
            "*.meta.xml",
            "*.META.XML",
        ),
    )


def resolve_scene_geometry_metadata_file(scene_dir: str) -> str:
    candidates = resolve_scene_geometry_metadata_files(scene_dir)
    return candidates[0] if candidates else ""


def calculate_looks_from_scene_metadata(
    *,
    master_metadata: str,
    slave_metadata: str,
    target_resolution_m: float,
) -> Dict[str, Any]:
    target_resolution = float(target_resolution_m)
    if target_resolution <= 0:
        raise ValueError("target_resolution_m must be greater than 0")

    master = _read_scene_geometry_metadata(master_metadata)
    slave = _read_scene_geometry_metadata(slave_metadata)

    avg_azimuth = (
        float(master["azimuth_pixel_spacing_m"]) + float(slave["azimuth_pixel_spacing_m"])
    ) / 2.0
    master_ground_range = float(master["range_pixel_spacing_m"]) / math.sin(
        math.radians(float(master["incidence_angle_deg"]))
    )
    slave_ground_range = float(slave["range_pixel_spacing_m"]) / math.sin(
        math.radians(float(slave["incidence_angle_deg"]))
    )
    avg_ground_range = (master_ground_range + slave_ground_range) / 2.0

    range_ratio = target_resolution / avg_ground_range
    azimuth_ratio = target_resolution / avg_azimuth
    range_looks = max(1, int(math.floor(range_ratio + 0.5)))
    azimuth_looks = max(1, int(math.floor(azimuth_ratio + 0.5)))

    return {
        "mode": "target_grid_size",
        "target_resolution_m": target_resolution,
        "range_looks": range_looks,
        "azimuth_looks": azimuth_looks,
        "avg_ground_range_spacing_m": avg_ground_range,
        "avg_azimuth_spacing_m": avg_azimuth,
        "range_look_ratio": range_ratio,
        "azimuth_look_ratio": azimuth_ratio,
        "resolved_ground_range_spacing_m": avg_ground_range * range_looks,
        "resolved_azimuth_spacing_m": avg_azimuth * azimuth_looks,
        "master": master,
        "slave": slave,
    }


def calculate_looks_from_task_dir(task_dir: str, target_resolution_m: float) -> Dict[str, Any]:
    task_root = os.path.normpath(str(task_dir or "").strip())
    master_candidates = resolve_scene_geometry_metadata_files(os.path.join(task_root, "master"))
    slave_candidates = resolve_scene_geometry_metadata_files(os.path.join(task_root, "slave"))
    if not master_candidates or not slave_candidates:
        raise ValueError(f"Cannot find SML/meta XML metadata under task: {task_root}")
    errors: List[str] = []
    for master_metadata in master_candidates:
        for slave_metadata in slave_candidates:
            try:
                return calculate_looks_from_scene_metadata(
                    master_metadata=master_metadata,
                    slave_metadata=slave_metadata,
                    target_resolution_m=target_resolution_m,
                )
            except Exception as exc:
                errors.append(f"{os.path.basename(master_metadata)} + {os.path.basename(slave_metadata)}: {exc}")
    detail = "; ".join(errors[:3]) if errors else "unknown metadata parsing error"
    raise ValueError(f"Cannot calculate looks from task metadata under {task_root}: {detail}")


def calculate_dem_oversampling(
    *,
    dem_resolution_m: float,
    target_grid_size_m: float,
) -> Dict[str, Any]:
    dem_resolution = float(dem_resolution_m or 0.0)
    target_grid = float(target_grid_size_m or 0.0)
    if not math.isfinite(dem_resolution) or dem_resolution <= 0:
        dem_resolution = DEFAULT_DEM_RESOLUTION_M

    raw_factor = dem_resolution / target_grid if math.isfinite(target_grid) and target_grid > 0 else None
    oversampling = 1.0
    actual_grid = dem_resolution / oversampling if oversampling > 0 else dem_resolution
    mismatch_ratio = abs(actual_grid - target_grid) / target_grid if target_grid > 0 else None
    return {
        "mode": "gamma_dem_oversampling",
        "dem_resolution_m": dem_resolution,
        "target_grid_size_m": target_grid,
        "raw_oversampling": raw_factor,
        "oversampling": oversampling,
        "actual_grid_size_m": actual_grid,
        "mismatch_ratio": mismatch_ratio,
        "min_oversampling": DEM_OVERSAMPLING_MIN,
        "max_oversampling": DEM_OVERSAMPLING_MAX,
    }


def slugify_text(value: Any, *, default: str = "item", max_len: int = 96) -> str:
    text = _SAFE_TEXT_RE.sub("_", str(value or "").strip()).strip("._")
    if not text:
        text = default
    return text[:max_len]


def build_project_name(pair_key: str, run_key: str) -> str:
    return slugify_text(f"{pair_key}_{run_key}", default="pyint_project", max_len=120)


def build_profile_project_name(satellite_family: Any, pair_key: str, run_key: str) -> str:
    family = str(normalize_satellite_family(satellite_family) or "").strip().upper()
    if family == "S1":
        return slugify_text(f"s1_{pair_key}_{run_key}", default="s1_pyint_project", max_len=120)
    if family == "LT1":
        return slugify_text(f"lt1_{pair_key}_{run_key}", default="lt1_pyint_project", max_len=120)
    return build_project_name(pair_key, run_key)


def windows_path_to_wsl_mount(path: str) -> str:
    text = str(path or "").strip().strip('"').strip("'")
    if not text:
        return ""
    normalized = os.path.normpath(text)
    if normalized.startswith("/"):
        return normalized.replace("\\", "/")
    if normalized.startswith("\\\\"):
        return ""
    drive, tail = os.path.splitdrive(normalized)
    if not drive:
        return normalized.replace("\\", "/")
    drive_letter = drive.rstrip(":").lower()
    normalized_tail = tail.replace("\\", "/")
    return f"/mnt/{drive_letter}/{normalized_tail}"


def to_wsl_path(path: str) -> str:
    return windows_path_to_wsl_mount(path)


def quote_shell(value: str) -> str:
    return shlex.quote(str(value or ""))


def discover_lt1_archives(task_dir: str) -> Dict[str, List[str]]:
    task_path = Path(os.path.normpath(os.path.abspath(str(task_dir or "").strip())))
    result: Dict[str, List[str]] = {"master": [], "slave": []}
    for role in ("master", "slave"):
        role_dir = task_path / role
        if not role_dir.is_dir():
            continue
        inputs = []
        for pattern in LT1_INPUT_GLOBS:
            inputs.extend(
                str(path.resolve())
                for path in role_dir.rglob(pattern)
                if path.is_file()
            )
        result[role] = sorted(set(inputs))
    return result


def discover_s1_scene_sources(task_dir: str) -> Dict[str, List[str]]:
    task_path = Path(os.path.normpath(os.path.abspath(str(task_dir or "").strip())))
    pair_meta = find_json_sidecar(str(task_path), PAIR_META_FILENAME, max_levels=0) or {}
    result: Dict[str, List[str]] = {"master": [], "slave": []}
    for role in ("master", "slave"):
        explicit_path = str(pair_meta.get(f"{role}_path") or "").strip()
        role_dir = task_path / role
        candidates: List[str] = []
        if explicit_path:
            candidates.append(str(Path(explicit_path).resolve()))
        if not candidates and role_dir.is_dir() and (role_dir / "manifest.safe").is_file():
            candidates.append(str(role_dir.resolve()))
        result[role] = sorted(set(candidates))
    return result


def infer_scene_date_from_archives(paths: Iterable[str]) -> str:
    dates = {
        date_text
        for path in paths
        for date_text in [normalize_date_text(os.path.basename(path))]
        if date_text
    }
    if len(dates) == 1:
        return next(iter(dates))
    return ""


def infer_task_identity(task_dir: str) -> Dict[str, Any]:
    task_name = os.path.basename(os.path.normpath(task_dir))
    pair_meta = find_json_sidecar(task_dir, PAIR_META_FILENAME, max_levels=0) or {}
    task_alias = str(pair_meta.get("task_alias") or task_name).strip() or task_name
    master_satellite = str(pair_meta.get("master_satellite") or "").strip().upper()
    slave_satellite = str(pair_meta.get("slave_satellite") or "").strip().upper()
    satellite_family = normalize_satellite_family(master_satellite or slave_satellite)
    pair_key = str(pair_meta.get("pair_key") or "").strip() or build_fallback_pair_key(
        task_alias,
        task_dir,
        satellite_family=satellite_family,
    )
    master_date = normalize_date_text(pair_meta.get("master_imaging_date"))
    slave_date = normalize_date_text(pair_meta.get("slave_imaging_date"))
    return {
        "task_name": task_name,
        "task_alias": task_alias,
        "pair_key": pair_key,
        "pair_meta": pair_meta,
        "master_date": master_date,
        "slave_date": slave_date,
        "master_satellite": master_satellite,
        "slave_satellite": slave_satellite,
        "satellite_family": satellite_family,
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
) -> str:
    lines = [
        f"# Auto-generated for {project_name}",
        "satelite=LT",
        f"masterDate={master_date}",
        f"range_looks={int(range_looks)}",
        f"azimuth_looks={int(azimuth_looks)}",
        "download_data=0",
        "raw2slc_all=1",
        f"raw2slc_all_parallel={int(parallel_workers)}",
        "coreg_all=1",
        f"coreg_all_parallel={int(parallel_workers)}",
        "select_pairs=0",
        "diff_all=1",
        f"diff_all_parallel={int(parallel_workers)}",
        f"unwrap_all={1 if unwrap else 0}",
        f"unwrap_all_parallel={int(parallel_workers)}",
        f"geocode_all={1 if geocode else 0}",
        f"geocode_all_parallel={int(parallel_workers)}",
        "geocode_products=hyp3,licsbas",
    ]
    return "\n".join(lines) + "\n"


def validate_pyint_root_dir(root_dir: str, num_to_process: int = 0) -> Dict[str, Any]:
    normalized_root = os.path.normpath(os.path.abspath(str(root_dir or "").strip()))
    if not root_dir or not os.path.isdir(normalized_root):
        raise ValueError(f"PyINT root_dir does not exist or is not a directory: {root_dir}")

    def _missing_task_subdirs(task_dir: str) -> List[str]:
        missing: List[str] = []
        for subdir in ("master", "slave"):
            if not os.path.isdir(os.path.join(task_dir, subdir)):
                missing.append(subdir)
        return missing

    def _iter_child_dirs(directory: str):
        with os.scandir(directory) as entries:
            child_dirs = [entry for entry in entries if entry.is_dir()]
        child_dirs.sort(key=lambda entry: entry.name.lower())
        return child_dirs

    if not _missing_task_subdirs(normalized_root):
        task_dirs = [normalized_root]
        invalid_candidates: List[Dict[str, Any]] = []
        mode = "single_task_dir"
    else:
        task_dirs = []
        invalid_candidates = []
        for entry in _iter_child_dirs(normalized_root):
            if not entry.name.lower().startswith("task_"):
                continue
            missing = _missing_task_subdirs(entry.path)
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
            "PyINT root_dir must be either a single task directory containing "
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


def resolve_time_baseline_days(master_date: str, slave_date: str, pair_meta: Dict[str, Any]) -> int:
    raw_days = pair_meta.get("time_baseline_days")
    try:
        if raw_days not in (None, ""):
            return int(raw_days)
    except (TypeError, ValueError):
        pass

    if not master_date or not slave_date:
        return 0
    try:
        master_dt = datetime.strptime(master_date, "%Y%m%d")
        slave_dt = datetime.strptime(slave_date, "%Y%m%d")
    except ValueError:
        return 0
    return (slave_dt - master_dt).days


def _gamma_prefix(gamma_env_script_wsl: str) -> str:
    script = str(gamma_env_script_wsl or "").strip()
    if not script:
        return ""
    return f". {quote_shell(script)} >/dev/null 2>&1 || exit 1; "


def _pyint_path_prefix(pyint_home_wsl: str) -> str:
    home = str(pyint_home_wsl or "").strip().rstrip("/")
    if not home:
        return ""
    return f"export PATH={quote_shell(home + '/pyint')}:\"$PATH\" && "


def check_pyint_environment(
    *,
    enabled: Optional[bool] = None,
    distro: Optional[str] = None,
    python_cmd: Optional[str] = None,
    pyint_home: Optional[str] = None,
    pyint_app_script: Optional[str] = None,
    template_root: Optional[str] = None,
    work_root: Optional[str] = None,
    output_root: Optional[str] = None,
    dem_root: Optional[str] = None,
    gamma_env_script: Optional[str] = None,
    smoke_test: Optional[bool] = None,
) -> PyintEnvironmentReport:
    enabled_value = _read_bool_env("PYINT_ENABLED", False) if enabled is None else bool(enabled)
    if not enabled_value:
        return PyintEnvironmentReport(
            overall_ok=False,
            checks=[PyintCheck(name="PYINT_ENABLED", ok=False, detail="PYINT_ENABLED=false")],
            message="PyINT is disabled. Set PYINT_ENABLED=true to enable it.",
        )

    distro_value = str(distro or _read_env("PYINT_WSL_DISTRO", settings.ISCE2_WSL_DISTRO)).strip()
    python_value = str(python_cmd or _read_env("PYINT_WSL_PYTHON", settings.ISCE2_PYTHON)).strip()
    pyint_home_wsl = to_wsl_path(str(pyint_home or _read_env("PYINT_HOME", "")))
    pyint_app_wsl = to_wsl_path(str(pyint_app_script or _read_env("PYINT_APP_SCRIPT", "")))
    template_root_wsl = to_wsl_path(str(template_root or _read_env("PYINT_TEMPLATE_ROOT", "")))
    work_root_wsl = to_wsl_path(str(work_root or _read_env("PYINT_WORK_ROOT", "")))
    output_root_wsl = to_wsl_path(str(output_root or _read_env("PYINT_OUTPUT_ROOT", "")))
    dem_root_wsl = to_wsl_path(str(dem_root or _read_env("PYINT_DEM_ROOT", "")))
    gamma_env_wsl = to_wsl_path(resolve_gamma_env_script(gamma_env_script))
    smoke_enabled = _read_bool_env("PYINT_SMOKE_TEST_ENABLED", False) if smoke_test is None else bool(smoke_test)
    precise_orbit_enabled = _read_bool_env("PYINT_LT1_PRECISE_ORBIT_ENABLED", True)

    checks: List[PyintCheck] = []

    def add(name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
        checks.append(PyintCheck(name=name, ok=ok, detail=detail, skipped=skipped))

    def run_check(command: str, timeout: int = 30):
        return run_wsl_exec(["bash", "-lc", command], distro=distro_value, timeout=timeout)

    rc, out, err = run_check("echo pyint_alive", timeout=15)
    wsl_ok = rc == 0 and "pyint_alive" in out
    add("WSL distro", wsl_ok, out or err or distro_value)

    if not wsl_ok:
        return PyintEnvironmentReport(
            overall_ok=False,
            checks=checks,
            message=f"WSL distro is unavailable: {distro_value}",
        )

    rc, out, err = run_check(
        f"{quote_shell(python_value)} --version",
        timeout=15,
    )
    add("WSL Python", rc == 0, out or err or python_value)

    if pyint_home_wsl:
        rc, out, err = run_check(
            f"test -d {quote_shell(pyint_home_wsl)} && echo ok",
            timeout=10,
        )
        add("PYINT_HOME", rc == 0 and "ok" in out, pyint_home_wsl or err)
    else:
        add("PYINT_HOME", False, "PYINT_HOME is empty")

    if pyint_app_wsl:
        rc, out, err = run_check(
            f"test -f {quote_shell(pyint_app_wsl)} && echo ok",
            timeout=10,
        )
        add("pyintApp.py", rc == 0 and "ok" in out, pyint_app_wsl or err)
    else:
        add("pyintApp.py", False, "PYINT_APP_SCRIPT is empty")

    for name, path_text in (
        ("PYINT_TEMPLATE_ROOT", template_root_wsl),
        ("PYINT_WORK_ROOT", work_root_wsl),
        ("PYINT_OUTPUT_ROOT", output_root_wsl),
        ("PYINT_DEM_ROOT", dem_root_wsl),
    ):
        if not path_text:
            add(name, False, f"{name} is empty")
            continue
        rc, out, err = run_check(
            f"test -d {quote_shell(path_text)} && test -w {quote_shell(path_text)} && echo ok",
            timeout=10,
        )
        add(name, rc == 0 and "ok" in out, path_text or err)

    if gamma_env_wsl:
        rc, out, err = run_check(
            f"test -f {quote_shell(gamma_env_wsl)} && echo ok",
            timeout=10,
        )
        add("GAMMA env script", rc == 0 and "ok" in out, gamma_env_wsl or err)
    else:
        add("GAMMA env script", True, "Not configured; using current PATH", skipped=True)

    gamma_prefix = _gamma_prefix(gamma_env_wsl)
    pyint_prefix = _pyint_path_prefix(pyint_home_wsl)
    for name, command_name in (
        ("GAMMA LT1 import", "LT1_import_SLC_from_zipfiles1"),
        ("GAMMA geocode_back", "geocode_back"),
    ):
        rc, out, err = run_check(
            gamma_prefix + pyint_prefix + f"command -v {quote_shell(command_name)} >/dev/null 2>&1 && echo ok",
            timeout=10,
        )
        add(name, rc == 0 and "ok" in out, out or err or command_name)

    helper_path = (
        Path(__file__).resolve().parent.parent
        / "pyint_pipeline"
        / "apply_lt1_precise_orbit.py"
    )
    if precise_orbit_enabled:
        add("LT1 precise orbit bridge helper", helper_path.is_file(), str(helper_path))
    else:
        add("LT1 precise orbit bridge helper", True, "Skipped", skipped=True)

    if smoke_enabled:
        smoke_cmd = (
            f"export PYTHONPATH={quote_shell(pyint_home_wsl)}:$PYTHONPATH && "
            + gamma_prefix
            + f"{quote_shell(python_value)} {quote_shell(pyint_app_wsl)} -h >/dev/null"
        )
        rc, out, err = run_check(smoke_cmd, timeout=60)
        add("PyINT smoke test", rc == 0, out or err or "pyintApp.py -h")
    else:
        add("PyINT smoke test", True, "Skipped", skipped=True)

    required_checks = [check for check in checks if not check.skipped]
    overall_ok = all(check.ok for check in required_checks)
    failed_names = [check.name for check in required_checks if not check.ok]
    message = "All PyINT checks passed." if overall_ok else f"Failed checks: {', '.join(failed_names)}"
    return PyintEnvironmentReport(overall_ok=overall_ok, checks=checks, message=message)
