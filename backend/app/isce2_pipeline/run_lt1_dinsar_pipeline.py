#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from export_isce_geotiff import (
    DEFAULT_DERAMP_COH_THRESHOLD,
    DEFAULT_DERAMP_MODE,
    DEFAULT_REFERENCE_COH_THRESHOLD,
    DEFAULT_REFERENCE_MODE,
    DEFAULT_WAVELENGTH,
    DERAMP_MODE_CHOICES,
    REFERENCE_MODE_CHOICES,
    export_products,
)
from lt1_input_resolver import (
    DEFAULT_WSL_DEM_CANDIDATES,
    ensure_lt1_orbit_xml,
    repair_related_dem_sidecars,
    resolve_prepared_dem_path,
)


DEFAULT_TARGET_GRID_SIZE_M = 10
METERS_PER_DEGREE = 111320.0
LARGE_BASE_DEM_PIXEL_THRESHOLD = 200_000_000
PIPELINE_STAGE_ORDER = ("filter", "unwrap", "geocode", "export")
RESUME_STAGE_CHOICES = PIPELINE_STAGE_ORDER[1:]
DEFAULT_EXPORT_GEOCODE_PRODUCTS = [
    "interferogram/filt_topophase.unw",
    "interferogram/topophase.cor",
    "ionosphere/dispersive.bil.unwCor.filt",
    "ionosphere/nondispersive.bil.unwCor.filt",
    "ionosphere/mask.bil",
]
DEFAULT_EXPORT_GEOCODE_PRODUCTS_NO_IONO = [
    "interferogram/filt_topophase.unw",
    "interferogram/topophase.cor",
]
DEFAULT_RUBBER_SHEET_SNR_THRESHOLD = 5.0
DEFAULT_RUBBER_SHEET_FILTER_SIZE = 9
DEFAULT_DENSE_WINDOW_WIDTH = 64
DEFAULT_DENSE_WINDOW_HEIGHT = 64
DEFAULT_DENSE_SEARCH_WIDTH = 20
DEFAULT_DENSE_SEARCH_HEIGHT = 20
DEFAULT_DENSE_SKIP_WIDTH = 32
DEFAULT_DENSE_SKIP_HEIGHT = 32


@dataclass
class Scene:
    role: str
    tiff_path: Path
    meta_path: Path
    date_yyyymmdd: str
    satellite: str
    orbit_xml_path: Path


