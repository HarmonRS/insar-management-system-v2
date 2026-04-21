#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
ISCE2_PIPELINE_DIR = SCRIPT_DIR.parent / "isce2_pipeline"
if str(ISCE2_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(ISCE2_PIPELINE_DIR))

from convert_lt1_orbit_to_isce_xml import StateVector, parse_orbit_file  # type: ignore


TRUE_VALUES = {"1", "true", "yes", "on"}
VECTOR_POS_RE = re.compile(r"^state_vector_position_(\d+):")
VECTOR_VEL_RE = re.compile(r"^state_vector_velocity_(\d+):")


@dataclass
class ParsedSlcPar:
    path: Path
    lines: List[str]
    trailing_newline: bool
    acquisition_date: date
    number_of_state_vectors: int
    time_of_first_state_vector: float
    state_vector_interval: float
    position_line_indexes: Dict[int, int]
    velocity_line_indexes: Dict[int, int]


def utc_now_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def read_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in TRUE_VALUES


def windows_path_to_wsl_mount(path: str) -> str:
    text = str(path or "").strip().strip('"').strip("'")
    if not text:
        return ""
    normalized = text.replace("\\", "/")
    if normalized.startswith("/"):
        return normalized
    if normalized.startswith("//"):
        return ""
    match = re.match(r"^([A-Za-z]):/(.*)$", normalized)
    if not match:
        return normalized
    drive_letter = match.group(1).lower()
    tail = match.group(2).lstrip("/")
    return f"/mnt/{drive_letter}/{tail}"


def resolve_existing_path(path: str) -> Optional[Path]:
    text = str(path or "").strip()
    if not text:
        return None
    direct = Path(text)
    if direct.exists():
        return direct.resolve()
    converted = windows_path_to_wsl_mount(text)
    if converted:
        candidate = Path(converted)
        if candidate.exists():
            return candidate.resolve()
    return None


def load_json_file(path: Path | None) -> Dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply LT-1 precise orbit TXT to Gamma .slc.par state vectors.")
    parser.add_argument("--date", required=True, help="Scene date in YYYYMMDD format.")
    parser.add_argument("--manifest-json", default=os.getenv("PYINT_LT1_PRECISE_ORBIT_MANIFEST", ""), help="task_manifest.json path.")
    parser.add_argument("--summary-json", default="", help="Summary JSON path. Defaults to <slc_dir>/orbit_bridge_summary.json.")
    parser.add_argument("--role", choices=("auto", "master", "slave"), default="auto")
    parser.add_argument("--operation-tag", default="raw2slc")
    parser.add_argument("--mode", default=os.getenv("PYINT_LT1_PRECISE_ORBIT_MODE", "replace"))
    parser.add_argument("--slc-par", dest="slc_par_files", action="append", default=[], help="Target .slc.par or .slc.update.par file.")
    parser.add_argument("--backup", dest="backup", action="store_true")
    parser.add_argument("--no-backup", dest="backup", action="store_false")
    parser.add_argument("--strict", dest="strict", action="store_true")
    parser.add_argument("--no-strict", dest="strict", action="store_false")
    parser.add_argument("--validate-with-orb-filt", dest="validate_with_orb_filt", action="store_true")
    parser.add_argument("--no-validate-with-orb-filt", dest="validate_with_orb_filt", action="store_false")
    parser.add_argument(
        "--orb-filt-degree",
        type=int,
        default=int(str(os.getenv("PYINT_LT1_PRECISE_ORBIT_ORB_FILT_DEGREE", "5")).strip() or "5"),
    )
    parser.set_defaults(
        backup=read_bool(os.getenv("PYINT_LT1_PRECISE_ORBIT_BACKUP"), True),
        strict=read_bool(os.getenv("PYINT_LT1_PRECISE_ORBIT_STRICT"), True),
        validate_with_orb_filt=read_bool(os.getenv("PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT"), False),
    )
    return parser.parse_args()


