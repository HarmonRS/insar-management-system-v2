from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


GAMMA_FLOAT32 = np.dtype(">f4")
ZERO_ERROR_RE = re.compile(r"number of zero values\s+(\d+)\s+in MLI1 image patch exceeds threshold:\s+(\d+)", re.IGNORECASE)


@dataclass
class GammaShape:
    width: int
    lines: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan init_offsetm patch positions for LT-1 DEM-assisted coreg.")
    parser.add_argument("run_root", help="Experiment run root, e.g. /mnt/d/.../run_20260420T093322Z")
    parser.add_argument("--case", dest="cases", action="append", default=[], help="Case name to scan. Can be repeated.")
    parser.add_argument("--date", dest="dates", action="append", default=[], help="Slave date to scan. Can be repeated.")
    parser.add_argument("--project", default="pyint_stage")
    parser.add_argument("--patch-size", type=int, default=512)
    parser.add_argument("--stride", type=int, default=256)
    parser.add_argument("--max-candidates", type=int, default=20)
    return parser.parse_args()


def parse_gamma_par_value(path: Path, key: str) -> str:
    prefix = key.strip() + ":"
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            _, _, tail = stripped.partition(":")
            return tail.strip().split()[0]
    raise ValueError(f"Missing key '{key}' in {path}")


def parse_gamma_shape(path: Path) -> GammaShape:
    width = int(float(parse_gamma_par_value(path, "range_samples")))
    lines = int(float(parse_gamma_par_value(path, "azimuth_lines")))
    return GammaShape(width=width, lines=lines)


def build_valid_mask(path: Path, shape: GammaShape) -> np.ndarray:
    arr = np.memmap(path, dtype=GAMMA_FLOAT32, mode="r", shape=(shape.lines, shape.width))
    valid = np.isfinite(arr) & (arr != 0)
    return np.asarray(valid, dtype=np.uint8)


def patch_sum(integral: np.ndarray, top: int, left: int, bottom: int, right: int) -> int:
    br = int(integral[bottom, right])
    tr = int(integral[top, right])
    bl = int(integral[bottom, left])
    tl = int(integral[top, left])
    return br - tr - bl + tl


def candidate_positions(mask: np.ndarray, *, patch_size: int, stride: int, max_candidates: int) -> list[dict[str, Any]]:
    lines, width = mask.shape
    patch_size = max(1, min(patch_size, lines, width))
    half = patch_size // 2
    valid_y = range(half, lines - (patch_size - half) + 1, max(1, stride))
    valid_x = range(half, width - (patch_size - half) + 1, max(1, stride))

    integral = np.pad(mask.astype(np.int64), ((1, 0), (1, 0)), mode="constant")
    integral = integral.cumsum(axis=0).cumsum(axis=1)
    patch_area = patch_size * patch_size

    center_y = lines // 2
    center_x = width // 2
    center_key = (center_x, center_y)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()

    for y in valid_y:
        for x in valid_x:
            top = y - half
            left = x - half
            bottom = top + patch_size
            right = left + patch_size
            nonzero = patch_sum(integral, top, left, bottom, right)
            ratio = float(nonzero / patch_area)
            entry = {
                "rpos": int(x),
                "azpos": int(y),
                "patch_nonzero_count": int(nonzero),
                "patch_nonzero_ratio": ratio,
                "is_center": bool((x, y) == center_key),
            }
            candidates.append(entry)

    candidates.sort(key=lambda item: item["patch_nonzero_ratio"], reverse=True)

    selected: list[dict[str, Any]] = []
    for entry in candidates:
        key = (entry["rpos"], entry["azpos"])
        if key in seen:
            continue
        selected.append(entry)
        seen.add(key)
        if len(selected) >= max_candidates:
            break

    if center_key not in seen:
        top = center_y - half
        left = center_x - half
        top = max(0, min(lines - patch_size, top))
        left = max(0, min(width - patch_size, left))
        bottom = top + patch_size
        right = left + patch_size
        nonzero = patch_sum(integral, top, left, bottom, right)
        selected.append(
            {
                "rpos": int(left + half),
                "azpos": int(top + half),
                "patch_nonzero_count": int(nonzero),
                "patch_nonzero_ratio": float(nonzero / patch_area),
                "is_center": True,
            }
        )

    for index, entry in enumerate(selected, start=1):
        entry["rank"] = index
    return selected


def parse_zero_error(text: str) -> dict[str, Any]:
    match = ZERO_ERROR_RE.search(text or "")
    if not match:
        return {}
    return {
        "zero_count": int(match.group(1)),
        "zero_threshold": int(match.group(2)),
    }