@dataclass
class PipelineConfig:
    task_name: str
    output_prefix: str
    dem_path: Path
    reference: Scene
    secondary: Scene
    bbox: list[float] | None
    target_grid_size_m: int
    geo_posting_deg: float
    geocode_products: list[str] | None
    ionosphere_correction: bool
    dense_offsets: bool
    rubbersheet_range: bool
    rubbersheet_azimuth: bool
    rubber_sheet_snr_threshold: float
    rubber_sheet_filter_size: int
    dense_window_width: int
    dense_window_height: int
    dense_search_width: int
    dense_search_height: int
    dense_skip_width: int
    dense_skip_height: int


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent

    parser = argparse.ArgumentParser(
        description="Run an LT-1 ISCE2 DInSAR production pipeline with the standard stripmap workflow."
    )
    parser.add_argument("task_dir", help="Task directory, for example Task_20250112_20250309")
    parser.add_argument(
        "--task-name",
        default=None,
        help="Override the task name used for work directory and default outputs",
    )
    parser.add_argument(
        "--work-root",
        default=str(script_dir / "jobs"),
        help="Root directory for ISCE2 work folders",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Explicit work directory. Overrides --work-root/<task_name>",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for final GeoTIFFs. Default: work_dir",
    )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Prefix for final output filenames. Default: task name",
    )
    parser.add_argument(
        "--orbit-root",
        default=str(repo_root / "orbit"),
        help="Directory containing LT1A_GpsData_GAS_C_YYYYMMDD.txt",
    )
    parser.add_argument(
        "--orbit-output-dir",
        default=None,
        help="Directory to place generated orbit XML files. Default: work_dir/orbits",
    )
    parser.add_argument(
        "--dem",
        default=None,
        help="DEM base path. Default: auto-detect the prepared WGS84 DEM",
    )
    parser.add_argument(
        "--bbox",
        default=None,
        help="Optional geocode bounding box: south,north,west,east",
    )
    parser.add_argument(
        "--bbox-margin",
        type=float,
        default=0.05,
        help="Auto-expand topo estimated bbox by this many degrees on each side",
    )
    parser.add_argument(
        "--orbit-margin-sec",
        type=float,
        default=60.0,
        help="Seconds to expand around scene time when clipping precise orbit, must be between 60 and 120",
    )
    parser.add_argument(
        "--master-dir-name",
        default="master",
        help="Subdirectory name for the reference scene inside the task directory",
    )
    parser.add_argument(
        "--slave-dir-name",
        default="slave",
        help="Subdirectory name for the secondary scene inside the task directory",
    )
    parser.add_argument(
        "--scene-glob",
        default="*.tiff",
        help="Glob pattern used to find scene files inside master/slave directories",
    )
    parser.add_argument(
        "--prefer-scene-keyword",
        default="_SLC_",
        help="Prefer matching files containing this keyword when multiple scene files are present",
    )
    parser.add_argument(
        "--coh-threshold",
        type=float,
        default=0.05,
        help="Coherence threshold for *_disp.tif export",
    )
    parser.add_argument(
        "--reference-mode",
        choices=REFERENCE_MODE_CHOICES,
        default=DEFAULT_REFERENCE_MODE,
        help="Reference normalization mode applied during final displacement export",
    )
    parser.add_argument(
        "--reference-coh-threshold",
        type=float,
        default=DEFAULT_REFERENCE_COH_THRESHOLD,
        help="Minimum coherence used when selecting reference pixels for export normalization",
    )
    parser.add_argument(
        "--deramp-mode",
        choices=DERAMP_MODE_CHOICES,
        default=DEFAULT_DERAMP_MODE,
        help="Optional ramp-removal mode applied after reference normalization",
    )
    parser.add_argument(
        "--deramp-coh-threshold",
        type=float,
        default=DEFAULT_DERAMP_COH_THRESHOLD,
        help="Minimum coherence used when selecting pixels for deramp fitting",
    )
    parser.add_argument(
        "--target-grid-size-m",
        type=int,
        default=DEFAULT_TARGET_GRID_SIZE_M,
        help="Target grid size in meters used to control multilook scale and geocoding spacing",
    )
    parser.add_argument(
        "--include-disp-full",
        action="store_true",
        help="Also export the unmasked displacement GeoTIFF for debugging",
    )
    parser.add_argument(
        "--full-geocode",
        action="store_true",
        help="Let ISCE2 geocode its full default product list instead of the reduced export-only list.",
    )
    parser.add_argument(
        "--no-ionosphere-correction",
        action="store_false",
        dest="ionosphere_correction",
        help="Disable split-spectrum dispersive correction and export the standard unwrapped interferogram.",
    )
    parser.set_defaults(ionosphere_correction=True)
    parser.add_argument(
        "--dense-offsets",
        action="store_true",
        help="Enable ISCE2 dense offset estimation before fine resampling.",
    )
    parser.add_argument(
        "--rubbersheet-range",
        action="store_true",
        help="Enable ISCE2 range rubbersheeting using dense offsets.",
    )
    parser.add_argument(
        "--rubbersheet-azimuth",
        action="store_true",
        help="Enable ISCE2 azimuth rubbersheeting using dense offsets.",
    )
    parser.add_argument(
        "--rubber-sheet-snr-threshold",
        type=float,
        default=DEFAULT_RUBBER_SHEET_SNR_THRESHOLD,
        help="SNR threshold used by ISCE2 rubbersheet offset masking.",
    )
    parser.add_argument(
        "--rubber-sheet-filter-size",
        type=int,
        default=DEFAULT_RUBBER_SHEET_FILTER_SIZE,
        help="Median filter size used by ISCE2 rubbersheet offset masking.",
    )
    parser.add_argument(
        "--dense-window-width",
        type=int,
        default=DEFAULT_DENSE_WINDOW_WIDTH,
        help="Dense offset correlation window width.",
    )
    parser.add_argument(
        "--dense-window-height",
        type=int,
        default=DEFAULT_DENSE_WINDOW_HEIGHT,
        help="Dense offset correlation window height.",
    )
    parser.add_argument(
        "--dense-search-width",
        type=int,
        default=DEFAULT_DENSE_SEARCH_WIDTH,
        help="Dense offset search window width.",
    )
    parser.add_argument(
        "--dense-search-height",
        type=int,
        default=DEFAULT_DENSE_SEARCH_HEIGHT,
        help="Dense offset search window height.",
    )
    parser.add_argument(
        "--dense-skip-width",
        type=int,
        default=DEFAULT_DENSE_SKIP_WIDTH,
        help="Dense offset sampling stride in range direction.",
    )
    parser.add_argument(
        "--dense-skip-height",
        type=int,
        default=DEFAULT_DENSE_SKIP_HEIGHT,
        help="Dense offset sampling stride in azimuth direction.",
    )
    parser.add_argument(
        "--resume-from",
        choices=RESUME_STAGE_CHOICES,
        default=None,
        help="Resume from an existing work directory starting at the given stage.",
    )
    parser.add_argument(
        "--wavelength",
        type=float,
        default=DEFAULT_WAVELENGTH,
        help="Radar wavelength in meters",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete an existing work directory before rerunning",
    )
    parser.add_argument(
        "--reference-satellite",
        default=None,
        help="Optional LT-1 satellite for the reference/master scene (LT1A or LT1B)",
    )
    parser.add_argument(
        "--secondary-satellite",
        default=None,
        help="Optional LT-1 satellite for the secondary/slave scene (LT1A or LT1B)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve inputs and print the planned configuration without running ISCE2",
    )
    args = parser.parse_args()
    if args.orbit_margin_sec < 60 or args.orbit_margin_sec > 120:
        raise ValueError("--orbit-margin-sec must be between 60 and 120 seconds")
    if args.target_grid_size_m <= 0:
        raise ValueError("--target-grid-size-m must be greater than 0")
    if args.reference_coh_threshold < 0 or args.reference_coh_threshold > 1:
        raise ValueError("--reference-coh-threshold must be between 0 and 1")
    if args.deramp_coh_threshold < 0 or args.deramp_coh_threshold > 1:
        raise ValueError("--deramp-coh-threshold must be between 0 and 1")
    if args.rubber_sheet_snr_threshold < 0:
        raise ValueError("--rubber-sheet-snr-threshold must be non-negative")
    if args.rubber_sheet_filter_size <= 0:
        raise ValueError("--rubber-sheet-filter-size must be greater than 0")
    for field_name in (
        "dense_window_width",
        "dense_window_height",
        "dense_search_width",
        "dense_search_height",
        "dense_skip_width",
        "dense_skip_height",
    ):
        if int(getattr(args, field_name)) <= 0:
            raise ValueError(f"--{field_name.replace('_', '-')} must be greater than 0")
    if args.force and args.resume_from:
        raise ValueError("--force cannot be used together with --resume-from")
    return args


