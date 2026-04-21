from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def load_base_module(script_path: Path):
    spec = importlib.util.spec_from_file_location("audit_lt1_dem_geometry_chain_base", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load base audit script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_summary_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.append(
        {
            "root": result["root"],
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
                    "root": result["root"],
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
                "root": result["root"],
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
                "root": result["root"],
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
        "root",
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


def audit_root(run_root: Path, master_date: str, slave_dates: tuple[str, ...], project: str) -> dict[str, Any]:
    base_script = Path(__file__).with_name("audit_lt1_dem_geometry_chain.py")
    base = load_base_module(base_script)

    project_root = run_root / project
    dem_dir = project_root / "DEM"
    out_root = run_root / "audit_dem_geometry"
    out_root.mkdir(parents=True, exist_ok=True)

    master_shape = base.parse_gamma_shape(dem_dir / f"{master_date}_2rlks.amp.par")
    hgtsim = base.read_float32_image(dem_dir / f"{master_date}_2rlks.rdc.dem", master_shape)

    result: dict[str, Any] = {
        "root": str(run_root),
        "project": project,
        "master_date": master_date,
        "master_shape": {"width": master_shape.width, "lines": master_shape.lines},
        "dem": {
            "hgtsim": base.summarize_array(hgtsim),
        },
        "slaves": {},
    }

    base.write_quicklook(out_root / "hgtsim.pgm", hgtsim)
    lt0_quicklook_written = False

    for slave_date in slave_dates:
        slc_dir = project_root / "SLC" / slave_date
        rslc_dir = project_root / "RSLC" / slave_date
        slave_shape = base.parse_gamma_shape(slc_dir / f"{slave_date}_2rlks.amp.par")
        samp = base.read_float32_image(slc_dir / f"{slave_date}_2rlks.amp", slave_shape)
        mli0 = base.read_float32_image(rslc_dir / "mli0", slave_shape)
        lt0 = base.read_lt0_lookup(rslc_dir / "lt0", master_shape)

        lt0_mag = (lt0[:, :, 0].astype("float64") ** 2 + lt0[:, :, 1].astype("float64") ** 2) ** 0.5

        slave_out = out_root / slave_date
        slave_out.mkdir(parents=True, exist_ok=True)
        base.write_quicklook(slave_out / "samp.pgm", samp)
        base.write_quicklook(slave_out / "mli0.pgm", mli0)
        if not lt0_quicklook_written:
            base.write_quicklook(out_root / "lt0_magnitude.pgm", lt0_mag.astype("float32"))
            lt0_quicklook_written = True

        slave_summary = {
            "shape": {"width": slave_shape.width, "lines": slave_shape.lines},
            "samp": base.summarize_array(samp),
            "mli0": base.summarize_array(mli0),
            "lt0": base.summarize_lt0(lt0),
            "mli0_samp_overlap": base.summarize_overlap(mli0, samp),
        }
        result["slaves"][slave_date] = slave_summary
        (slave_out / "summary.json").write_text(
            json.dumps(slave_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    (out_root / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = build_summary_rows(result)
    write_summary_tsv(out_root / "audit_summary.tsv", rows)
    return result


def main() -> int:
    if len(sys.argv) < 4:
        print(
            "usage: audit_lt1_pool_multiscene_root.py <run_root> <master_date> <slave_date> [slave_date ...] [--project name]",
            file=sys.stderr,
        )
        return 2

    args = list(sys.argv[1:])
    project = "pyint_stage"
    if "--project" in args:
        idx = args.index("--project")
        try:
            project = args[idx + 1]
        except IndexError as exc:
            raise SystemExit("--project requires a value") from exc
        del args[idx : idx + 2]

    run_root = Path(args[0]).resolve()
    master_date = args[1]
    slave_dates = tuple(args[2:])

    result = audit_root(run_root, master_date, slave_dates, project)
    print(
        json.dumps(
            {
                "run_root": str(run_root),
                "output_dir": str(run_root / "audit_dem_geometry"),
                "master_date": master_date,
                "slave_count": len(slave_dates),
                "project": project,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
