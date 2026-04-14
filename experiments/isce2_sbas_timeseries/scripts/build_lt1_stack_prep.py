#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST_PATH = (
    REPO_ROOT
    / "experiments"
    / "isce2_sbas_timeseries"
    / "configs"
    / "sample_stack_e123p3_n46p1.json"
)
DEFAULT_STACK_SCRIPT_WSL = (
    "/home/administrator/miniconda3/envs/isce2/share/isce2/stripmapStack/stackStripMap.py"
)
DEFAULT_CONDA_WSL = "/home/administrator/miniconda3/bin/conda"
DEFAULT_ISCE2_SHARE_WSL = "/home/administrator/miniconda3/envs/isce2/share/isce2"
DEFAULT_STRIPMAP_STACK_DIR_WSL = f"{DEFAULT_ISCE2_SHARE_WSL}/stripmapStack"
SUPPORTED_STACK_WORKFLOWS = ("slc", "interferogram", "ionosphere")
DEFAULT_STACK_TEXT_CMD = (
    f"export PATH={DEFAULT_STRIPMAP_STACK_DIR_WSL}:$PATH; "
    f"export PYTHONPATH={DEFAULT_STRIPMAP_STACK_DIR_WSL}:{DEFAULT_ISCE2_SHARE_WSL}${{PYTHONPATH:+:$PYTHONPATH}}; "
)


def windows_to_wsl(path: str | Path) -> str:
    text = str(path)
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if not match:
        return text.replace("\\", "/")
    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{tail}"