def _find_python_module(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def validate_runtime_dependencies(args: argparse.Namespace) -> str:
    errors: list[str] = []
    env_for_cli = build_process_env()
    ionosphere_correction = bool(getattr(args, "ionosphere_correction", True))

    if ionosphere_correction:
        missing_ionosphere_modules: list[str] = []
        if not _find_python_module("cv2"):
            missing_ionosphere_modules.append("cv2")
        if not _find_python_module("scipy"):
            missing_ionosphere_modules.append("scipy")
        if missing_ionosphere_modules:
            errors.append(
                "Missing Python dependencies for the ISCE2 stripmap ionosphere step: "
                + ", ".join(missing_ionosphere_modules)
                + ". The managed LT-1 workflow enables split-spectrum dispersive correction "
                "before geocode. Install the missing packages in the WSL runtime, for example: "
                "conda install -n insar_wsl_v1 -c conda-forge opencv scipy."
            )

    if (args.rubbersheet_range or args.rubbersheet_azimuth) and not _find_python_module(
        "astropy.convolution"
    ):
        errors.append(
            "Missing Python dependency 'astropy.convolution'. "
            "ISCE2 stripmap rubbersheeting imports astropy.convolution in "
            "runRubbersheetRange.py. Install astropy in the WSL runtime, for example: "
            "conda install -n insar_wsl_v1 -c conda-forge astropy."
        )

    if ionosphere_correction and not shutil.which("imageMath.py", path=str(env_for_cli.get("PATH") or "")):
        errors.append(
            "Missing CLI dependency 'imageMath.py' on PATH. "
            "ISCE2 stripmap shells out to imageMath.py in the ionosphere step, so a missing PATH entry "
            "will only surface late in the run. Export the active conda env bin directory into PATH "
            "before launching production."
        )

    return "\n".join(errors)


def locate_stripmap_app() -> Path:
    import isce

    app_path = Path(isce.__file__).resolve().parent / "applications" / "stripmapApp.py"
    if not app_path.exists():
        raise FileNotFoundError(f"stripmapApp.py not found: {app_path}")
    return app_path


def locate_isce_applications_dir() -> Path | None:
    spec = importlib.util.find_spec("isce")
    if not spec or not spec.origin:
        return None

    app_dir = Path(spec.origin).resolve().parent / "applications"
    if app_dir.exists():
        return app_dir
    return None


def build_process_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base_env or os.environ.copy())
    path_prefixes = [Path(sys.executable).resolve().parent.as_posix()]
    app_dir = locate_isce_applications_dir()
    if app_dir:
        path_prefixes.append(app_dir.as_posix())

    current_path = str(env.get("PATH") or "")
    env["PATH"] = ":".join(path_prefixes + ([current_path] if current_path else []))
    return env


def normalize_linux_path(value: str | Path) -> Path:
    text = str(value).strip()
    if text.startswith("\\\\"):
        raise ValueError("UNC paths are not supported directly. Mount them in WSL first.")

    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")

    return Path(text)


def choose_scene_tiff(scene_dir: Path, scene_glob: str, prefer_scene_keyword: str) -> Path:
    candidates = sorted(scene_dir.glob(scene_glob))
    if not candidates:
        raise FileNotFoundError(f"No scene file matching {scene_glob} found in {scene_dir}")

    slc_candidates = [path for path in candidates if prefer_scene_keyword in path.name]
    if len(slc_candidates) == 1:
        return slc_candidates[0]
    if len(candidates) == 1:
        return candidates[0]
    raise RuntimeError(
        f"Expected one scene file in {scene_dir}; found {len(candidates)} matches for {scene_glob}"
    )


def scene_meta_from_tiff(tiff_path: Path) -> Path:
    candidates = [tiff_path.with_suffix(".meta.xml")]
    legacy_path = Path(str(tiff_path).replace(".tiff", ".meta.xml"))
    if legacy_path not in candidates:
        candidates.append(legacy_path)

    for meta_path in candidates:
        if meta_path.exists():
            return meta_path

    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Missing meta XML for {tiff_path}. Searched: {searched}")


def extract_scene_date(name: str) -> str:
    match = re.search(r"_(\d{8})_SLC_", name)
    if not match:
        match = re.search(r"(\d{8})", name)
    if not match:
        raise ValueError(f"Unable to extract scene date from filename: {name}")
    return match.group(1)


def normalize_lt1_satellite(value: str | None) -> str:
    text = str(value or "").strip().upper().replace("-", "").replace("_", "")
    if "LT1A" in text or text in {"A", "LTA"}:
        return "LT1A"
    if "LT1B" in text or text in {"B", "LTB"}:
        return "LT1B"
    return ""


def extract_scene_satellite_from_name(name: str) -> str:
    match = re.search(r"(LT1[AB])", str(name or ""), re.IGNORECASE)
    return normalize_lt1_satellite(match.group(1) if match else "")


def extract_scene_satellite_from_meta(meta_path: Path) -> str:
    try:
        root = ET.parse(meta_path).getroot()
    except Exception:
        return ""

    for element in root.iter():
        tag = str(element.tag or "").rsplit("}", 1)[-1].strip().lower()
        if tag not in {"mission", "satellite", "platform", "platformid", "missionid"}:
            continue
        satellite = normalize_lt1_satellite(element.text)
        if satellite:
            return satellite
    return ""


def resolve_scene_satellite(
    tiff_path: Path,
    meta_path: Path,
    explicit_satellite: str | None = None,
) -> str:
    explicit = normalize_lt1_satellite(explicit_satellite)
    name_satellite = extract_scene_satellite_from_name(tiff_path.name)
    meta_satellite = extract_scene_satellite_from_meta(meta_path)

    if explicit:
        if name_satellite and name_satellite != explicit:
            raise ValueError(
                f"Explicit satellite {explicit} does not match filename for {tiff_path.name}: {name_satellite}"
            )
        if meta_satellite and meta_satellite != explicit:
            raise ValueError(
                f"Explicit satellite {explicit} does not match metadata for {meta_path.name}: {meta_satellite}"
            )
        return explicit

    if name_satellite and meta_satellite and name_satellite != meta_satellite:
        raise ValueError(
            f"Satellite mismatch between filename and metadata for {tiff_path.name}: "
            f"{name_satellite} vs {meta_satellite}"
        )
    if name_satellite:
        return name_satellite
    if meta_satellite:
        return meta_satellite
    raise ValueError(f"Unable to resolve LT-1 satellite from {tiff_path} / {meta_path}")


