#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "usage: $0 YYYYMMDD" >&2
  exit 1
fi

DATE_TEXT="$1"
ROOT='/mnt/d/PyINT_POOL_TEST/LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene'
PROJECT_DIR="$ROOT/pyint_stage"
DOWNLOAD_DIR="$PROJECT_DIR/DOWNLOAD"
CURRENT_SLC_DIR="$PROJECT_DIR/SLC/$DATE_TEXT"
COMPARE_ROOT="$ROOT/import_compare/$DATE_TEXT"
LIST_FILE="$COMPARE_ROOT/t_$DATE_TEXT"
CURRENT_PAR="$CURRENT_SLC_DIR/$DATE_TEXT.slc.par"
GAMMA_ENV='/mnt/d/Code/Insar_management_system_v2/backend/app/pyint_pipeline/pyint_gamma_env.sh'

die() {
  echo "$1" >&2
  exit 1
}

[ -f "$CURRENT_PAR" ] || die "Current .slc.par not found: $CURRENT_PAR"
[ -f "$GAMMA_ENV" ] || die "Gamma env script not found: $GAMMA_ENV"

SCENE_TIFF="$(find "$DOWNLOAD_DIR" -maxdepth 1 -type f -name "LT1*${DATE_TEXT}*.tiff" | sort | head -n 1)"
[ -n "$SCENE_TIFF" ] || die "Scene TIFF not found for date: $DATE_TEXT"

. "$GAMMA_ENV" >/dev/null 2>&1
mkdir -p "$COMPARE_ROOT"
printf '%s\n' "$SCENE_TIFF" >"$LIST_FILE"

pushd "$COMPARE_ROOT" >/dev/null
SCENE_XML="${SCENE_TIFF%.tiff}.meta.xml"
[ -f "$SCENE_XML" ] || die "Scene meta xml not found: $SCENE_XML"
par_LT1_SLC "$SCENE_TIFF" "$SCENE_XML" "./$DATE_TEXT.slc.par" "./$DATE_TEXT.slc" > import.stdout.log 2> import.stderr.log
par_LT1_SLC_YSLi "$SCENE_TIFF" "$SCENE_XML" "./$DATE_TEXT.slc.update" "./$DATE_TEXT.slc.update.par" 0 >> import.stdout.log 2>> import.stderr.log
popd >/dev/null

ALT_PAR="$COMPARE_ROOT/$DATE_TEXT.slc.par"
[ -f "$ALT_PAR" ] || die "Alternate .slc.par not found: $ALT_PAR"

python3 - "$ALT_PAR" "$COMPARE_ROOT/$DATE_TEXT.slc.update.par" <<'PY'
import sys
from pathlib import Path

main_path = Path(sys.argv[1])
update_path = Path(sys.argv[2])

main_lines = main_path.read_text(encoding="utf-8", errors="ignore").splitlines()
update_lines = update_path.read_text(encoding="utf-8", errors="ignore").splitlines()

prefixes = ("number_of_state_vectors:", "time_of_first_state_vector:", "state_vector_interval:", "state_vector_position_", "state_vector_velocity_")
main_prefix = ("number_of_state_vectors:", "time_of_first_state_vector:", "state_vector_interval:", "state_vector_position_", "state_vector_velocity_")

filtered_main = [line for line in main_lines if not line.startswith(main_prefix)]
replacement = [line for line in update_lines if line.startswith(prefixes)]

main_path.write_text("\n".join(filtered_main + replacement) + "\n", encoding="utf-8")
PY

python3 - "$CURRENT_PAR" "$ALT_PAR" <<'PY'
import re
import sys
from pathlib import Path

keys = [
    "center_latitude",
    "center_longitude",
    "start_time",
    "center_time",
    "end_time",
    "azimuth_line_time",
    "range_samples",
    "azimuth_lines",
    "range_pixel_spacing",
    "azimuth_pixel_spacing",
    "near_range_slc",
    "center_range_slc",
    "far_range_slc",
    "incidence_angle",
    "heading",
    "prf",
    "azimuth_proc_bandwidth",
    "doppler_polynomial",
    "number_of_state_vectors",
    "time_of_first_state_vector",
    "state_vector_interval",
]

vector_patterns = [
    "state_vector_position_1",
    "state_vector_velocity_1",
    "state_vector_position_2",
    "state_vector_velocity_2",
    "state_vector_position_3",
    "state_vector_velocity_3",
]

def read_map(path: Path):
    data = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        for key in keys + vector_patterns:
            if line.startswith(key + ":"):
                data[key] = line.split(":", 1)[1].strip()
                break
    return data

cur = read_map(Path(sys.argv[1]))
alt = read_map(Path(sys.argv[2]))
all_keys = keys + vector_patterns
for key in all_keys:
    cur_v = cur.get(key, "")
    alt_v = alt.get(key, "")
    if cur_v != alt_v:
        print(f"{key}\n  current: {cur_v}\n  alt:     {alt_v}")
PY
