#! /usr/bin/env python
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from pyint import _utils as ut


INTRODUCTION = """
-------------------------------------------------------------------

   Generate radar-coordinates based DEM.
   [Geo-coordinates DEM can be downloaded automatically if not provided.]
"""

EXAMPLE = """Usage:

  makedem_pyint.py projectName --processor gamma
"""


def cmdLineParse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate radar-coordinates based DEM.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=INTRODUCTION + "\n" + EXAMPLE,
    )
    parser.add_argument("projectName", help="Name of project.")
    parser.add_argument(
        "-p",
        "--processor",
        dest="processor",
        choices={"gamma", "roi_pac"},
        default="gamma",
        help="Interferometry processor. [default: gamma]",
    )
    return parser.parse_args()


def _run_checked(command: list[str], *, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result


def _resolve_existing_dem_open_path(source_dem: str) -> Path:
    source_path = Path(source_dem)
    if source_path.is_file() and source_path.suffix.lower() == ".vrt":
        return source_path

    vrt_path = Path(str(source_path) + ".vrt")
    if vrt_path.is_file():
        return vrt_path

    if source_path.is_file():
        return source_path

    raise FileNotFoundError(f"Prepared DEM source does not exist: {source_dem}")


def _resolve_research_bbox_from_slc_par(slc_par: str) -> tuple[int, int, int, int]:
    result = _run_checked(["SLC_corners", slc_par])
    lines = result.stdout.splitlines()
    if len(lines) < 10:
        raise RuntimeError(f"Unexpected SLC_corners output for {slc_par}")

    lat_line = lines[8].rstrip()
    lon_line = lines[9].rstrip()
    min_lat = float(lat_line.split(":")[1].split("  max. ")[0])
    max_lat = float(lat_line.split(":")[2])
    min_lon = float(lon_line.split(":")[1].split("  max. ")[0])
    max_lon = float(lon_line.split(":")[2])

    north = int(max_lat) + 2
    south = int(min_lat)
    east = int(max_lon) + 2
    west = int(min_lon)
    return west, south, east, north


def _cleanup_temp_outputs(paths: list[Path]) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            continue


def _build_dem_from_existing_source(
    *,
    project_name: str,
    processor: str,
    slc_par: str,
    source_dem: str,
    work_dir: Path,
) -> None:
    west, south, east, north = _resolve_research_bbox_from_slc_par(slc_par)
    source_open_path = _resolve_existing_dem_open_path(source_dem)
    clipped_tif = work_dir / f"{project_name}.prepared_source_clip.tif"
    clipped_aux = Path(str(clipped_tif) + ".aux.xml")

    print(f"Using prepared DEM source: {source_dem}")
    print(f"Clipping prepared DEM window: west={west}, south={south}, east={east}, north={north}")

    _run_checked(
        [
            "gdal_translate",
            "-projwin",
            str(west),
            str(north),
            str(east),
            str(south),
            "-of",
            "GTiff",
            str(source_open_path),
            str(clipped_tif),
        ],
        cwd=str(work_dir),
    )
    _run_checked(
        [
            "makedem.py",
            "-d",
            str(clipped_tif),
            "-p",
            processor,
            "-o",
            project_name,
        ],
        cwd=str(work_dir),
    )
    _cleanup_temp_outputs([clipped_tif, clipped_aux])


def main(argv: list[str]) -> None:
    inps = cmdLineParse()
    projectName = inps.projectName
    processor = inps.processor

    scratchDir = os.getenv("SCRATCHDIR")
    slcDir = scratchDir + "/" + projectName + "/SLC"
    templateDir = os.getenv("TEMPLATEDIR")
    templateFile = templateDir + "/" + projectName + ".template"
    templateDict = ut.update_template(templateFile)

    masterDate = templateDict["masterDate"]
    SLC_PAR = slcDir + "/" + masterDate + "/" + masterDate + ".slc.par"

    demDir = os.getenv("DEMDIR")
    demDir1 = demDir + "/" + projectName
    if not os.path.isdir(demDir1):
        os.mkdir(demDir1)

    os.chdir(demDir1)
    work_dir = Path(demDir1)

    prepared_dem_source = str(templateDict.get("prepared_dem_source", "") or "").strip()
    if prepared_dem_source not in {"", "-"}:
        _build_dem_from_existing_source(
            project_name=projectName,
            processor=processor,
            slc_par=SLC_PAR,
            source_dem=prepared_dem_source,
            work_dir=work_dir,
        )
        print(f"Generate DEM for project {projectName} is done.")
        sys.exit(0)

    call_str = "makedem.py " + "-s " + SLC_PAR + " -p gamma " + " -o " + projectName

    if "fabdem_dir" in templateDict and templateDict["fabdem_dir"].strip() not in ["", "-"]:
        fabdem_dir = templateDict["fabdem_dir"].strip()
        if os.path.isdir(fabdem_dir):
            call_str += " --fabdem-dir " + fabdem_dir
            print("Using local FABDEM directory: %s" % fabdem_dir)

    if "opentopo_api_key" in templateDict and templateDict["opentopo_api_key"].strip() not in ["", "-"]:
        call_str += " --opentopo-api-key " + templateDict["opentopo_api_key"].strip()
    if "opentopo_dem_type" in templateDict and templateDict["opentopo_dem_type"].strip() not in ["", "-"]:
        call_str += " --opentopo-dem-type " + templateDict["opentopo_dem_type"].strip()

    print("Running: %s" % call_str)
    os.system(call_str)

    print("Generate DEM for project %s is done." % projectName)
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[:])
