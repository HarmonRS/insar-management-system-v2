#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .lt1_input_resolver import repair_dem_sidecar_paths
except ImportError:
    SCRIPT_DIR = Path(__file__).resolve().parent
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    from lt1_input_resolver import repair_dem_sidecar_paths  # type: ignore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit and optionally repair moved ISCE DEM XML sidecars."
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Directory containing DEM files and sidecars",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Write repaired file_name / metadata_location / extra_file_name values back to XML",
    )
    return parser.parse_args()


def iter_dem_sidecars(root: Path) -> list[Path]:
    sidecars: list[Path] = []
    for xml_path in sorted(root.rglob("*.xml")):
        if xml_path.name.lower().endswith(".aux.xml"):
            continue
        dem_path = Path(str(xml_path)[:-4])
        if dem_path.exists():
            sidecars.append(dem_path)
    return sidecars


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"DEM root directory not found: {root}")

    sidecars = iter_dem_sidecars(root)
    changed_count = 0
    mismatch_count = 0

    print(f"DEM root: {root}")
    print(f"Sidecars: {len(sidecars)}")
    for dem_path in sidecars:
        report = repair_dem_sidecar_paths(dem_path, write_changes=bool(args.repair))
        updated_fields = list(report.get("updated_fields") or [])
        if updated_fields:
            changed_count += 1
            mismatch_count += 1
            print(
                f"[fixed] {report['xml_path']} -> {', '.join(updated_fields)}"
                if args.repair
                else f"[mismatch] {report['xml_path']} -> {', '.join(updated_fields)}"
            )
            continue

        current = report.get("current") or {}
        expected = report.get("expected") or {}
        mismatched = [
            key
            for key, expected_value in expected.items()
            if expected_value and str(current.get(key) or "").strip() != str(expected_value).strip()
        ]
        if mismatched:
            mismatch_count += 1
            print(f"[mismatch] {report['xml_path']} -> {', '.join(mismatched)}")
        else:
            print(f"[ok] {report['xml_path']}")

    print(f"Mismatched: {mismatch_count}")
    if args.repair:
        print(f"Repaired:   {changed_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
