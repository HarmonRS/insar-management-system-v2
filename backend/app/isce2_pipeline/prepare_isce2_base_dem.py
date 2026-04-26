#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .lt1_input_resolver import load_env_file
except ImportError:
    SCRIPT_DIR = Path(__file__).resolve().parent
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    from lt1_input_resolver import load_env_file  # type: ignore


@dataclass(frozen=True)
class DemResolution:
    source_label: str
    configured_value: str
    source_path: Path
    prepared_path: Path


def _configure_proj_environment() -> None:
    if os.environ.get("PROJ_DATA") and os.environ.get("PROJ_LIB"):
        return

    candidates: list[Path] = []

    isce2_python = str(os.environ.get("ISCE2_PYTHON") or "").strip()
    if isce2_python:
        candidates.append(Path(isce2_python).resolve().parents[1] / "share" / "proj")

    candidates.append(Path(sys.executable).resolve().parents[1] / "share" / "proj")

    for candidate in candidates:
        if candidate.exists():
            proj_path = candidate.as_posix()
            os.environ.setdefault("PROJ_DATA", proj_path)
            os.environ.setdefault("PROJ_LIB", proj_path)
            return


def normalize_linux_path(value: str | Path) -> Path:
    text = str(value or "").strip().strip('"').strip("'")
    if not text:
        return Path("")
    if text.startswith("\\\\"):
        raise ValueError("UNC paths are not supported directly. Mount them in WSL first.")

    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(text)


def linux_path_to_windows(path: Path) -> str:
    text = str(path)
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", text)
    if not match:
        return text
    drive = match.group(1).upper()
    rest = match.group(2).replace("/", "\\")
    return f"{drive}:\\{rest}"


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent.parent
    parser = argparse.ArgumentParser(
        description="Prepare a reusable WGS84 '.wgs84' DEM once for ISCE2 and PyINT."
    )
    parser.add_argument(
        "--source-dem",
        default=None,
        help="Optional source DEM base path. Accepts either a raw DEM base path or an existing .wgs84 path.",
    )
    parser.add_argument(
        "--env-file",
        default=str(repo_root / ".env"),
        help="Project .env file used to resolve default DEM paths.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild the prepared .wgs84 outputs even if they already exist.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve paths and print the planned output without modifying files.",
    )
    return parser.parse_args()


def _remove_prepare_outputs(base_path: Path) -> None:
    for suffix in ("", ".xml", ".vrt", ".hdr", ".aux.xml"):
        candidate = Path(str(base_path) + suffix)
        if candidate.exists():
            candidate.unlink()


def _ensure_source_xml(path: Path) -> None:
    if not Path(str(path) + ".xml").exists():
        raise FileNotFoundError(f"Missing DEM XML sidecar: {path}.xml")


def _maybe_prepared_path(path: Path) -> Path:
    return path if str(path).lower().endswith(".wgs84") else Path(str(path) + ".wgs84")


def _existing_path(path: Path) -> bool:
    return path.exists() and Path(str(path) + ".xml").exists()


def _resolve_from_value(label: str, value: str) -> DemResolution | None:
    normalized = normalize_linux_path(value)
    if not str(normalized):
        return None

    if _existing_path(normalized):
        if str(normalized).lower().endswith(".wgs84"):
            raw_candidate = normalized.with_suffix("")
            if _existing_path(raw_candidate):
                return DemResolution(label, value, raw_candidate, normalized)
            return DemResolution(label, value, normalized, normalized)
        return DemResolution(label, value, normalized, _maybe_prepared_path(normalized))

    if str(normalized).lower().endswith(".wgs84"):
        raw_candidate = normalized.with_suffix("")
        if _existing_path(raw_candidate):
            return DemResolution(label, value, raw_candidate, normalized)
    else:
        prepared_candidate = Path(str(normalized) + ".wgs84")
        if _existing_path(prepared_candidate):
            return DemResolution(label, value, normalized, prepared_candidate)
        if Path(str(normalized) + ".xml").exists():
            return DemResolution(label, value, normalized, prepared_candidate)
    return None


def resolve_dem_from_env(explicit_source: str | None, env_file: Path) -> DemResolution:
    env_values = load_env_file(env_file)
    candidates: list[tuple[str, str]] = []
    if explicit_source:
        candidates.append(("explicit", explicit_source))

    # Keep the raw source path explicit in .env via IDL_DINSAR_DEM_BASE_FILE.
    for key in (
        "IDL_DINSAR_DEM_BASE_FILE",
        "ISCE2_DEM_PATH",
        "PYINT_PREPARED_DEM_PATH",
    ):
        value = str(env_values.get(key) or "").strip()
        if value:
            candidates.append((key, value))

    for label, value in candidates:
        resolved = _resolve_from_value(label, value)
        if resolved is not None:
            return resolved

    raise FileNotFoundError(
        "Unable to resolve a DEM source from --source-dem, IDL_DINSAR_DEM_BASE_FILE, "
        "ISCE2_DEM_PATH, or PYINT_PREPARED_DEM_PATH."
    )