def get_orbits_payload(manifest: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(manifest.get("orbits"), dict):
        return manifest["orbits"]
    input_assets = manifest.get("input_assets")
    if isinstance(input_assets, dict) and isinstance(input_assets.get("orbits"), dict):
        return input_assets["orbits"]
    return {}


def resolve_orbit_entry(manifest: Dict[str, Any], date_text: str, role: str) -> Dict[str, Any]:
    orbits = get_orbits_payload(manifest)
    candidates: List[tuple[str, Dict[str, Any]]] = []
    for role_name in ("master", "slave"):
        item = orbits.get(role_name)
        if isinstance(item, dict):
            candidates.append((role_name, item))

    if role in {"master", "slave"}:
        item = dict(orbits.get(role) or {})
        if not item:
            raise RuntimeError(f"Missing orbit entry for role={role}")
        item["role"] = role
        return item

    matched: List[Dict[str, Any]] = []
    for role_name, item in candidates:
        item_date = str(item.get("date") or "").strip()
        expected_name = str(item.get("expected_name") or "").strip()
        if item_date == date_text or date_text in expected_name:
            candidate = dict(item)
            candidate["role"] = role_name
            matched.append(candidate)

    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise RuntimeError(f"Unable to match precise orbit entry for date={date_text}")
    raise RuntimeError(f"Ambiguous precise orbit entries for date={date_text}")


def resolve_orbit_txt_path(entry: Dict[str, Any]) -> Path:
    for key in ("staged_path", "path"):
        candidate = resolve_existing_path(str(entry.get(key) or ""))
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"Precise orbit TXT does not exist: expected {entry.get('expected_name') or '<unknown>'}"
    )


def parse_float_field(lines: Iterable[str], prefix: str) -> float:
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        _, _, value = stripped.partition(":")
        first_token = value.strip().split()[0]
        return float(first_token)
    raise ValueError(f"Missing field: {prefix}")


def parse_int_field(lines: Iterable[str], prefix: str) -> int:
    return int(round(parse_float_field(lines, prefix)))


def parse_slc_date(lines: Iterable[str]) -> date:
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("date:"):
            continue
        match = re.match(r"^date:\s+(\d+)\s+(\d+)\s+(\d+)", stripped)
        if not match:
            raise ValueError(f"Unable to parse date line: {stripped}")
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    raise ValueError("Missing date: field in .slc.par")


def parse_slc_par(path: Path) -> ParsedSlcPar:
    raw_text = path.read_text(encoding="utf-8", errors="ignore")
    trailing_newline = raw_text.endswith("\n")
    lines = raw_text.splitlines()
    acquisition_date = parse_slc_date(lines)
    number_of_state_vectors = parse_int_field(lines, "number_of_state_vectors")
    time_of_first_state_vector = parse_float_field(lines, "time_of_first_state_vector")
    state_vector_interval = parse_float_field(lines, "state_vector_interval")

    position_line_indexes: Dict[int, int] = {}
    velocity_line_indexes: Dict[int, int] = {}
    for idx, line in enumerate(lines):
        stripped = line.strip()
        pos_match = VECTOR_POS_RE.match(stripped)
        if pos_match:
            position_line_indexes[int(pos_match.group(1))] = idx
            continue
        vel_match = VECTOR_VEL_RE.match(stripped)
        if vel_match:
            velocity_line_indexes[int(vel_match.group(1))] = idx

    missing_positions = [index for index in range(1, number_of_state_vectors + 1) if index not in position_line_indexes]
    missing_velocities = [index for index in range(1, number_of_state_vectors + 1) if index not in velocity_line_indexes]
    if missing_positions or missing_velocities:
        raise ValueError(
            "Incomplete state vector block in .slc.par: "
            f"missing positions={missing_positions[:5]}, missing velocities={missing_velocities[:5]}"
        )

    return ParsedSlcPar(
        path=path,
        lines=lines,
        trailing_newline=trailing_newline,
        acquisition_date=acquisition_date,
        number_of_state_vectors=number_of_state_vectors,
        time_of_first_state_vector=time_of_first_state_vector,
        state_vector_interval=state_vector_interval,
        position_line_indexes=position_line_indexes,
        velocity_line_indexes=velocity_line_indexes,
    )