def load_isce2_input_helper_module():
    helper_path = REPO_ROOT / "backend" / "app" / "isce2_pipeline" / "lt1_input_resolver.py"
    spec = importlib.util.spec_from_file_location("lt1_orbit_helper", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load orbit helper module: {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ISCE2_INPUT_HELPER = load_isce2_input_helper_module()


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def shelve_stem_exists(stem: Path) -> bool:
    for suffix in ("", ".db", ".dat", ".dir", ".bak"):
        if Path(str(stem) + suffix).exists():
            return True
    return False


@dataclass
class ScenePlan:
    date: str
    target_dir_windows: str
    target_dir_wsl: str
    source_scene_json_windows: str
    source_scene_json_wsl: str
    source_tiff_windows: str
    source_tiff_wsl: str
    source_meta_windows: str
    source_meta_wsl: str
    orbit_xml_windows: Optional[str]
    orbit_xml_wsl: Optional[str]
    orbit_xml_exists: bool
    orbit_resolution_mode: Optional[str]
    orbit_resolution_error: Optional[str]
    source_exists: bool
    scene_start_utc: str
    scene_stop_utc: str
    orbit_window_start_utc: str
    orbit_window_stop_utc: str
    expected_slc_windows: str
    expected_slc_wsl: str
    expected_slc_xml_windows: str
    expected_slc_xml_wsl: str
    expected_data_shelve_windows: str
    expected_data_shelve_wsl: str
    materialized_slc_exists: bool
    materialized_data_exists: bool
    stack_ready: bool
    status: str


def build_scene_plan(
    scene: Dict[str, Any],
    slc_root: Path,
    orbit_pool: Optional[Path],
    orbit_stage_dir: Path,
    margin_sec: float,
) -> ScenePlan:
    date = str(scene["imaging_date"])
    satellite = str(scene["satellite"])
    source_tiff = Path(scene["tiff_path"])
    source_meta = Path(scene["meta_path"])
    require_file(source_tiff, f"scene TIFF for {date}")
    require_file(source_meta, f"scene meta XML for {date}")

    scene_start_dt, scene_stop_dt = ISCE2_INPUT_HELPER.parse_scene_window(source_meta, margin_sec=0.0)
    orbit_window_start_dt, orbit_window_stop_dt = ISCE2_INPUT_HELPER.parse_scene_window(
        source_meta,
        margin_sec=margin_sec,
    )
    scene_start_utc = scene_start_dt.isoformat()
    scene_stop_utc = scene_stop_dt.isoformat()
    orbit_window_start_utc = orbit_window_start_dt.isoformat()
    orbit_window_stop_utc = orbit_window_stop_dt.isoformat()

    target_dir = slc_root / date
    expected_slc = target_dir / f"{date}.slc"
    expected_slc_xml = target_dir / f"{date}.slc.xml"
    expected_data = target_dir / "data"
    source_scene_json = target_dir / "source_scene.json"

    orbit_xml: Optional[Path] = None
    orbit_resolution_mode: Optional[str] = None
    orbit_resolution_error: Optional[str] = None
    if orbit_pool is not None:
        try:
            orbit_resolution = ISCE2_INPUT_HELPER.ensure_lt1_orbit_xml(
                date_yyyymmdd=date,
                satellite=satellite,
                annotation_xml=source_meta,
                orbit_root=orbit_pool,
                orbit_output_dir=orbit_stage_dir,
                margin_sec=margin_sec,
            )
            orbit_xml = orbit_resolution.path
            orbit_resolution_mode = orbit_resolution.source
        except Exception as exc:
            orbit_resolution_error = str(exc)

    materialized_slc_exists = expected_slc.exists() and expected_slc_xml.exists()
    materialized_data_exists = shelve_stem_exists(expected_data)
    stack_ready = bool(orbit_xml and materialized_slc_exists and materialized_data_exists)

    if not orbit_xml:
        status = "missing_orbit_xml"
    elif not materialized_slc_exists and not materialized_data_exists:
        status = "waiting_for_scene_materializer"
    elif not materialized_slc_exists:
        status = "missing_slc"
    elif not materialized_data_exists:
        status = "missing_data_shelve"
    else:
        status = "ready"

    return ScenePlan(
        date=date,
        target_dir_windows=str(target_dir),
        target_dir_wsl=windows_to_wsl(target_dir),
        source_scene_json_windows=str(source_scene_json),
        source_scene_json_wsl=windows_to_wsl(source_scene_json),
        source_tiff_windows=str(source_tiff),
        source_tiff_wsl=windows_to_wsl(source_tiff),
        source_meta_windows=str(source_meta),
        source_meta_wsl=windows_to_wsl(source_meta),
        orbit_xml_windows=str(orbit_xml) if orbit_xml else None,
        orbit_xml_wsl=windows_to_wsl(orbit_xml) if orbit_xml else None,
        orbit_xml_exists=bool(orbit_xml),
        orbit_resolution_mode=orbit_resolution_mode,
        orbit_resolution_error=orbit_resolution_error,
        source_exists=True,
        scene_start_utc=scene_start_utc,
        scene_stop_utc=scene_stop_utc,
        orbit_window_start_utc=orbit_window_start_utc,
        orbit_window_stop_utc=orbit_window_stop_utc,
        expected_slc_windows=str(expected_slc),
        expected_slc_wsl=windows_to_wsl(expected_slc),
        expected_slc_xml_windows=str(expected_slc_xml),
        expected_slc_xml_wsl=windows_to_wsl(expected_slc_xml),
        expected_data_shelve_windows=str(expected_data),
        expected_data_shelve_wsl=windows_to_wsl(expected_data),
        materialized_slc_exists=materialized_slc_exists,
        materialized_data_exists=materialized_data_exists,
        stack_ready=stack_ready,
        status=status,
    )


def render_stack_command(
    slc_dir_wsl: str,
    dem_wsl: str,
    work_dir_wsl: str,
    reference_date: str,
    workflow: str,
) -> List[str]:
    return [
        DEFAULT_CONDA_WSL,
        "run",
        "-n",
        "isce2",
        "python",
        DEFAULT_STACK_SCRIPT_WSL,
        "-s",
        slc_dir_wsl,
        "-d",
        dem_wsl,
        "-w",
        work_dir_wsl,
        "-m",
        reference_date,
        "--nofocus",
        "-W",
        workflow,
        "-u",
        "snaphu",
        "-c",
        DEFAULT_STACK_TEXT_CMD,
    ]


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def render_shell_command(argv: List[str]) -> str:
    return " ".join(shell_quote(item) for item in argv)


def build_blockers(scene_plans: List[ScenePlan], orbit_pool: Optional[Path], dem_path: Optional[Path]) -> List[str]:
    blockers: List[str] = []
    if orbit_pool is None:
        blockers.append("ORBIT_POOL_ISCE2 was not resolved.")
    if dem_path is None:
        blockers.append("Prepared DEM with .xml sidecar was not resolved.")

    missing_orbit = [item.date for item in scene_plans if not item.orbit_xml_exists]
    if missing_orbit:
        blockers.append("Missing orbit XML for dates: " + ", ".join(missing_orbit))
    orbit_errors = [f"{item.date}: {item.orbit_resolution_error}" for item in scene_plans if item.orbit_resolution_error]
    if orbit_errors:
        blockers.append("Orbit resolution errors: " + "; ".join(orbit_errors))

    missing_slc = [item.date for item in scene_plans if not item.materialized_slc_exists]
    if missing_slc:
        blockers.append("Materialized .slc/.slc.xml are missing for dates: " + ", ".join(missing_slc))

    missing_data = [item.date for item in scene_plans if not item.materialized_data_exists]
    if missing_data:
        blockers.append("ISCE data shelve is missing for dates: " + ", ".join(missing_data))

    return blockers


def render_contract_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    ready = bool(report["readiness"]["ready_for_stackStripMap_nofocus"])
    lines.append("# LT-1 Stack Prep Contract")
    lines.append("")
    lines.append(f"Generated: {report['generated_at_utc']}")
    lines.append("")
    lines.append("## Selected Stack")
    lines.append("")
    lines.append(f"- Group key: `{report['group_key']}`")
    lines.append(f"- Reference date: `{report['reference_date']}`")
    lines.append(f"- Workflow: `{report['processing_workflow']}`")
    lines.append(f"- Scene count: `{report['scene_count']}`")
    lines.append("")
    lines.append("## Resolved Runtime Inputs")
    lines.append("")
    lines.append(f"- Orbit pool (Windows): `{report['resolved_dependencies']['orbit_pool_windows'] or 'UNRESOLVED'}`")
    lines.append(f"- Orbit pool (WSL): `{report['resolved_dependencies']['orbit_pool_wsl'] or 'UNRESOLVED'}`")
    lines.append(f"- DEM (Windows): `{report['resolved_dependencies']['dem_path_windows'] or 'UNRESOLVED'}`")
    lines.append(f"- DEM (WSL): `{report['resolved_dependencies']['dem_path_wsl'] or 'UNRESOLVED'}`")
    lines.append("")
    lines.append("## Confirmed stripmapStack Contract")
    lines.append("")
    lines.append("- `stackStripMap.py --nofocus` discovers dates from `SLC/YYYYMMDD/YYYYMMDD.slc`.")
    lines.append("- `topo.py` opens `SLC/YYYYMMDD/data` for the reference acquisition.")
    lines.append("- `geo2rdr.py` opens `SLC/YYYYMMDD/data` for each secondary acquisition.")
    lines.append("- Therefore each acquisition directory must contain at least:")
    lines.append("  - `YYYYMMDD.slc`")
    lines.append("  - `YYYYMMDD.slc.xml`")
    lines.append("  - `data` shelve with `frame` and optional `doppler`")
    lines.append("")
    lines.append("## Scene Status")
    lines.append("")
    lines.append("| Date | Orbit XML | SLC | Data | Status |")
    lines.append("| --- | --- | --- | --- | --- |")
    for scene in report["scenes"]:
        orbit_ok = "yes" if scene["orbit_xml_exists"] else "no"
        slc_ok = "yes" if scene["materialized_slc_exists"] else "no"
        data_ok = "yes" if scene["materialized_data_exists"] else "no"
        lines.append(f"| {scene['date']} | {orbit_ok} | {slc_ok} | {data_ok} | `{scene['status']}` |")
    lines.append("")
    lines.append("## Draft stackStripMap Command")
    lines.append("")
    lines.append("```bash")
    lines.append(report["stack_command"]["shell"])
    lines.append("```")
    lines.append("")
    lines.append("## Current Blockers")
    lines.append("")
    blockers = report["readiness"]["blocking_reasons"]
    if blockers:
        for blocker in blockers:
            lines.append(f"- {blocker}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Next Tasks")
    lines.append("")
    if ready:
        lines.append("- Execute `run_01_reference` and confirm geometry generation succeeds.")
        lines.append("- Execute `run_02` to `run_07` step by step and record any LT-1-specific failures.")
        lines.append("- Inspect `baselines/`, `configs/`, and the first coarse coregistration outputs.")
        lines.append("- Install MintPy only after the stack run outputs are stable.")
    else:
        lines.append("- Use the LT-1 scene materializer to finish the remaining acquisitions under the generated `SLC/` root.")
        lines.append("- Re-run the generated preflight script, then execute `stackStripMap.py --nofocus`.")
        lines.append("- Install MintPy only after the stack materializer contract is working end to end.")
    lines.append("")
    return "\n".join(lines)


def render_run_script(report: Dict[str, Any]) -> str:
    slc_dir = report["workspace"]["slc_dir_wsl"]
    work_dir = report["workspace"]["stack_work_dir_wsl"]
    dem_path = report["resolved_dependencies"]["dem_path_wsl"] or "__MISSING_DEM__"
    reference_date = report["reference_date"]
    dates = " ".join(scene["date"] for scene in report["scenes"])
    command = report["stack_command"]["shell"]

    return f"""#!/usr/bin/env bash
set -euo pipefail

SLC_DIR={shell_quote(slc_dir)}
WORK_DIR={shell_quote(work_dir)}
DEM={shell_quote(dem_path)}
REFERENCE_DATE={shell_quote(reference_date)}
ISCE2_SHARE={shell_quote(DEFAULT_ISCE2_SHARE_WSL)}
STRIPMAP_STACK_DIR={shell_quote(DEFAULT_STRIPMAP_STACK_DIR_WSL)}
DATES=({dates})

export PYTHONPATH="$STRIPMAP_STACK_DIR:$ISCE2_SHARE${{PYTHONPATH:+:$PYTHONPATH}}"
export PATH="$STRIPMAP_STACK_DIR:$PATH"

echo "LT-1 stripmap stack dry-run preflight"
echo "SLC root: $SLC_DIR"
echo "Work dir: $WORK_DIR"
echo "DEM: $DEM"
echo "Reference date: $REFERENCE_DATE"
echo "PYTHONPATH: $PYTHONPATH"
echo "PATH prefix: $STRIPMAP_STACK_DIR"

missing=0
for d in "${{DATES[@]}}"; do
  if [[ ! -f "$SLC_DIR/$d/$d.slc" ]]; then
    echo "MISSING: $SLC_DIR/$d/$d.slc"
    missing=1
  fi
  if [[ ! -f "$SLC_DIR/$d/$d.slc.xml" ]]; then
    echo "MISSING: $SLC_DIR/$d/$d.slc.xml"
    missing=1
  fi
  if [[ ! -e "$SLC_DIR/$d/data" && ! -e "$SLC_DIR/$d/data.db" && ! -e "$SLC_DIR/$d/data.dat" && ! -e "$SLC_DIR/$d/data.dir" && ! -e "$SLC_DIR/$d/data.bak" ]]; then
    echo "MISSING: $SLC_DIR/$d/data"
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  echo "Dry-run only. LT-1 scene materialization is still missing."
  exit 2
fi

echo "Preflight passed. Running stackStripMap."
{command}
"""


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a dry-run LT-1 SBAS stack-prep workspace for ISCE2 stripmapStack."
    )
    parser.add_argument(
        "--manifest-path",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Path to the selected stack manifest JSON.",
    )
    parser.add_argument(
        "--scratch-root",
        default=None,
        help="Override the stack scratch root directory. Defaults to proposed_scratch_windows in the manifest.",
    )
    parser.add_argument(
        "--orbit-pool",
        default=None,
        help="Override ORBIT_POOL_ISCE2 (Windows path containing LT1A_GpsData_GAS_C_YYYYMMDD.xml).",
    )
    parser.add_argument(
        "--dem-path",
        default=None,
        help="Override the prepared DEM base path (must have a .xml sidecar).",
    )
    parser.add_argument(
        "--orbit-margin-sec",
        type=float,
        default=60.0,
        help="Margin used when reporting the recommended orbit clip window.",
    )
    parser.add_argument(
        "--workflow",
        default="slc",
        choices=SUPPORTED_STACK_WORKFLOWS,
        help="stripmapStack workflow to generate: slc, interferogram, or ionosphere.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_values = ISCE2_INPUT_HELPER.load_env_file(REPO_ROOT / ".env")

    manifest_path = Path(args.manifest_path).resolve()
    require_file(manifest_path, "stack manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    scratch_root = Path(args.scratch_root or manifest["proposed_scratch_windows"]).resolve()
    slc_root = scratch_root / "SLC"
    orbits_dir = scratch_root / "orbits"
    logs_dir = scratch_root / "logs"
    notes_dir = scratch_root / "notes"
    inputs_dir = scratch_root / "inputs"
    stack_work_dir = scratch_root / "stack_work"

    for path in (scratch_root, slc_root, orbits_dir, logs_dir, notes_dir, inputs_dir, stack_work_dir):
        path.mkdir(parents=True, exist_ok=True)

    orbit_pool = ISCE2_INPUT_HELPER.resolve_orbit_pool_path(
        explicit_path=args.orbit_pool,
        env_values=env_values,
        default_candidates=ISCE2_INPUT_HELPER.DEFAULT_WINDOWS_ORBIT_POOL_CANDIDATES,
    )
    local_dem_candidate = inputs_dir / "dem" / "stack_dem_window.wgs84"
    dem_path = ISCE2_INPUT_HELPER.resolve_prepared_dem_path(
        explicit_path=args.dem_path,
        env_values=env_values,
        extra_candidates=[local_dem_candidate],
        default_candidates=ISCE2_INPUT_HELPER.DEFAULT_WINDOWS_DEM_CANDIDATES,
    )

    scene_plans = [
        build_scene_plan(
            scene,
            slc_root=slc_root,
            orbit_pool=orbit_pool,
            orbit_stage_dir=orbits_dir,
            margin_sec=args.orbit_margin_sec,
        )
        for scene in manifest["scenes"]
    ]
    scene_plans.sort(key=lambda item: item.date)

    for plan, source_scene in zip(scene_plans, sorted(manifest["scenes"], key=lambda item: item["imaging_date"])):
        target_dir = Path(plan.target_dir_windows)
        target_dir.mkdir(parents=True, exist_ok=True)
        scene_payload = dict(source_scene)
        scene_payload["stack_prep"] = {
            "date": plan.date,
            "target_dir_windows": plan.target_dir_windows,
            "target_dir_wsl": plan.target_dir_wsl,
            "orbit_xml_windows": plan.orbit_xml_windows,
            "orbit_xml_wsl": plan.orbit_xml_wsl,
            "orbit_resolution_mode": plan.orbit_resolution_mode,
            "orbit_resolution_error": plan.orbit_resolution_error,
            "scene_start_utc": plan.scene_start_utc,
            "scene_stop_utc": plan.scene_stop_utc,
            "orbit_window_start_utc": plan.orbit_window_start_utc,
            "orbit_window_stop_utc": plan.orbit_window_stop_utc,
            "expected_slc_windows": plan.expected_slc_windows,
            "expected_slc_xml_windows": plan.expected_slc_xml_windows,
            "expected_data_shelve_windows": plan.expected_data_shelve_windows,
            "status": plan.status,
        }
        write_json(target_dir / "source_scene.json", scene_payload)

    stack_command_argv = render_stack_command(
        slc_dir_wsl=windows_to_wsl(slc_root),
        dem_wsl=windows_to_wsl(dem_path) if dem_path else "__MISSING_DEM__",
        work_dir_wsl=windows_to_wsl(stack_work_dir),
        reference_date=manifest["reference_date"],
        workflow=args.workflow,
    )

    blockers = build_blockers(scene_plans, orbit_pool=orbit_pool, dem_path=dem_path)
    readiness = {
        "all_orbits_resolved": all(item.orbit_xml_exists for item in scene_plans),
        "all_materialized_slc_present": all(item.materialized_slc_exists for item in scene_plans),
        "all_data_shelves_present": all(item.materialized_data_exists for item in scene_plans),
        "ready_for_stackStripMap_nofocus": not blockers,
        "blocking_reasons": blockers,
    }

    report: Dict[str, Any] = {
        "manifest_version": 1,
        "generated_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "source_manifest_windows": str(manifest_path),
        "source_manifest_wsl": windows_to_wsl(manifest_path),
        "group_key": manifest["group_key"],
        "tile_key": manifest["tile_key"],
        "scene_count": manifest["scene_count"],
        "reference_date": manifest["reference_date"],
        "reference_strategy": manifest["reference_strategy"],
        "processing_workflow": args.workflow,
        "sensor_name": "LUTAN1",
        "stack_driver": "isce2.stripmapStack.stackStripMap",
        "workspace": {
            "root_windows": str(scratch_root),
            "root_wsl": windows_to_wsl(scratch_root),
            "slc_dir_windows": str(slc_root),
            "slc_dir_wsl": windows_to_wsl(slc_root),
            "orbits_dir_windows": str(orbits_dir),
            "orbits_dir_wsl": windows_to_wsl(orbits_dir),
            "logs_dir_windows": str(logs_dir),
            "logs_dir_wsl": windows_to_wsl(logs_dir),
            "notes_dir_windows": str(notes_dir),
            "notes_dir_wsl": windows_to_wsl(notes_dir),
            "inputs_dir_windows": str(inputs_dir),
            "inputs_dir_wsl": windows_to_wsl(inputs_dir),
            "stack_work_dir_windows": str(stack_work_dir),
            "stack_work_dir_wsl": windows_to_wsl(stack_work_dir),
        },
        "resolved_dependencies": {
            "orbit_pool_windows": str(orbit_pool) if orbit_pool else None,
            "orbit_pool_wsl": windows_to_wsl(orbit_pool) if orbit_pool else None,
            "dem_path_windows": str(dem_path) if dem_path else None,
            "dem_path_wsl": windows_to_wsl(dem_path) if dem_path else None,
        },
        "stack_contract": {
            "mode": "nofocus",
            "workflow": args.workflow,
            "required_per_acquisition_files": [
                "YYYYMMDD.slc",
                "YYYYMMDD.slc.xml",
                "data shelve",
            ],
            "current_source_layout": "per_scene_folder_with_tiff_meta_rpc",
            "adapter_needed": True,
            "adapter_goal": "materialize a stripmapStack-ready date directory from LT-1 TIFF/meta/orbit inputs",
        },
        "stack_command": {
            "argv": stack_command_argv,
            "shell": render_shell_command(stack_command_argv),
        },
        "readiness": readiness,
        "scenes": [plan.__dict__ for plan in scene_plans],
        "next_tasks": [
            "Use the LT-1 scene materializer to build YYYYMMDD.slc and data shelve for the remaining acquisitions.",
            "Keep raw scene data external and store only lightweight source manifests plus generated ISCE products under scratch/SLC/YYYYMMDD.",
            f"Run stripmapStack in --nofocus mode with workflow={args.workflow} once every date directory is materialized.",
            "Install MintPy only after stackStripMap produces stable interferogram outputs.",
        ],
    }

    report_path = scratch_root / "stack_input_manifest.json"
    contract_path = scratch_root / "stack_prep_contract.md"
    run_script_path = scratch_root / "run_stripmap_stack_dryrun.sh"

    write_json(report_path, report)
    contract_path.write_text(render_contract_markdown(report), encoding="utf-8")
    run_script_path.write_text(render_run_script(report), encoding="utf-8", newline="\n")

    print(f"Manifest:     {report_path}")
    print(f"Contract:     {contract_path}")
    print(f"Run script:   {run_script_path}")
    print(f"Scratch root: {scratch_root}")
    print(f"Orbit pool:   {orbit_pool if orbit_pool else 'UNRESOLVED'}")
    print(f"DEM:          {dem_path if dem_path else 'UNRESOLVED'}")
    print(f"Ready:        {readiness['ready_for_stackStripMap_nofocus']}")
    if blockers:
        print("Blockers:")
        for blocker in blockers:
            print(f"  - {blocker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