def ensure_orbit_xml(
    date_yyyymmdd: str,
    satellite: str,
    annotation_xml: Path,
    orbit_root: Path,
    orbit_out_dir: Path,
    margin_sec: float,
) -> Path:
    resolution = ensure_lt1_orbit_xml(
        date_yyyymmdd=date_yyyymmdd,
        satellite=satellite,
        annotation_xml=annotation_xml,
        orbit_root=orbit_root,
        orbit_output_dir=orbit_out_dir,
        margin_sec=margin_sec,
    )
    return resolution.path


def resolve_dem(dem_value: str | None) -> Path:
    dem_path = resolve_prepared_dem_path(
        explicit_path=dem_value,
        env_values=None,
        default_candidates=DEFAULT_WSL_DEM_CANDIDATES,
        path_transform=normalize_linux_path,
    )
    if dem_path is not None:
        repair_reports = repair_related_dem_sidecars(dem_path)
        for report in repair_reports:
            if not report.get("changed"):
                continue
            print(
                "Repaired DEM sidecar paths: "
                f"{report['xml_path']} -> {', '.join(report['updated_fields'])}"
            )
        return dem_path

    searched = ", ".join(str(path) for path in DEFAULT_WSL_DEM_CANDIDATES)
    raise FileNotFoundError(
        "Unable to resolve a prepared DEM with ISCE wrappers. "
        f"Searched: {searched}"
    )


def _read_xml_property_value(root: ET.Element, name: str) -> str:
    for prop in root.findall("property"):
        if str(prop.get("name") or "").strip() != name:
            continue
        return str(prop.findtext("value") or "").strip()
    return ""


def read_dem_dimensions(dem_path: Path) -> tuple[int, int] | None:
    xml_path = Path(str(dem_path) + ".xml")
    if not xml_path.exists():
        return None
    root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="ignore"))
    width_text = _read_xml_property_value(root, "width")
    length_text = _read_xml_property_value(root, "length")
    if not width_text or not length_text:
        return None
    try:
        return int(float(width_text)), int(float(length_text))
    except ValueError:
        return None


def has_prepared_dem_sibling(dem_path: Path) -> bool:
    if dem_path.as_posix().lower().endswith(".wgs84"):
        return True
    sibling = Path(str(dem_path) + ".wgs84")
    return sibling.exists() and Path(str(sibling) + ".xml").exists()


def guard_large_unprepared_base_dem(dem_path: Path) -> None:
    if has_prepared_dem_sibling(dem_path):
        return
    dimensions = read_dem_dimensions(dem_path)
    if dimensions is None:
        return
    width, length = dimensions
    pixel_count = width * length
    if pixel_count < LARGE_BASE_DEM_PIXEL_THRESHOLD:
        return
    raise RuntimeError(
        "Configured DEM resolves to a large base raster without a prepared '.wgs84' sibling. "
        f"Selected DEM: {dem_path} ({width}x{length}, {pixel_count} pixels). "
        "A fresh ISCE2 run would spend a very long time rebuilding the geoid-corrected DEM during "
        "verifyDEM/topo. Prepare '<dem>.wgs84' once, or point ISCE2_DEM_PATH directly to the "
        "prepared file before starting a fresh run."
    )


def resolve_task(
    task_dir: Path,
    orbit_root: Path,
    orbit_out_dir: Path,
    margin_sec: float,
    master_dir_name: str,
    slave_dir_name: str,
    scene_glob: str,
    prefer_scene_keyword: str,
    reference_satellite: str | None = None,
    secondary_satellite: str | None = None,
) -> tuple[Scene, Scene]:
    scenes: list[Scene] = []
    satellite_hints = {
        "master": reference_satellite,
        "slave": secondary_satellite,
    }
    for role, subdir in (("master", master_dir_name), ("slave", slave_dir_name)):
        scene_dir = task_dir / subdir
        if not scene_dir.exists():
            raise FileNotFoundError(f"Missing task subdirectory: {scene_dir}")

        tiff_path = choose_scene_tiff(scene_dir, scene_glob, prefer_scene_keyword)
        meta_path = scene_meta_from_tiff(tiff_path)
        date_yyyymmdd = extract_scene_date(tiff_path.name)
        satellite = resolve_scene_satellite(
            tiff_path=tiff_path,
            meta_path=meta_path,
            explicit_satellite=satellite_hints.get(role),
        )
        orbit_xml_path = ensure_orbit_xml(
            date_yyyymmdd=date_yyyymmdd,
            satellite=satellite,
            annotation_xml=meta_path,
            orbit_root=orbit_root,
            orbit_out_dir=orbit_out_dir,
            margin_sec=margin_sec,
        )
        scenes.append(
            Scene(
                role=role,
                tiff_path=tiff_path,
                meta_path=meta_path,
                date_yyyymmdd=date_yyyymmdd,
                satellite=satellite,
                orbit_xml_path=orbit_xml_path,
            )
        )

    return scenes[0], scenes[1]


def render_bbox(bbox: list[float] | None) -> str:
    if bbox is None:
        return ""
    values = ", ".join(f"{value:.10f}".rstrip("0").rstrip(".") for value in bbox)
    return f'    <property name="geocode bounding box">[{values}]</property>\n'


def render_string_list(name: str, values: list[str] | None) -> str:
    if not values:
        return ""
    rendered = ", ".join(repr(str(value)) for value in values if str(value).strip())
    return f'    <property name="{name}">[{rendered}]</property>\n' if rendered else ""


def meters_to_geoposting_degrees(target_grid_size_m: int) -> float:
    return float(target_grid_size_m) / METERS_PER_DEGREE