def build_target_times(parsed: ParsedSlcPar) -> List[datetime]:
    start_time = datetime(parsed.acquisition_date.year, parsed.acquisition_date.month, parsed.acquisition_date.day)
    return [
        start_time + timedelta(seconds=parsed.time_of_first_state_vector + parsed.state_vector_interval * index)
        for index in range(parsed.number_of_state_vectors)
    ]


def norm3(values: Iterable[float]) -> float:
    items = [float(item) for item in values]
    return math.sqrt(sum(item * item for item in items))


def interpolate_state_vector(target_time: datetime, vectors: List[StateVector]) -> StateVector:
    if not vectors:
        raise ValueError("No precise orbit vectors available for interpolation")

    times = [vector.time for vector in vectors]
    if target_time < times[0] or target_time > times[-1]:
        raise ValueError(
            f"Target time {target_time.isoformat()} is outside orbit range {times[0].isoformat()} - {times[-1].isoformat()}"
        )

    right_index = bisect.bisect_left(times, target_time)
    if right_index < len(vectors) and times[right_index] == target_time:
        return vectors[right_index]
    if right_index == 0:
        return vectors[0]
    if right_index >= len(vectors):
        return vectors[-1]

    left = vectors[right_index - 1]
    right = vectors[right_index]
    interval_seconds = (right.time - left.time).total_seconds()
    if interval_seconds <= 0:
        raise ValueError("Orbit vectors are not strictly increasing in time")

    offset_seconds = (target_time - left.time).total_seconds()
    u = offset_seconds / interval_seconds

    h00 = 2 * u * u * u - 3 * u * u + 1
    h10 = u * u * u - 2 * u * u + u
    h01 = -2 * u * u * u + 3 * u * u
    h11 = u * u * u - u * u

    dh00 = 6 * u * u - 6 * u
    dh10 = 3 * u * u - 4 * u + 1
    dh01 = -6 * u * u + 6 * u
    dh11 = 3 * u * u - 2 * u

    p0 = (left.x, left.y, left.z)
    p1 = (right.x, right.y, right.z)
    v0 = (left.vx, left.vy, left.vz)
    v1 = (right.vx, right.vy, right.vz)

    position = []
    velocity = []
    for axis in range(3):
        pos = (
            h00 * p0[axis]
            + h10 * interval_seconds * v0[axis]
            + h01 * p1[axis]
            + h11 * interval_seconds * v1[axis]
        )
        vel = (
            dh00 * p0[axis]
            + dh10 * interval_seconds * v0[axis]
            + dh01 * p1[axis]
            + dh11 * interval_seconds * v1[axis]
        ) / interval_seconds
        position.append(pos)
        velocity.append(vel)

    return StateVector(
        time=target_time,
        x=position[0],
        y=position[1],
        z=position[2],
        vx=velocity[0],
        vy=velocity[1],
        vz=velocity[2],
    )


def format_position_line(index: int, vector: StateVector) -> str:
    return (
        f"state_vector_position_{index}:"
        f" {vector.x:14.4f} {vector.y:14.4f} {vector.z:14.4f}   m   m   m"
    )


def format_velocity_line(index: int, vector: StateVector) -> str:
    return (
        f"state_vector_velocity_{index}:"
        f" {vector.vx:13.5f} {vector.vy:13.5f} {vector.vz:13.5f}   m/s m/s m/s"
    )


def backup_slc_par(path: Path) -> str:
    backup_path = path.with_name(path.name + ".orbit_bridge.bak")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
    return str(backup_path)


def write_bridged_slc_par(
    parsed: ParsedSlcPar,
    vectors: List[StateVector],
    *,
    backup_enabled: bool,
) -> Dict[str, Any]:
    if len(vectors) != parsed.number_of_state_vectors:
        raise ValueError("Interpolated vector count does not match .slc.par state vector count")

    backup_path = ""
    if backup_enabled:
        backup_path = backup_slc_par(parsed.path)

    updated_lines = list(parsed.lines)
    for index, vector in enumerate(vectors, start=1):
        updated_lines[parsed.position_line_indexes[index]] = format_position_line(index, vector)
        updated_lines[parsed.velocity_line_indexes[index]] = format_velocity_line(index, vector)

    text = "\n".join(updated_lines)
    if parsed.trailing_newline:
        text += "\n"
    parsed.path.write_text(text, encoding="utf-8")

    return {
        "backup_path": backup_path,
    }