def inspect_dem(path: Path) -> dict[str, Any]:
    _configure_proj_environment()
    import isce  # noqa: F401
    import isceobj

    _ensure_source_xml(path)
    dem = isceobj.createDemImage()
    dem.load(str(path) + ".xml")
    return {
        "path": str(path),
        "reference": str(dem.reference or "").strip(),
        "width": int(dem.width or 0),
        "length": int(dem.length or 0),
        "first_lon": float(dem.coord1.coordStart or 0.0),
        "first_lat": float(dem.coord2.coordStart or 0.0),
        "delta_lon": float(dem.coord1.coordDelta or 0.0),
        "delta_lat": float(dem.coord2.coordDelta or 0.0),
    }


def ensure_prepared_dem(source_path: Path, prepared_path: Path, *, force: bool) -> dict[str, Any]:
    _configure_proj_environment()
    import isce  # noqa: F401
    import isceobj
    from iscesys.DataManager import createManager

    if source_path != prepared_path:
        _ensure_source_xml(source_path)

    if _existing_path(prepared_path) and not force:
        prepared_meta = inspect_dem(prepared_path)
        if prepared_meta["reference"].upper() != "WGS84":
            raise RuntimeError(
                f"Prepared DEM exists but is not WGS84: {prepared_path} ({prepared_meta['reference']})"
            )
        return {
            "action": "validated_existing",
            "source": inspect_dem(source_path),
            "prepared": prepared_meta,
        }

    if source_path == prepared_path:
        prepared_meta = inspect_dem(prepared_path)
        if prepared_meta["reference"].upper() != "WGS84":
            raise RuntimeError(
                f"Provided DEM is not WGS84: {prepared_path} ({prepared_meta['reference']})"
            )
        return {
            "action": "already_wgs84",
            "source": prepared_meta,
            "prepared": prepared_meta,
        }

    if force:
        _remove_prepare_outputs(prepared_path)

    source_dem = isceobj.createDemImage()
    source_dem.load(str(source_path) + ".xml")
    # Some DEM XML sidecars store only the basename. Force an absolute filename
    # so ISCE2 writes the generated ".wgs84" next to the source DEM, not in cwd.
    source_dem.filename = str(source_path)
    if not Path(str(source_path) + ".vrt").exists():
        source_dem.renderVRT()

    source_reference = str(source_dem.reference or "").strip().upper()
    if source_reference != "EGM96":
        raise RuntimeError(
            f"Expected an EGM96 raw DEM before preparation, got: {source_dem.reference or '<empty>'}"
        )

    dem_stitcher = createManager("dem1", "iscestitcher")
    dem_stitcher.noFilling = False
    prepared_dem = dem_stitcher.correct(source_dem)
    prepared_dem.metadatalocation = str(prepared_path) + ".xml"
    prepared_dem._extraFilename = str(prepared_path) + ".vrt"
    if not Path(prepared_dem.metadatalocation).exists():
        prepared_dem.dump(prepared_dem.metadatalocation)
    if not Path(prepared_dem._extraFilename).exists():
        prepared_dem.renderVRT()

    prepared_meta = inspect_dem(prepared_path)
    if prepared_meta["reference"].upper() != "WGS84":
        raise RuntimeError(
            f"Generated prepared DEM is not WGS84: {prepared_path} ({prepared_meta['reference']})"
        )

    return {
        "action": "converted",
        "source": inspect_dem(source_path),
        "prepared": prepared_meta,
    }


def build_report(resolution: DemResolution, outcome: dict[str, Any]) -> dict[str, Any]:
    source_windows = linux_path_to_windows(resolution.source_path)
    prepared_windows = linux_path_to_windows(resolution.prepared_path)
    return {
        "source_label": resolution.source_label,
        "configured_value": resolution.configured_value,
        "source_dem_wsl": str(resolution.source_path),
        "source_dem_windows": source_windows,
        "prepared_dem_wsl": str(resolution.prepared_path),
        "prepared_dem_windows": prepared_windows,
        "action": outcome["action"],
        "source_dem": outcome["source"],
        "prepared_dem": outcome["prepared"],
        "suggested_env": {
            "IDL_DINSAR_DEM_BASE_FILE": source_windows,
            "ISCE2_DEM_PATH": prepared_windows,
            "PYINT_PREPARED_DEM_PATH": prepared_windows,
        },
    }


def main() -> int:
    args = parse_args()
    env_file = normalize_linux_path(args.env_file)
    resolution = resolve_dem_from_env(args.source_dem, env_file)

    if args.dry_run:
        outcome = {
            "action": "dry_run",
            "source": inspect_dem(resolution.source_path),
            "prepared": inspect_dem(resolution.prepared_path)
            if _existing_path(resolution.prepared_path)
            else {"path": str(resolution.prepared_path), "reference": "", "width": 0, "length": 0},
        }
    else:
        outcome = ensure_prepared_dem(
            resolution.source_path,
            resolution.prepared_path,
            force=bool(args.force),
        )

    report = build_report(resolution, outcome)
    report_path = Path(str(resolution.prepared_path) + ".prepare_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Action:         {report['action']}")
    print(f"Source label:   {report['source_label']}")
    print(f"Source DEM:     {report['source_dem_windows']}")
    print(f"Prepared DEM:   {report['prepared_dem_windows']}")
    print(f"Report:         {linux_path_to_windows(report_path)}")
    print("Suggested .env values:")
    for key, value in report["suggested_env"].items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