def build_default_geocode_products(*, ionosphere_correction: bool) -> list[str]:
    return list(
        DEFAULT_EXPORT_GEOCODE_PRODUCTS
        if ionosphere_correction
        else DEFAULT_EXPORT_GEOCODE_PRODUCTS_NO_IONO
    )


def write_stripmap_xml(xml_path: Path, config: PipelineConfig) -> None:
    bbox_xml = render_bbox(config.bbox)
    geocode_list_xml = render_string_list("geocode list", config.geocode_products)
    enhancement_props = (
        f"    <property name=\"do denseoffsets\">{str(config.dense_offsets)}</property>\n"
        f"    <property name=\"do rubbersheetingRange\">{str(config.rubbersheet_range)}</property>\n"
        f"    <property name=\"do rubbersheetingAzimuth\">{str(config.rubbersheet_azimuth)}</property>\n"
        f"    <property name=\"rubber sheet SNR Threshold\">{config.rubber_sheet_snr_threshold}</property>\n"
        f"    <property name=\"rubber sheet filter size\">{config.rubber_sheet_filter_size}</property>\n"
        f"    <property name=\"dense window width\">{config.dense_window_width}</property>\n"
        f"    <property name=\"dense window height\">{config.dense_window_height}</property>\n"
        f"    <property name=\"dense search width\">{config.dense_search_width}</property>\n"
        f"    <property name=\"dense search height\">{config.dense_search_height}</property>\n"
        f"    <property name=\"dense skip width\">{config.dense_skip_width}</property>\n"
        f"    <property name=\"dense skip height\">{config.dense_skip_height}</property>\n"
    )
    text = (
        "<stripmapApp>\n"
        "  <component name=\"stripmapApp\">\n"
        "    <property name=\"sensor name\">LUTAN1</property>\n"
        "    <property name=\"reference sensor name\">LUTAN1</property>\n"
        "    <property name=\"secondary sensor name\">LUTAN1</property>\n"
        "    <property name=\"renderer\">xml</property>\n"
        "    <property name=\"do unwrap\">True</property>\n"
        "    <property name=\"unwrapper name\">snaphu</property>\n"
        f"    <property name=\"do split spectrum\">{str(config.ionosphere_correction)}</property>\n"
        f"    <property name=\"do dispersive\">{str(config.ionosphere_correction)}</property>\n"
        f"    <property name=\"posting\">{config.target_grid_size_m}</property>\n"
        f"    <property name=\"geoPosting\">{config.geo_posting_deg:.12f}</property>\n"
        f"{bbox_xml}"
        f"{geocode_list_xml}"
        f"{enhancement_props}"
        f"    <property name=\"demFilename\">{config.dem_path.as_posix()}</property>\n"
        "\n"
        "    <component name=\"Reference\">\n"
        f"      <property name=\"tiff\">{config.reference.tiff_path.as_posix()}</property>\n"
        f"      <property name=\"orbitFile\">{config.reference.orbit_xml_path.as_posix()}</property>\n"
        "      <property name=\"OUTPUT\">reference</property>\n"
        "    </component>\n"
        "\n"
        "    <component name=\"Secondary\">\n"
        f"      <property name=\"tiff\">{config.secondary.tiff_path.as_posix()}</property>\n"
        f"      <property name=\"orbitFile\">{config.secondary.orbit_xml_path.as_posix()}</property>\n"
        "      <property name=\"OUTPUT\">secondary</property>\n"
        "    </component>\n"
        "  </component>\n"
        "</stripmapApp>\n"
    )
    xml_path.write_text(text, encoding="utf-8")


def run_logged(stage_name: str, cmd: list[str], cwd: Path, log_path: Path) -> None:
    started_monotonic = time.monotonic()
    started_text = time.strftime("%Y-%m-%d %H:%M:%S")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[{stage_name}] Starting at {started_text}", flush=True)
    print("Running:", flush=True)
    print("  " + " ".join(cmd), flush=True)
    print(f"Log: {log_path}", flush=True)

    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"[{stage_name}] Starting at {started_text}\n")
        handle.write("Running:\n")
        handle.write("  " + " ".join(cmd) + "\n")
        handle.write(f"Log: {log_path}\n")
        handle.flush()

        child_env = build_process_env()
        child_env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=child_env,
        )

        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            handle.write(line)
            handle.flush()

        status = proc.wait()
        elapsed_seconds = time.monotonic() - started_monotonic
        handle.write(f"[{stage_name}] Finished with exit code {status} after {elapsed_seconds:.1f}s\n")
        handle.flush()

    print(
        f"[{stage_name}] Finished with exit code {status} after {elapsed_seconds:.1f}s",
        flush=True,
    )

    if status != 0:
        raise RuntimeError(f"Command failed with exit code {status}: {' '.join(cmd)}")


def parse_bbox_arg(value: str | None) -> list[float] | None:
    if value is None:
        return None
    parts = [item.strip() for item in value.split(",")]
    if len(parts) != 4:
        raise ValueError("Bounding box must be south,north,west,east")
    return [float(item) for item in parts]


def load_estimated_bbox(topo_xml: Path) -> list[float]:
    root = ET.fromstring(topo_xml.read_text(encoding="utf-8"))
    for prop in root.findall("property"):
        if prop.attrib.get("name") == "estimatedboundingbox":
            value = prop.findtext("value")
            if not value:
                break
            bbox = ast.literal_eval(value)
            return [float(item) for item in bbox]
    raise ValueError(f"estimatedboundingbox not found in {topo_xml}")


def expand_bbox(bbox: list[float], margin: float) -> list[float]:
    south, north, west, east = bbox
    return [
        max(-90.0, south - margin),
        min(90.0, north + margin),
        max(-180.0, west - margin),
        min(180.0, east + margin),
    ]


