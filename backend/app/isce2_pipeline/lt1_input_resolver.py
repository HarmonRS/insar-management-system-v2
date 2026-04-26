#!/usr/bin/env python3
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

try:
    from .convert_lt1_orbit_to_isce_xml import (
        build_xml,
        clip_vectors,
        parse_annotation_window,
        parse_orbit_file,
    )
except ImportError:
    SCRIPT_DIR = Path(__file__).resolve().parent
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    from convert_lt1_orbit_to_isce_xml import (  # type: ignore
        build_xml,
        clip_vectors,
        parse_annotation_window,
        parse_orbit_file,
    )


PathTransform = Callable[[str | Path], Path]


DEFAULT_WINDOWS_DEM_CANDIDATES = (
    r"D:\SRTM30m\SRTMDEM_RSP_SARscape.wgs84",
    r"D:\SRTM30m\SRTMDEM_RSP_SARscape",
)
DEFAULT_WSL_DEM_CANDIDATES = (
    "/mnt/d/SRTM30m/SRTMDEM_RSP_SARscape.wgs84",
    "/mnt/d/SRTM30m/SRTMDEM_RSP_SARscape",
)
DEFAULT_WINDOWS_ORBIT_POOL_CANDIDATES = (r"D:\orbit_pools\isce2",)


@dataclass(frozen=True)
class OrbitXmlResolution:
    path: Path
    source: str
    source_txt: Optional[Path] = None


def identity_path_transform(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(str(value))


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def parse_scene_window(meta_path: Path, margin_sec: float = 0.0) -> tuple:
    return parse_annotation_window(meta_path, margin_sec)


def resolve_orbit_pool_path(
    explicit_path: str | Path | None = None,
    env_values: Mapping[str, str] | None = None,
    extra_candidates: Iterable[str | Path] | None = None,
    default_candidates: Iterable[str | Path] | None = None,
    path_transform: PathTransform = identity_path_transform,
) -> Optional[Path]:
    candidates: list[str | Path] = []
    if explicit_path:
        candidates.append(explicit_path)
    if extra_candidates:
        candidates.extend(extra_candidates)

    if env_values:
        for key in ("ORBIT_POOL_ISCE2", "ISCE2_ORBIT_DIR"):
            value = env_values.get(key)
            if value:
                candidates.append(value)

    if default_candidates:
        candidates.extend(default_candidates)

    return resolve_existing_directory(candidates, path_transform=path_transform)


def resolve_prepared_dem_path(
    explicit_path: str | Path | None = None,
    env_values: Mapping[str, str] | None = None,
    extra_candidates: Iterable[str | Path] | None = None,
    default_candidates: Iterable[str | Path] | None = None,
    path_transform: PathTransform = identity_path_transform,
) -> Optional[Path]:
    candidates: list[str | Path] = []
    if explicit_path:
        candidates.extend(_prepared_dem_variants(explicit_path))
    if extra_candidates:
        for candidate in extra_candidates:
            candidates.extend(_prepared_dem_variants(candidate))

    if env_values:
        env_dem = env_values.get("ISCE2_DEM_PATH")
        if env_dem:
            candidates.extend(_prepared_dem_variants(env_dem))

        env_base = env_values.get("IDL_DINSAR_DEM_BASE_FILE")
        if env_base:
            if str(env_base).lower().endswith(".wgs84"):
                candidates.extend(_prepared_dem_variants(env_base))
            else:
                candidates.extend((f"{env_base}.wgs84", env_base))

    if default_candidates:
        for candidate in default_candidates:
            candidates.extend(_prepared_dem_variants(candidate))

    return resolve_existing_prepared_file(candidates, path_transform=path_transform)


def resolve_existing_directory(
    candidates: Iterable[str | Path],
    path_transform: PathTransform = identity_path_transform,
) -> Optional[Path]:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = path_transform(candidate)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and path.is_dir():
            return path
    return None


def resolve_existing_prepared_file(
    candidates: Iterable[str | Path],
    path_transform: PathTransform = identity_path_transform,
) -> Optional[Path]:
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = path_transform(candidate)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists() and Path(str(path) + ".xml").exists():
            return path
    return None


def ensure_lt1_orbit_xml(
    date_yyyymmdd: str,
    satellite: str,
    annotation_xml: Path,
    orbit_root: Path,
    orbit_output_dir: Path,
    margin_sec: float,
) -> OrbitXmlResolution:
    stem = build_lt1_orbit_stem(satellite=satellite, date_yyyymmdd=date_yyyymmdd)
    existing_xml = find_existing_lt1_orbit_xml(
        date_yyyymmdd=date_yyyymmdd,
        satellite=satellite,
        orbit_root=orbit_root,
        orbit_output_dir=orbit_output_dir,
    )
    if existing_xml is not None:
        return OrbitXmlResolution(path=existing_xml, source="existing_xml")

    txt_path = find_existing_lt1_orbit_text(
        date_yyyymmdd=date_yyyymmdd,
        satellite=satellite,
        orbit_root=orbit_root,
    )
    if txt_path is None:
        raise FileNotFoundError(
            "Missing LT-1 precise orbit source. "
            f"Searched under: {orbit_root} for {stem}.xml/.txt"
        )

    orbit_output_dir.mkdir(parents=True, exist_ok=True)
    xml_path = orbit_output_dir / f"{stem}.xml"
    vectors = parse_orbit_file(txt_path)
    start_time, stop_time = parse_annotation_window(annotation_xml, margin_sec)
    clipped = clip_vectors(vectors, start_time, stop_time)
    tree = build_xml(clipped)
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    return OrbitXmlResolution(path=xml_path, source="generated_from_txt", source_txt=txt_path)


def find_existing_lt1_orbit_xml(
    date_yyyymmdd: str,
    satellite: str,
    orbit_root: Path,
    orbit_output_dir: Path | None = None,
) -> Optional[Path]:
    stem = build_lt1_orbit_stem(satellite=satellite, date_yyyymmdd=date_yyyymmdd)
    candidates = []
    if orbit_output_dir is not None:
        candidates.append(orbit_output_dir / f"{stem}.xml")
    candidates.extend(
        [
            orbit_root / f"{stem}.xml",
            orbit_root / satellite.upper() / f"{stem}.xml",
            orbit_root / "converted" / "isce2" / f"{stem}.xml",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def find_existing_lt1_orbit_text(
    date_yyyymmdd: str,
    satellite: str,
    orbit_root: Path,
) -> Optional[Path]:
    stem = build_lt1_orbit_stem(satellite=satellite, date_yyyymmdd=date_yyyymmdd)
    candidates = [
        orbit_root / satellite.upper() / f"{stem}.txt",
        orbit_root / f"{stem}.txt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def build_lt1_orbit_stem(satellite: str, date_yyyymmdd: str) -> str:
    return f"{satellite.upper()}_GpsData_GAS_C_{date_yyyymmdd}"


def _prepared_dem_variants(value: str | Path) -> tuple[str | Path, ...]:
    text = str(value).strip()
    if not text:
        return ()
    if text.lower().endswith(".wgs84"):
        return (value,)
    return (f"{text}.wgs84", value)