def run_init_offsetm(
    *,
    mli0: Path,
    samp: Path,
    diff0: Path,
    output_dir: Path,
    patch_size: int,
    rpos: int,
    azpos: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    diff_copy = output_dir / f"r{rpos}_a{azpos}.diff_par"
    shutil.copy2(diff0, diff_copy)

    cmd = [
        "init_offsetm",
        str(mli0),
        str(samp),
        str(diff_copy),
        "1",
        "1",
        str(int(rpos)),
        str(int(azpos)),
        "-",
        "-",
        "-",
        str(int(patch_size)),
        "0",
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    combined = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    payload = {
        "command": " ".join(cmd),
        "returncode": int(result.returncode),
        "ok": result.returncode == 0,
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "diff_par_copy": str(diff_copy),
    }
    payload.update(parse_zero_error(combined))
    return payload


def scan_case_date(
    *,
    run_root: Path,
    project: str,
    case_name: str,
    slave_date: str,
    patch_size: int,
    stride: int,
    max_candidates: int,
    output_root: Path,
) -> dict[str, Any]:
    case_root = run_root / case_name / project
    slc_dir = case_root / "SLC" / slave_date
    rslc_dir = case_root / "RSLC" / slave_date
    amp_par = slc_dir / f"{slave_date}_2rlks.amp.par"
    shape = parse_gamma_shape(amp_par)
    mask = build_valid_mask(rslc_dir / "mli0", shape)

    candidates = candidate_positions(mask, patch_size=patch_size, stride=stride, max_candidates=max_candidates)
    output_dir = output_root / case_name / slave_date
    results = []
    for candidate in candidates:
        command_result = run_init_offsetm(
            mli0=rslc_dir / "mli0",
            samp=slc_dir / f"{slave_date}_2rlks.amp",
            diff0=rslc_dir / "diff0",
            output_dir=output_dir / "diff_par",
            patch_size=patch_size,
            rpos=int(candidate["rpos"]),
            azpos=int(candidate["azpos"]),
        )
        row = dict(candidate)
        row.update(command_result)
        results.append(row)

    success_count = sum(1 for item in results if item["ok"])
    best_ratio = max((float(item["patch_nonzero_ratio"]) for item in results), default=0.0)
    best_zero = min((int(item.get("zero_count", 10**18)) for item in results if "zero_count" in item), default=None)
    payload = {
        "case": case_name,
        "slave_date": slave_date,
        "shape": {"width": shape.width, "lines": shape.lines},
        "patch_size": int(patch_size),
        "stride": int(stride),
        "candidate_count": len(results),
        "success_count": int(success_count),
        "best_patch_nonzero_ratio": best_ratio,
        "best_zero_count": best_zero,
        "results": results,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "scan_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def write_summary_tsv(path: Path, summaries: list[dict[str, Any]]) -> None:
    header = [
        "case",
        "slave_date",
        "rank",
        "is_center",
        "rpos",
        "azpos",
        "patch_nonzero_ratio",
        "returncode",
        "ok",
        "zero_count",
        "zero_threshold",
    ]
    lines = ["\t".join(header)]
    for summary in summaries:
        for row in summary["results"]:
            values: list[str] = []
            for key in header:
                if key in {"case", "slave_date"}:
                    value = summary[key]
                else:
                    value = row.get(key)
                if isinstance(value, float):
                    values.append(f"{value:.6f}")
                elif value is None:
                    values.append("")
                else:
                    values.append(str(value))
            lines.append("\t".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root).resolve()
    case_names = tuple(args.cases) if args.cases else ("case_A_baseline", "case_C_precise_orbit_rewrite")
    slave_dates = tuple(args.dates) if args.dates else ("20230624", "20230920")
    output_root = run_root / "scan_init_offsetm_patch"
    output_root.mkdir(parents=True, exist_ok=True)

    if not shutil.which("init_offsetm"):
        raise RuntimeError("init_offsetm is not available in PATH")

    summaries = []
    for case_name in case_names:
        for slave_date in slave_dates:
            summaries.append(
                scan_case_date(
                    run_root=run_root,
                    project=args.project,
                    case_name=case_name,
                    slave_date=slave_date,
                    patch_size=int(args.patch_size),
                    stride=int(args.stride),
                    max_candidates=int(args.max_candidates),
                    output_root=output_root,
                )
            )

    (output_root / "scan_summary.json").write_text(
        json.dumps({"run_root": str(run_root), "summaries": summaries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary_tsv(output_root / "scan_summary.tsv", summaries)
    print(json.dumps({"run_root": str(run_root), "output_root": str(output_root), "scan_count": len(summaries)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
