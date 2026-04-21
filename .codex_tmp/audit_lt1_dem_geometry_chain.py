from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


MASTER_DATE = "20230726"
SLAVE_DATES = ("20230624", "20230920")
PATCH_SIZE = 512
GAMMA_FLOAT32 = np.dtype(">f4")


@dataclass
class GammaImageShape:
    width: int
    lines: int


def parse_gamma_par_value(path: Path, key: str) -> str:
    prefix = key.strip() + ":"
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            _, _, tail = stripped.partition(":")
            return tail.strip().split()[0]
    raise ValueError(f"Missing key '{key}' in {path}")


def parse_gamma_shape(path: Path) -> GammaImageShape:
    width = int(float(parse_gamma_par_value(path, "range_samples")))
    lines = int(float(parse_gamma_par_value(path, "azimuth_lines")))
    return GammaImageShape(width=width, lines=lines)


def read_float32_image(path: Path, shape: GammaImageShape) -> np.ndarray:
    arr = np.fromfile(path, dtype=GAMMA_FLOAT32)
    expected = shape.width * shape.lines
    if arr.size != expected:
        raise ValueError(f"Unexpected size for {path}: expected {expected}, got {arr.size}")
    return arr.reshape(shape.lines, shape.width)


def read_lt0_lookup(path: Path, shape: GammaImageShape) -> np.ndarray:
    arr = np.fromfile(path, dtype=GAMMA_FLOAT32)
    expected = shape.width * shape.lines * 2
    if arr.size != expected:
        raise ValueError(f"Unexpected lt0 size for {path}: expected {expected}, got {arr.size}")
    return arr.reshape(shape.lines, shape.width, 2)


