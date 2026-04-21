from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np


def summarize_float_raster(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"exists": False}

    arr = np.fromfile(path, dtype=np.float32)
    if arr.size == 0:
        return {"exists": True, "count": 0}

    finite = arr[np.isfinite(arr)]
    zeros = int((finite == 0).sum())
    nz = finite[finite != 0]
    result: dict[str, object] = {
        "exists": True,
        "count": int(finite.size),
        "zero_ratio": float(zeros / finite.size) if finite.size else None,
        "nonzero_count": int(nz.size),
    }
    if nz.size:
        result.update(
            {
                "min_nonzero": float(nz.min()),
                "max_nonzero": float(nz.max()),
                "mean_nonzero": float(nz.mean()),
                "std_nonzero": float(nz.std()),
            }
        )
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: summarize_pyint_case.py <case_root>", file=sys.stderr)
        return 2

    case_root = Path(sys.argv[1]).resolve()
    output_root = case_root / "output"
    summary_path = output_root / "pyint_run_summary.json"
    if not summary_path.is_file():
        print(json.dumps({"case_root": str(case_root), "summary_exists": False}, ensure_ascii=False, indent=2))
        return 1

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    pair_dir = Path(payload["copied_outputs"]["pair_dir"])

    pair_name = str(payload["pair_name"])
    rlks = int(payload["range_looks"])
    look_text = f"{rlks}rlks"

    files = {
        "diff_filt": pair_dir / f"{pair_name}_{look_text}.diff_filt",
        "cor": pair_dir / f"{pair_name}_{look_text}.diff_filt.cor",
        "unw": pair_dir / f"{pair_name}_{look_text}.diff_filt.unw",
        "los": pair_dir / f"{pair_name}_{look_text}.los_disp",
        "vert": pair_dir / f"{pair_name}_{look_text}.vert_disp",
        "geo_unw": pair_dir / f"geo_{pair_name}_{look_text}.diff_filt.unw",
        "geo_los": pair_dir / f"geo_{pair_name}_{look_text}.los_disp",
        "geo_vert": pair_dir / f"geo_{pair_name}_{look_text}.vert_disp",
    }

    result = {
        "case_root": str(case_root),
        "ok": payload.get("ok"),
        "project_name": payload.get("project_name"),
        "pair_dir": str(pair_dir),
        "files": {name: summarize_float_raster(path) for name, path in files.items()},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