def resolve_auto_geocode_bbox(work_dir: Path, bbox_margin: float) -> tuple[list[float], list[float]]:
    topo_xml = work_dir / "PICKLE" / "topo.xml"
    if not topo_xml.exists():
        raise FileNotFoundError("topo step output is missing; cannot resolve the geocode bounding box.")
    estimated_bbox = load_estimated_bbox(topo_xml)
    return estimated_bbox, expand_bbox(estimated_bbox, bbox_margin)


def ensure_geocode_bbox(work_dir: Path, config: PipelineConfig, bbox_margin: float) -> None:
    if config.bbox is not None:
        return
    estimated_bbox, expanded_bbox = resolve_auto_geocode_bbox(work_dir, bbox_margin)
    config.bbox = expanded_bbox
    print(f"Auto bbox from topo: {estimated_bbox}")
    print(f"Expanded bbox used for geocode: {config.bbox}")


def cleanup_geocode_outputs(work_dir: Path, geocode_products: list[str] | None) -> None:
    if not geocode_products:
        return

    removed: list[Path] = []
    for product in geocode_products:
        base_path = work_dir / product
        for suffix in (".geo", ".geo.xml", ".geo.vrt", ".geo.aux.xml"):
            candidate = Path(str(base_path) + suffix)
            if candidate.exists():
                candidate.unlink()
                removed.append(candidate)

    if removed:
        print(f"Removed {len(removed)} stale geocode output file(s).")


def cleanup_dem_subset_outputs(base_path: Path) -> None:
    for suffix in ("", ".hdr", ".xml", ".vrt", ".aux.xml"):
        candidate = Path(str(base_path) + suffix)
        if candidate.exists():
            candidate.unlink()


def prepare_geocode_dem_subset(work_dir: Path, source_dem_path: Path, bbox: list[float]) -> Path:
    import isce  # noqa: F401  # Ensures the bundled ISCE packages are initialized on sys.path.
    import isceobj
    from osgeo import gdal

    gdal.UseExceptions()

    source_xml = Path(str(source_dem_path) + ".xml")
    if not source_xml.exists():
        raise FileNotFoundError(f"Missing DEM XML sidecar: {source_xml}")

    source_vrt = Path(str(source_dem_path) + ".vrt")
    source_open_path = source_vrt if source_vrt.exists() else source_dem_path
    if not source_open_path.exists():
        raise FileNotFoundError(f"Missing DEM source for geocode subset: {source_open_path}")

    subset_base = work_dir / "geocode_dem"
    cleanup_dem_subset_outputs(subset_base)

    south, north, west, east = bbox
    ds = gdal.Translate(
        subset_base.as_posix(),
        source_open_path.as_posix(),
        format="ENVI",
        projWin=[west, north, east, south],
    )
    if ds is None:
        raise RuntimeError(f"Failed to crop DEM subset from {source_open_path}")

    width = int(ds.RasterXSize or 0)
    length = int(ds.RasterYSize or 0)
    geotransform = ds.GetGeoTransform(can_return_null=True)
    ds = None

    if width <= 0 or length <= 0 or geotransform is None:
        raise RuntimeError("Cropped DEM subset is empty or missing georeferencing metadata.")

    source_dem = isceobj.createDemImage()
    source_dem.load(source_xml.as_posix())
    dem_reference = str(source_dem.reference or "").strip() or "UNKNOWN"

    source_dem.filename = subset_base.as_posix()
    source_dem.width = width
    source_dem.length = length
    source_dem.coord1.coordStart = geotransform[0]
    source_dem.coord1.coordDelta = geotransform[1]
    source_dem.coord1.coordSize = width
    source_dem.coord2.coordStart = geotransform[3]
    source_dem.coord2.coordDelta = geotransform[5]
    source_dem.coord2.coordSize = length
    source_dem.dump(subset_base.as_posix() + ".xml")
    source_dem.renderVRT()

    print(
        "Prepared geocode DEM subset: "
        f"{subset_base} ({width}x{length}, reference={dem_reference})"
    )
    return subset_base


def prepare_geocode_dem(work_dir: Path, config: PipelineConfig) -> None:
    if config.bbox is None:
        raise ValueError("Cannot prepare a geocode DEM subset without a resolved bbox.")
    config.dem_path = prepare_geocode_dem_subset(work_dir, config.dem_path, config.bbox)


def should_run_stage(start_stage: str, stage_name: str) -> bool:
    start_index = PIPELINE_STAGE_ORDER.index(start_stage)
    stage_index = PIPELINE_STAGE_ORDER.index(stage_name)
    return stage_index >= start_index


def has_pickle_state(work_dir: Path, state_name: str) -> bool:
    pickle_dir = work_dir / "PICKLE"
    return (pickle_dir / state_name).exists() and (pickle_dir / f"{state_name}.xml").exists()


def copy_pickle_state(work_dir: Path, source_state: str, target_state: str) -> Path:
    pickle_dir = work_dir / "PICKLE"
    src = pickle_dir / source_state
    src_xml = pickle_dir / f"{source_state}.xml"
    dst = pickle_dir / target_state
    dst_xml = pickle_dir / f"{target_state}.xml"

    if not src.exists() or not src_xml.exists():
        raise FileNotFoundError(
            f"PICKLE/{source_state} state is missing; cannot prepare PICKLE/{target_state}."
        )

    shutil.copy2(src, dst)
    shutil.copy2(src_xml, dst_xml)
    return dst_xml