def center_slice(size: int, patch: int) -> slice:
    patch = min(size, patch)
    start = max(0, (size - patch) // 2)
    stop = start + patch
    return slice(start, stop)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except Exception:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def summarize_array(arr: np.ndarray, *, patch_size: int = PATCH_SIZE) -> dict[str, Any]:
    finite = np.isfinite(arr)
    zeros = finite & (arr == 0)
    nonzero = finite & (arr != 0)

    ys = center_slice(arr.shape[0], patch_size)
    xs = center_slice(arr.shape[1], patch_size)
    patch = arr[ys, xs]
    patch_finite = np.isfinite(patch)
    patch_zeros = patch_finite & (patch == 0)
    patch_nonzero = patch_finite & (patch != 0)

    nz = arr[nonzero]
    patch_nz = patch[patch_nonzero]
    stats: dict[str, Any] = {
        "shape": [int(arr.shape[0]), int(arr.shape[1])],
        "count": int(arr.size),
        "finite_count": int(finite.sum()),
        "zero_count": int(zeros.sum()),
        "zero_ratio": safe_float(zeros.sum() / arr.size if arr.size else None),
        "nonzero_count": int(nonzero.sum()),
        "center_patch_shape": [int(patch.shape[0]), int(patch.shape[1])],
        "center_patch_count": int(patch.size),
        "center_patch_zero_count": int(patch_zeros.sum()),
        "center_patch_zero_ratio": safe_float(patch_zeros.sum() / patch.size if patch.size else None),
        "center_patch_nonzero_count": int(patch_nonzero.sum()),
    }
    if nz.size:
        stats.update(
            {
                "min_nonzero": safe_float(nz.min()),
                "max_nonzero": safe_float(nz.max()),
                "mean_nonzero": safe_float(nz.mean()),
                "std_nonzero": safe_float(nz.std()),
            }
        )
    if patch_nz.size:
        stats.update(
            {
                "center_patch_min_nonzero": safe_float(patch_nz.min()),
                "center_patch_max_nonzero": safe_float(patch_nz.max()),
                "center_patch_mean_nonzero": safe_float(patch_nz.mean()),
                "center_patch_std_nonzero": safe_float(patch_nz.std()),
            }
        )
    return stats


def summarize_lt0(arr: np.ndarray, *, patch_size: int = PATCH_SIZE) -> dict[str, Any]:
    rng = arr[:, :, 0]
    az = arr[:, :, 1]
    finite = np.isfinite(rng) & np.isfinite(az)
    zero_pair = finite & (rng == 0) & (az == 0)
    valid_pair = finite & (~zero_pair)
    magnitude = np.sqrt(np.square(rng, dtype=np.float64) + np.square(az, dtype=np.float64))

    ys = center_slice(arr.shape[0], patch_size)
    xs = center_slice(arr.shape[1], patch_size)
    patch_valid = valid_pair[ys, xs]
    patch_zero = zero_pair[ys, xs]
    patch_mag = magnitude[ys, xs][patch_valid]
    all_mag = magnitude[valid_pair]

    stats: dict[str, Any] = {
        "shape": [int(arr.shape[0]), int(arr.shape[1]), 2],
        "count": int(arr.shape[0] * arr.shape[1]),
        "valid_pair_count": int(valid_pair.sum()),
        "valid_pair_ratio": safe_float(valid_pair.sum() / valid_pair.size if valid_pair.size else None),
        "zero_pair_count": int(zero_pair.sum()),
        "zero_pair_ratio": safe_float(zero_pair.sum() / zero_pair.size if zero_pair.size else None),
        "center_patch_shape": [int(patch_valid.shape[0]), int(patch_valid.shape[1])],
        "center_patch_valid_pair_count": int(patch_valid.sum()),
        "center_patch_valid_pair_ratio": safe_float(patch_valid.sum() / patch_valid.size if patch_valid.size else None),
        "center_patch_zero_pair_count": int(patch_zero.sum()),
        "center_patch_zero_pair_ratio": safe_float(patch_zero.sum() / patch_zero.size if patch_zero.size else None),
    }
    if all_mag.size:
        stats.update(
            {
                "magnitude_min": safe_float(all_mag.min()),
                "magnitude_max": safe_float(all_mag.max()),
                "magnitude_mean": safe_float(all_mag.mean()),
                "magnitude_std": safe_float(all_mag.std()),
            }
        )
    if patch_mag.size:
        stats.update(
            {
                "center_patch_magnitude_min": safe_float(patch_mag.min()),
                "center_patch_magnitude_max": safe_float(patch_mag.max()),
                "center_patch_magnitude_mean": safe_float(patch_mag.mean()),
                "center_patch_magnitude_std": safe_float(patch_mag.std()),
            }
        )
    return stats


def summarize_overlap(a: np.ndarray, b: np.ndarray, *, patch_size: int = PATCH_SIZE) -> dict[str, Any]:
    if a.shape != b.shape:
        raise ValueError(f"Shape mismatch for overlap: {a.shape} vs {b.shape}")
    finite_a = np.isfinite(a)
    finite_b = np.isfinite(b)
    nz_a = finite_a & (a != 0)
    nz_b = finite_b & (b != 0)
    overlap = nz_a & nz_b

    ys = center_slice(a.shape[0], patch_size)
    xs = center_slice(a.shape[1], patch_size)
    patch_overlap = overlap[ys, xs]
    patch_nz_a = nz_a[ys, xs]
    patch_nz_b = nz_b[ys, xs]

    return {
        "shape": [int(a.shape[0]), int(a.shape[1])],
        "overlap_nonzero_count": int(overlap.sum()),
        "overlap_nonzero_ratio": safe_float(overlap.sum() / overlap.size if overlap.size else None),
        "a_nonzero_count": int(nz_a.sum()),
        "b_nonzero_count": int(nz_b.sum()),
        "center_patch_overlap_nonzero_count": int(patch_overlap.sum()),
        "center_patch_overlap_nonzero_ratio": safe_float(patch_overlap.sum() / patch_overlap.size if patch_overlap.size else None),
        "center_patch_a_nonzero_count": int(patch_nz_a.sum()),
        "center_patch_b_nonzero_count": int(patch_nz_b.sum()),
    }


def scale_to_u8(arr: np.ndarray) -> np.ndarray:
    finite = np.isfinite(arr)
    valid = arr[finite & (arr != 0)]
    if valid.size == 0:
        return np.zeros(arr.shape, dtype=np.uint8)
    lo = np.percentile(valid, 1)
    hi = np.percentile(valid, 99)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(valid.min())
        hi = float(valid.max()) if valid.size else lo + 1.0
        if hi <= lo:
            hi = lo + 1.0
    scaled = np.clip((arr - lo) / (hi - lo), 0, 1)
    scaled[~finite] = 0
    scaled[arr == 0] = 0
    return np.round(scaled * 255.0).astype(np.uint8)


def write_pgm(path: Path, arr_u8: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"P5\n{arr_u8.shape[1]} {arr_u8.shape[0]}\n255\n".encode("ascii")
    with path.open("wb") as fp:
        fp.write(header)
        fp.write(arr_u8.tobytes())


def write_quicklook(path: Path, arr: np.ndarray) -> None:
    write_pgm(path, scale_to_u8(arr))


def case_root(run_root: Path, case_name: str) -> Path:
    return run_root / case_name / "pyint_stage"


def audit_case(run_root: Path, case_name: str, out_root: Path) -> dict[str, Any]:
    case_dir = case_root(run_root, case_name)
    dem_dir = case_dir / "DEM"
    out_case_root = out_root / case_name
    out_case_root.mkdir(parents=True, exist_ok=True)

    master_shape = parse_gamma_shape(dem_dir / f"{MASTER_DATE}_2rlks.amp.par")
    hgtsim = read_float32_image(dem_dir / f"{MASTER_DATE}_2rlks.rdc.dem", master_shape)
    lt0_quicklook_written = False

    result: dict[str, Any] = {
        "case": case_name,
        "master_date": MASTER_DATE,
        "master_shape": {"width": master_shape.width, "lines": master_shape.lines},
        "dem": {
            "hgtsim": summarize_array(hgtsim),
        },
        "slaves": {},
    }

    write_quicklook(out_case_root / "hgtsim.pgm", hgtsim)

    for slave_date in SLAVE_DATES:
        slc_dir = case_dir / "SLC" / slave_date
        rslc_dir = case_dir / "RSLC" / slave_date
        slave_shape = parse_gamma_shape(slc_dir / f"{slave_date}_2rlks.amp.par")
        samp = read_float32_image(slc_dir / f"{slave_date}_2rlks.amp", slave_shape)
        mli0 = read_float32_image(rslc_dir / "mli0", slave_shape)
        lt0 = read_lt0_lookup(rslc_dir / "lt0", master_shape)

        lt0_mag = np.sqrt(np.square(lt0[:, :, 0], dtype=np.float64) + np.square(lt0[:, :, 1], dtype=np.float64))

        slave_out = out_case_root / slave_date
        slave_out.mkdir(parents=True, exist_ok=True)
        write_quicklook(slave_out / "samp.pgm", samp)
        write_quicklook(slave_out / "mli0.pgm", mli0)
        if not lt0_quicklook_written:
            write_quicklook(out_case_root / "lt0_magnitude.pgm", lt0_mag.astype(np.float32))
            lt0_quicklook_written = True

        slave_summary = {
            "shape": {"width": slave_shape.width, "lines": slave_shape.lines},
            "samp": summarize_array(samp),
            "mli0": summarize_array(mli0),
            "lt0": summarize_lt0(lt0),
            "mli0_samp_overlap": summarize_overlap(mli0, samp),
        }
        result["slaves"][slave_date] = slave_summary

        (slave_out / "summary.json").write_text(
            json.dumps(slave_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    (out_case_root / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def build_summary_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.append(
        {
            "case": result["case"],
            "date": result["master_date"],
            "artifact": "hgtsim",
            "zero_ratio": result["dem"]["hgtsim"].get("zero_ratio"),
            "center_patch_zero_ratio": result["dem"]["hgtsim"].get("center_patch_zero_ratio"),
            "overlap_ratio": None,
            "center_patch_overlap_ratio": None,
        }
    )
    for slave_date, payload in result["slaves"].items():
        for artifact in ("samp", "mli0"):
            stats = payload[artifact]
            rows.append(
                {
                    "case": result["case"],
                    "date": slave_date,
                    "artifact": artifact,
                    "zero_ratio": stats.get("zero_ratio"),
                    "center_patch_zero_ratio": stats.get("center_patch_zero_ratio"),
                    "overlap_ratio": None,
                    "center_patch_overlap_ratio": None,
                }
            )
        lt0 = payload["lt0"]
        rows.append(
            {
                "case": result["case"],
                "date": slave_date,
                "artifact": "lt0",
                "zero_ratio": lt0.get("zero_pair_ratio"),
                "center_patch_zero_ratio": lt0.get("center_patch_zero_pair_ratio"),
                "overlap_ratio": lt0.get("valid_pair_ratio"),
                "center_patch_overlap_ratio": lt0.get("center_patch_valid_pair_ratio"),
            }
        )
        overlap = payload["mli0_samp_overlap"]
        rows.append(
            {
                "case": result["case"],
                "date": slave_date,
                "artifact": "mli0_samp_overlap",
                "zero_ratio": None,
                "center_patch_zero_ratio": None,
                "overlap_ratio": overlap.get("overlap_nonzero_ratio"),
                "center_patch_overlap_ratio": overlap.get("center_patch_overlap_nonzero_ratio"),
            }
        )
    return rows


def write_summary_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    header = [
        "case",
        "date",
        "artifact",
        "zero_ratio",
        "center_patch_zero_ratio",
        "overlap_ratio",
        "center_patch_overlap_ratio",
    ]
    lines = ["\t".join(header)]
    for row in rows:
        values = []
        for key in header:
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
    if len(sys.argv) < 2:
        print("usage: audit_lt1_dem_geometry_chain.py <run_root> [case ...]", file=sys.stderr)
        return 2

    run_root = Path(sys.argv[1]).resolve()
    case_names = tuple(sys.argv[2:]) if len(sys.argv) > 2 else ("case_A_baseline", "case_C_precise_orbit_rewrite")
    out_root = run_root / "audit_dem_geometry"
    out_root.mkdir(parents=True, exist_ok=True)

    all_results = []
    all_rows: list[dict[str, Any]] = []
    for case_name in case_names:
        result = audit_case(run_root, case_name, out_root)
        all_results.append(result)
        all_rows.extend(build_summary_rows(result))

    (out_root / "audit_summary.json").write_text(
        json.dumps({"run_root": str(run_root), "results": all_results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_summary_tsv(out_root / "audit_summary.tsv", all_rows)

    print(json.dumps({"run_root": str(run_root), "output_dir": str(out_root), "case_count": len(case_names)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
