#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_utils_module():
    utils_path = _repo_root() / "backend" / "app" / "utils.py"
    spec = importlib.util.spec_from_file_location("repo_utils", utils_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load repo utils module: {utils_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UTILS = _load_utils_module()


LT1_NAME_RE = re.compile(
    r"^(?P<satellite>LT1[AB])_"
    r"(?P<satellite_mode>[^_]+)_"
    r"(?P<receiving_station>[^_]+)_"
    r"(?P<imaging_mode>[^_]+)_"
    r"(?P<abs_orbit>\d+)_"
    r"(?P<lon>E\d+\.\d+)_"
    r"(?P<lat>N\d+\.\d+)_"
    r"(?P<date>\d{8})_"
    r"(?P<product_type>[^_]+)_"
    r"(?P<polarization>[^_]+)_"
    r"(?P<product_level>[^_]+)_"
    r"(?P<product_unique_id>\d+)$"
)


@dataclass
class SceneRecord:
    folder_name: str
    folder_path: str
    folder_path_wsl: str
    tiff_path: str
    tiff_path_wsl: str
    meta_path: str
    meta_path_wsl: str
    file_size_bytes: int
    satellite: str
    imaging_date: str
    imaging_mode: Optional[str]
    polarization: Optional[str]
    orbit_direction: Optional[str]
    satellite_mode: Optional[str]
    receiving_station: Optional[str]
    orbit_circle: Optional[str]
    scene_center_lon: Optional[float]
    scene_center_lat: Optional[float]
    acquisition_time_utc: Optional[str]
    product_type: Optional[str]
    product_level: Optional[str]
    product_unique_id: Optional[str]
    tile_key: str
    group_key: str
    orbit_txt_expected_name: str


def windows_to_wsl(path: str | Path) -> str:
    text = str(path)
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", os.path.normpath(text))
    if not match:
        return text.replace("\\", "/")
    drive = match.group(1).lower()
    normalized_tail = match.group(2).replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{normalized_tail}"


def choose_tiff(folder: Path) -> Optional[Path]:
    candidates = sorted(folder.glob("*.tiff"))
    if not candidates:
        return None
    slc_candidates = [path for path in candidates if "_SLC_" in path.name]
    if len(slc_candidates) == 1:
        return slc_candidates[0]
    if len(candidates) == 1:
        return candidates[0]
    return candidates[0]


def merge_metadata(name_meta: Dict[str, Any], xml_meta: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(name_meta or {})
    prefer_name_keys = {"product_unique_id"}
    for key, value in (xml_meta or {}).items():
        if value in (None, ""):
            continue
        if key in prefer_name_keys and merged.get(key):
            continue
        merged[key] = value
    return merged


def parse_scene(folder: Path) -> Optional[SceneRecord]:
    match = LT1_NAME_RE.match(folder.name)
    if not match:
        return None

    name_meta = UTILS.get_parser(folder.name, UTILS.RADAR_PARSERS)
    if not name_meta:
        return None

    xml_file_path = UTILS.find_xml_file(str(folder))
    if not xml_file_path:
        return None

    coverage_polygon, xml_meta = UTILS.parse_xml_metadata(xml_file_path)
    if not coverage_polygon:
        return None

    tiff_path = choose_tiff(folder)
    if tiff_path is None:
        return None

    merged = merge_metadata(name_meta, xml_meta or {})
    tile_key = f"{match.group('lon')}_{match.group('lat')}"
    orbit_direction = str(merged.get("orbit_direction") or "").upper() or None
    group_key = "|".join(
        [
            str(merged.get("satellite") or ""),
            str(merged.get("imaging_mode") or ""),
            str(merged.get("polarization") or ""),
            str(orbit_direction or ""),
            tile_key,
        ]
    )
    satellite = str(merged.get("satellite") or "")
    imaging_date = str(merged.get("imaging_date") or "")

    return SceneRecord(
        folder_name=folder.name,
        folder_path=str(folder),
        folder_path_wsl=windows_to_wsl(folder),
        tiff_path=str(tiff_path),
        tiff_path_wsl=windows_to_wsl(tiff_path),
        meta_path=str(xml_file_path),
        meta_path_wsl=windows_to_wsl(xml_file_path),
        file_size_bytes=tiff_path.stat().st_size,
        satellite=satellite,
        imaging_date=imaging_date,
        imaging_mode=merged.get("imaging_mode"),
        polarization=merged.get("polarization"),
        orbit_direction=orbit_direction,
        satellite_mode=merged.get("satellite_mode"),
        receiving_station=merged.get("receiving_station"),
        orbit_circle=merged.get("orbit_circle"),
        scene_center_lon=merged.get("scene_center_lon"),
        scene_center_lat=merged.get("scene_center_lat"),
        acquisition_time_utc=merged.get("acquisition_time_utc"),
        product_type=merged.get("product_type"),
        product_level=merged.get("product_level"),
        product_unique_id=merged.get("product_unique_id"),
        tile_key=tile_key,
        group_key=group_key,
        orbit_txt_expected_name=f"{satellite}_GpsData_GAS_C_{imaging_date}.txt",
    )


def scan_scenes(root_dir: Path) -> List[SceneRecord]:
    scenes: List[SceneRecord] = []
    for entry in sorted(root_dir.iterdir()):
        if not entry.is_dir():
            continue
        scene = parse_scene(entry)
        if scene:
            scenes.append(scene)
    return scenes


def build_group_summary(scenes: List[SceneRecord]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[SceneRecord]] = {}
    for scene in scenes:
        groups.setdefault(scene.group_key, []).append(scene)

    summary: List[Dict[str, Any]] = []
    for key, items in groups.items():
        items.sort(key=lambda item: item.imaging_date)
        first = items[0]
        summary.append(
            {
                "group_key": key,
                "count": len(items),
                "satellite": first.satellite,
                "imaging_mode": first.imaging_mode,
                "polarization": first.polarization,
                "orbit_direction": first.orbit_direction,
                "tile_key": first.tile_key,
                "dates": [item.imaging_date for item in items],
                "receiving_stations": sorted({item.receiving_station for item in items if item.receiving_station}),
            }
        )
    summary.sort(key=lambda item: (-item["count"], item["group_key"]))
    return summary


def select_group(
    summary: List[Dict[str, Any]],
    tile_key: Optional[str],
    group_key: Optional[str],
    min_scenes: int,
) -> Optional[str]:
    if group_key:
        return group_key
    if tile_key:
        for item in summary:
            if item["tile_key"] == tile_key and item["count"] >= min_scenes:
                return item["group_key"]
        return None
    for item in summary:
        if item["count"] >= min_scenes:
            return item["group_key"]
    return None


def build_manifest(root_dir: Path, group_key: str, scenes: List[SceneRecord]) -> Dict[str, Any]:
    group_scenes = [scene for scene in scenes if scene.group_key == group_key]
    if not group_scenes:
        raise ValueError(f"Group not found: {group_key}")
    group_scenes.sort(key=lambda item: item.imaging_date)

    first = group_scenes[0]
    reference_index = len(group_scenes) // 2
    reference_scene = group_scenes[reference_index]
    slug = (
        f"{first.satellite.lower()}_"
        f"{(first.imaging_mode or 'unknown').lower()}_"
        f"{(first.polarization or 'unknown').lower()}_"
        f"{(first.orbit_direction or 'unknown').lower()}_"
        f"{first.tile_key.lower().replace('.', 'p')}"
    )

    scratch_root = _repo_root() / "experiments" / "isce2_sbas_timeseries" / "scratch" / slug
    scratch_root_wsl = windows_to_wsl(scratch_root)

    return {
        "source_root_windows": str(root_dir),
        "source_root_wsl": windows_to_wsl(root_dir),
        "group_key": group_key,
        "tile_key": first.tile_key,
        "scene_count": len(group_scenes),
        "reference_strategy": "middle_by_date",
        "reference_date": reference_scene.imaging_date,
        "stack_group": {
            "satellite": first.satellite,
            "imaging_mode": first.imaging_mode,
            "polarization": first.polarization,
            "orbit_direction": first.orbit_direction,
            "receiving_stations": sorted({item.receiving_station for item in group_scenes if item.receiving_station}),
        },
        "proposed_scratch_windows": str(scratch_root),
        "proposed_scratch_wsl": scratch_root_wsl,
        "proposed_layout": {
            "stack_input_manifest": f"{scratch_root_wsl}/stack_input_manifest.json",
            "slc_dir": f"{scratch_root_wsl}/SLC",
            "orbits_dir": f"{scratch_root_wsl}/orbits",
            "logs_dir": f"{scratch_root_wsl}/logs",
        },
        "stack_prep_assessment": {
            "current_scene_layout": "per_scene_folder_with_tiff_meta_rpc",
            "official_stripmapStack_expected_layout": "SLC/YYYYMMDD/YYYYMMDD.raw or YYYYMMDD.slc",
            "direct_compatibility": "unproven",
            "lt1_adapter_required_likely": True,
            "notes": [
                "Current repo can read these scene folders as RadarData assets.",
                "Official stripmapStack helper scripts do not advertise LT-1/LUTAN1 preparation hooks.",
                "A custom LT-1 stack preparation layer is likely needed before official stack execution.",
            ],
        },
        "scenes": [asdict(scene) for scene in group_scenes],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan LT-1 scene folders and build a dry-run SBAS stack-prep manifest."
    )
    parser.add_argument(
        "--root-dir",
        default=r"F:\Insar_data_pool_1",
        help="Windows root directory containing LT-1 scene folders.",
    )
    parser.add_argument(
        "--min-scenes",
        type=int,
        default=4,
        help="Minimum scenes required for candidate groups.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="How many candidate groups to print.",
    )
    parser.add_argument(
        "--tile-key",
        default=None,
        help="Pick one candidate by tile key, for example E123.3_N46.1.",
    )
    parser.add_argument(
        "--group-key",
        default=None,
        help="Pick one candidate by full group key.",
    )
    parser.add_argument(
        "--manifest-path",
        default=None,
        help="Optional JSON output path for the selected group's dry-run manifest.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root_dir = Path(args.root_dir)
    if not root_dir.exists():
        raise FileNotFoundError(f"Root directory does not exist: {root_dir}")

    scenes = scan_scenes(root_dir)
    summary = build_group_summary(scenes)

    print(f"scanned_scenes={len(scenes)}")
    print(f"candidate_groups={len(summary)}")
    print("top_candidates:")
    for item in summary[: args.top_n]:
        print(
            json.dumps(
                {
                    "count": item["count"],
                    "tile_key": item["tile_key"],
                    "group_key": item["group_key"],
                    "dates": item["dates"],
                    "receiving_stations": item["receiving_stations"],
                },
                ensure_ascii=False,
            )
        )

    selected_group = select_group(summary, args.tile_key, args.group_key, args.min_scenes)
    if not selected_group:
        print("selected_group=None")
        return 0

    manifest = build_manifest(root_dir, selected_group, scenes)
    print(f"selected_group={selected_group}")
    print(f"reference_date={manifest['reference_date']}")

    if args.manifest_path:
        manifest_path = Path(args.manifest_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"manifest_written={manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