def prepare_no_ionosphere_snaphu_resume(work_dir: Path, bbox: list[float] | None) -> None:
    dst_xml = copy_pickle_state(work_dir, "filter", "filter_high_band")
    root = ET.fromstring(dst_xml.read_text(encoding="utf-8"))
    props = {prop.attrib.get("name"): prop for prop in root.findall("property")}

    required = {
        "referenceslccroppedproduct": "reference_slc.xml",
        "secondaryslccroppedproduct": "secondary_slc.xml",
        "referenceslcproduct": "reference_slc.xml",
        "secondaryslcproduct": "secondary_slc.xml",
        "referencegeometrysystem": "Zero Doppler",
        "secondarygeometrysystem": "Zero Doppler",
    }
    if bbox is not None:
        required["estimatedboundingbox"] = str(bbox)

    for name, value in required.items():
        if name in props:
            node = props[name].find("value")
            if node is None:
                node = ET.SubElement(props[name], "value")
            node.text = value
            continue

        prop = ET.SubElement(root, "property", {"name": name})
        ET.SubElement(prop, "value").text = value

    dst_xml.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    print("Prepared no-ionosphere SNAPHU resume state: PICKLE/filter_high_band", flush=True)


def prepare_no_ionosphere_geocode_resume(work_dir: Path) -> None:
    copy_pickle_state(work_dir, "unwrap", "ionosphere")
    print("Prepared no-ionosphere geocode resume state: PICKLE/ionosphere", flush=True)


def resolve_unwrap_start_step(work_dir: Path, *, ionosphere_correction: bool) -> str:
    if ionosphere_correction:
        if has_pickle_state(work_dir, "ionosphere"):
            return "ionosphere"
        if has_pickle_state(work_dir, "unwrap_low_band") and has_pickle_state(
            work_dir, "unwrap_high_band"
        ):
            return "ionosphere"
        if has_pickle_state(work_dir, "filter_low_band") and has_pickle_state(
            work_dir, "filter_high_band"
        ):
            return "unwrap"
        if has_pickle_state(work_dir, "filter"):
            return "filter_low_band"
        raise FileNotFoundError(
            "Unable to resume the ISCE2 unwrap/ionosphere stage. Missing PICKLE state for "
            "filter, filter_low_band/filter_high_band, unwrap_low_band/unwrap_high_band, or ionosphere."
        )

    if has_pickle_state(work_dir, "unwrap"):
        return "unwrap"
    if has_pickle_state(work_dir, "filter"):
        return "unwrap"
    raise FileNotFoundError(
        "Unable to resume the ISCE2 unwrap stage. Missing PICKLE state for filter or unwrap."
    )


def resolve_geocode_start_step(work_dir: Path, *, ionosphere_correction: bool) -> str:
    if ionosphere_correction:
        if has_pickle_state(work_dir, "ionosphere"):
            return "geocode"
        if has_pickle_state(work_dir, "unwrap_low_band") and has_pickle_state(
            work_dir, "unwrap_high_band"
        ):
            return "ionosphere"
        raise FileNotFoundError(
            "Unable to resume the ISCE2 geocode stage. Missing PICKLE state for ionosphere or "
            "unwrap_low_band/unwrap_high_band."
        )

    if has_pickle_state(work_dir, "unwrap"):
        return "geocode"
    raise FileNotFoundError(
        "Unable to resume the ISCE2 geocode stage. Missing PICKLE state for unwrap."
    )


def print_summary(
    task_dir: Path,
    work_dir: Path,
    output_dir: Path,
    config: PipelineConfig,
) -> None:
    print(f"Task dir:     {task_dir}")
    print(f"Task name:    {config.task_name}")
    print(f"Output prefix:{config.output_prefix}")
    print(f"Work dir:     {work_dir}")
    print(f"Output dir:   {output_dir}")
    print(f"DEM:          {config.dem_path}")
    print(f"Reference:    {config.reference.tiff_path} [{config.reference.satellite}]")
    print(f"Secondary:    {config.secondary.tiff_path} [{config.secondary.satellite}]")
    print(f"Ref orbit:    {config.reference.orbit_xml_path}")
    print(f"Sec orbit:    {config.secondary.orbit_xml_path}")
    print(f"BBox:         {config.bbox if config.bbox is not None else 'auto'}")
    print(f"Target grid:  {config.target_grid_size_m} m")
    print(f"Geo posting:  {config.geo_posting_deg:.12f} deg")
    print(
        "Enhancement:  "
        f"split_spectrum={config.ionosphere_correction}, "
        f"ionosphere={config.ionosphere_correction}, "
        f"dense_offsets={config.dense_offsets}, "
        f"rubbersheet_range={config.rubbersheet_range}, "
        f"rubbersheet_azimuth={config.rubbersheet_azimuth}"
    )
    print(
        "Dense params: "
        f"window={config.dense_window_width}x{config.dense_window_height}, "
        f"search={config.dense_search_width}x{config.dense_search_height}, "
        f"skip={config.dense_skip_width}x{config.dense_skip_height}"
    )
    print(
        "Rubber mask:  "
        f"snr_threshold={config.rubber_sheet_snr_threshold}, "
        f"filter_size={config.rubber_sheet_filter_size}"
    )
    print(
        "Geocode list: "
        + (
            ", ".join(config.geocode_products)
            if config.geocode_products
            else "ISCE2 default"
        )
    )