def run_orb_filt_validation(path: Path, degree: int) -> Dict[str, Any]:
    command = shutil.which("ORB_filt_spline.py")
    if not command:
        return {
            "requested": True,
            "ok": False,
            "status": "missing_command",
            "command": "ORB_filt_spline.py",
        }

    validate_path = path.with_name(path.name + ".orb_filt_validate.par")
    result = subprocess.run(
        [command, str(path), str(validate_path), "--degree", str(int(degree))],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not validate_path.exists():
        return {
            "requested": True,
            "ok": False,
            "status": "command_failed",
            "command": " ".join(result.args),
            "returncode": int(result.returncode),
            "stdout": (result.stdout or "")[-2000:],
            "stderr": (result.stderr or "")[-2000:],
            "output_par": str(validate_path),
        }

    current_parsed = parse_slc_par(path)
    validated_parsed = parse_slc_par(validate_path)
    position_corrections: List[float] = []
    velocity_corrections: List[float] = []
    for index in range(1, current_parsed.number_of_state_vectors + 1):
        cur_position = parse_vector_values(current_parsed.lines[current_parsed.position_line_indexes[index]])
        val_position = parse_vector_values(validated_parsed.lines[validated_parsed.position_line_indexes[index]])
        cur_velocity = parse_vector_values(current_parsed.lines[current_parsed.velocity_line_indexes[index]])
        val_velocity = parse_vector_values(validated_parsed.lines[validated_parsed.velocity_line_indexes[index]])
        position_corrections.append(norm3([val_position[i] - cur_position[i] for i in range(3)]))
        velocity_corrections.append(norm3([val_velocity[i] - cur_velocity[i] for i in range(3)]))

    return {
        "requested": True,
        "ok": True,
        "status": "ok",
        "command": " ".join(result.args),
        "returncode": int(result.returncode),
        "output_par": str(validate_path),
        "max_position_correction_m": max(position_corrections) if position_corrections else 0.0,
        "max_velocity_correction_mps": max(velocity_corrections) if velocity_corrections else 0.0,
    }


def parse_vector_values(line: str) -> List[float]:
    _, _, payload = line.partition(":")
    values: List[float] = []
    for token in payload.split():
        try:
            values.append(float(token))
        except ValueError:
            break
        if len(values) == 3:
            break
    if len(values) != 3:
        raise ValueError(f"Unable to parse state vector values from line: {line}")
    return values


def build_operation_record(args: argparse.Namespace, summary_path: Path, manifest_path: Path | None) -> Dict[str, Any]:
    return {
        "generated_at": utc_now_text(),
        "date": str(args.date or "").strip(),
        "role": args.role,
        "operation_tag": str(args.operation_tag or "").strip(),
        "mode": str(args.mode or "").strip(),
        "strict": bool(args.strict),
        "backup": bool(args.backup),
        "validate_with_orb_filt": bool(args.validate_with_orb_filt),
        "orb_filt_degree": int(args.orb_filt_degree),
        "manifest_json": str(manifest_path) if manifest_path else "",
        "summary_json": str(summary_path),
        "slc_par_files": [str(path) for path in args.slc_par_files],
        "ok": False,
        "error": "",
        "orbit_source": {},
        "results": [],
    }


def append_operation_summary(summary_path: Path, operation: Dict[str, Any]) -> None:
    existing = load_json_file(summary_path)
    operations = existing.get("operations")
    if not isinstance(operations, list):
        operations = []
    operations.append(operation)
    payload = {
        "generated_at": existing.get("generated_at") or utc_now_text(),
        "last_updated_at": utc_now_text(),
        "ok": all(bool(item.get("ok")) for item in operations),
        "operation_count": len(operations),
        "operations": operations,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_summary_path(slc_par_files: List[str]) -> Path:
    first_path = Path(slc_par_files[0]).resolve()
    return first_path.parent / "orbit_bridge_summary.json"


def main() -> int:
    args = parse_args()
    if not args.slc_par_files:
        raise SystemExit("--slc-par must be specified at least once")

    manifest_path = resolve_existing_path(args.manifest_json) if args.manifest_json else None
    summary_path = Path(args.summary_json).resolve() if args.summary_json else default_summary_path(args.slc_par_files)
    operation = build_operation_record(args, summary_path, manifest_path)

    exit_code = 0
    try:
        if manifest_path is None:
            raise FileNotFoundError("Precise orbit manifest JSON is not available")
        manifest = load_json_file(manifest_path)
        orbit_entry = resolve_orbit_entry(manifest, str(args.date or "").strip(), args.role)
        orbit_txt_path = resolve_orbit_txt_path(orbit_entry)
        orbit_vectors = sorted(parse_orbit_file(orbit_txt_path), key=lambda item: item.time)
        operation["orbit_source"] = {
            "role": orbit_entry.get("role"),
            "satellite": orbit_entry.get("satellite"),
            "date": orbit_entry.get("date"),
            "expected_name": orbit_entry.get("expected_name"),
            "source_txt": str(orbit_txt_path),
            "vector_count": len(orbit_vectors),
            "time_start": orbit_vectors[0].time.isoformat() if orbit_vectors else "",
            "time_stop": orbit_vectors[-1].time.isoformat() if orbit_vectors else "",
        }

        results: List[Dict[str, Any]] = []
        for slc_par_text in args.slc_par_files:
            slc_par_path = resolve_existing_path(slc_par_text)
            if slc_par_path is None or not slc_par_path.is_file():
                raise FileNotFoundError(f"Target .slc.par does not exist: {slc_par_text}")

            parsed = parse_slc_par(slc_par_path)
            target_times = build_target_times(parsed)
            bridged_vectors = [interpolate_state_vector(target_time, orbit_vectors) for target_time in target_times]
            write_info = write_bridged_slc_par(parsed, bridged_vectors, backup_enabled=bool(args.backup))
            validation = (
                run_orb_filt_validation(slc_par_path, args.orb_filt_degree)
                if args.validate_with_orb_filt
                else {"requested": False, "ok": True, "status": "skipped"}
            )
            result_item = {
                "path": str(slc_par_path),
                "status": "applied",
                "ok": bool(validation.get("ok", False)),
                "backup_path": write_info.get("backup_path", ""),
                "vector_count": parsed.number_of_state_vectors,
                "time_of_first_state_vector": parsed.time_of_first_state_vector,
                "state_vector_interval": parsed.state_vector_interval,
                "validation": validation,
                "first_target_time": target_times[0].isoformat() if target_times else "",
                "last_target_time": target_times[-1].isoformat() if target_times else "",
                "max_position_norm_m": max(norm3((vector.x, vector.y, vector.z)) for vector in bridged_vectors) if bridged_vectors else 0.0,
                "max_velocity_norm_mps": max(norm3((vector.vx, vector.vy, vector.vz)) for vector in bridged_vectors) if bridged_vectors else 0.0,
            }
            results.append(result_item)

        operation["results"] = results
        operation["ok"] = all(bool(item.get("ok")) for item in results)
        if not operation["ok"]:
            operation["error"] = "One or more target .slc.par files failed validation"
            if args.strict:
                exit_code = 1
    except Exception as exc:
        operation["error"] = str(exc)
        operation["ok"] = False
        exit_code = 1 if args.strict else 0

    append_operation_summary(summary_path, operation)
    if operation.get("error"):
        print(operation["error"], file=sys.stderr)
    else:
        applied_count = len(operation.get("results") or [])
        print(
            f"Applied LT-1 precise orbit bridge to {applied_count} file(s) for {operation.get('date')} "
            f"[{operation.get('operation_tag')}]"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