def main() -> int:
    args = parse_args()
    if not args.dry_run:
        dependency_error = validate_runtime_dependencies(args)
        if dependency_error:
            print(dependency_error, file=sys.stderr)
            return 2
    resume_from = str(args.resume_from or "").strip().lower()
    start_stage = resume_from or PIPELINE_STAGE_ORDER[0]
    task_dir = normalize_linux_path(args.task_dir).resolve()
    if not task_dir.exists():
        raise FileNotFoundError(f"Task directory not found: {task_dir}")

    task_name = args.task_name or task_dir.name
    output_prefix = args.output_prefix or task_name
    work_root = normalize_linux_path(args.work_root).resolve()
    work_dir = normalize_linux_path(args.work_dir).resolve() if args.work_dir else work_root / task_name
    output_dir = normalize_linux_path(args.output_dir).resolve() if args.output_dir else work_dir
    orbit_root = normalize_linux_path(args.orbit_root).resolve()
    orbit_out_dir = (
        normalize_linux_path(args.orbit_output_dir).resolve()
        if args.orbit_output_dir
        else work_dir / "orbits"
    )

    if work_dir.exists():
        if resume_from:
            pass
        elif args.force:
            shutil.rmtree(work_dir)
        else:
            raise FileExistsError(f"Work directory already exists: {work_dir}. Use --force to recreate it.")
    elif resume_from:
        raise FileNotFoundError(
            f"Resume requested from {resume_from}, but work directory does not exist: {work_dir}"
        )

    work_dir.mkdir(parents=True, exist_ok=True)

    reference, secondary = resolve_task(
        task_dir=task_dir,
        orbit_root=orbit_root,
        orbit_out_dir=orbit_out_dir,
        margin_sec=args.orbit_margin_sec,
        master_dir_name=args.master_dir_name,
        slave_dir_name=args.slave_dir_name,
        scene_glob=args.scene_glob,
        prefer_scene_keyword=args.prefer_scene_keyword,
        reference_satellite=args.reference_satellite,
        secondary_satellite=args.secondary_satellite,
    )
    dem_path = resolve_dem(args.dem)
    bbox = parse_bbox_arg(args.bbox)
    geo_posting_deg = meters_to_geoposting_degrees(args.target_grid_size_m)

    config = PipelineConfig(
        task_name=task_name,
        output_prefix=output_prefix,
        dem_path=dem_path,
        reference=reference,
        secondary=secondary,
        bbox=bbox,
        target_grid_size_m=args.target_grid_size_m,
        geo_posting_deg=geo_posting_deg,
        geocode_products=(
            None
            if args.full_geocode
            else build_default_geocode_products(
                ionosphere_correction=bool(args.ionosphere_correction)
            )
        ),
        ionosphere_correction=bool(args.ionosphere_correction),
        dense_offsets=bool(args.dense_offsets),
        rubbersheet_range=bool(args.rubbersheet_range),
        rubbersheet_azimuth=bool(args.rubbersheet_azimuth),
        rubber_sheet_snr_threshold=float(args.rubber_sheet_snr_threshold),
        rubber_sheet_filter_size=int(args.rubber_sheet_filter_size),
        dense_window_width=int(args.dense_window_width),
        dense_window_height=int(args.dense_window_height),
        dense_search_width=int(args.dense_search_width),
        dense_search_height=int(args.dense_search_height),
        dense_skip_width=int(args.dense_skip_width),
        dense_skip_height=int(args.dense_skip_height),
    )
    if start_stage == PIPELINE_STAGE_ORDER[0]:
        guard_large_unprepared_base_dem(config.dem_path)

    if resume_from in {"unwrap", "geocode"}:
        ensure_geocode_bbox(work_dir, config, args.bbox_margin)
        if should_run_stage(start_stage, "geocode"):
            prepare_geocode_dem(work_dir, config)

    print_summary(task_dir=task_dir, work_dir=work_dir, output_dir=output_dir, config=config)

    xml_path = work_dir / f"{task_name}_stripmap.xml"
    write_stripmap_xml(xml_path, config)

    if args.dry_run:
        print(f"Generated XML: {xml_path}")
        return 0

    app_py = locate_stripmap_app()

    if should_run_stage(start_stage, "filter"):
        run_logged(
            "01_to_filter",
            [sys.executable, app_py.as_posix(), xml_path.as_posix(), "--steps", "--end=filter"],
            cwd=work_dir,
            log_path=work_dir / "01_to_filter.log",
        )

    if should_run_stage(start_stage, "geocode") and config.bbox is None:
        ensure_geocode_bbox(work_dir, config, args.bbox_margin)
        prepare_geocode_dem(work_dir, config)
        write_stripmap_xml(xml_path, config)

    if should_run_stage(start_stage, "unwrap"):
        if not config.ionosphere_correction:
            prepare_no_ionosphere_snaphu_resume(work_dir, config.bbox)
        unwrap_start_step = resolve_unwrap_start_step(
            work_dir,
            ionosphere_correction=config.ionosphere_correction,
        )
        unwrap_end_step = "ionosphere" if config.ionosphere_correction else "unwrap"
        unwrap_stage_name = "02_to_ionosphere" if config.ionosphere_correction else "02_to_unwrap"
        run_logged(
            unwrap_stage_name,
            [
                sys.executable,
                app_py.as_posix(),
                xml_path.as_posix(),
                "--steps",
                f"--start={unwrap_start_step}",
                f"--end={unwrap_end_step}",
            ],
            cwd=work_dir,
            log_path=work_dir / f"{unwrap_stage_name}.log",
        )

    if should_run_stage(start_stage, "geocode"):
        if not config.ionosphere_correction:
            prepare_no_ionosphere_geocode_resume(work_dir)
        geocode_start_step = resolve_geocode_start_step(
            work_dir,
            ionosphere_correction=config.ionosphere_correction,
        )
        cleanup_geocode_outputs(work_dir, config.geocode_products)
        run_logged(
            "03_geocode",
            [
                sys.executable,
                app_py.as_posix(),
                xml_path.as_posix(),
                "--steps",
                f"--start={geocode_start_step}",
                "--end=geocode",
            ],
            cwd=work_dir,
            log_path=work_dir / "03_geocode.log",
        )

    outputs: dict[str, Path] = {}
    if should_run_stage(start_stage, "export"):
        outputs = export_products(
            work_dir=work_dir,
            output_dir=output_dir,
            prefix=output_prefix,
            wavelength=args.wavelength,
            coh_threshold=args.coh_threshold,
            reference_mode=args.reference_mode,
            reference_coh_threshold=args.reference_coh_threshold,
            deramp_mode=args.deramp_mode,
            deramp_coh_threshold=args.deramp_coh_threshold,
            include_disp_full=args.include_disp_full,
        )

        print("Pipeline finished.")
        for key, path in outputs.items():
            print(f"{key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
