#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <scratch_root_wsl> <run_file_name>" >&2
  echo "Example: $0 /mnt/z/Code/Insar_management_system_v2/experiments/isce2_sbas_timeseries/scratch/lt1a_strip1_hh_descending_e123p3_n46p1 run_01_reference" >&2
  exit 1
fi

SCRATCH_ROOT="$1"
RUN_FILE_NAME="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_ROOT="${CONDA_ROOT:-/home/administrator/miniconda3}"
CONDA_ENV="${CONDA_ENV:-insar_wsl_v1}"
CONDA_BIN="${CONDA_BIN:-$CONDA_ROOT/bin/conda}"
ISCE2_SHARE="${ISCE2_SHARE:-$CONDA_ROOT/envs/$CONDA_ENV/share/isce2}"
STRIPMAP_STACK_DIR="${STRIPMAP_STACK_DIR:-$ISCE2_SHARE/stripmapStack}"
SYNTHETIC_WATERMASK_SCRIPT="${SYNTHETIC_WATERMASK_SCRIPT:-$SCRIPT_DIR/create_synthetic_watermask.py}"
ALLOW_SYNTHETIC_WATERMASK="${ALLOW_SYNTHETIC_WATERMASK:-1}"
STACK_WORK="$SCRATCH_ROOT/stack_work"
RUN_FILE="$STACK_WORK/run_files/$RUN_FILE_NAME"
LOG_DIR="$STACK_WORK/logs"
LOG_FILE="$LOG_DIR/$RUN_FILE_NAME.log"

if [[ ! -x "$CONDA_BIN" ]]; then
  echo "Missing conda binary: $CONDA_BIN" >&2
  exit 1
fi

if [[ ! -f "$RUN_FILE" ]]; then
  echo "Run file not found: $RUN_FILE" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

export PYTHONPATH="$STRIPMAP_STACK_DIR:$ISCE2_SHARE${PYTHONPATH:+:$PYTHONPATH}"
export PATH="$STRIPMAP_STACK_DIR:$PATH"

recover_reference_watermask() {
  local like_image="$STACK_WORK/geom_reference/shadowMask.rdr"
  local output_mask="$STACK_WORK/geom_reference/waterMask.rdr"
  local report_path="$LOG_DIR/$RUN_FILE_NAME.synthetic_watermask.json"
  local watermask_failure_pattern='Please create a \.netrc file|Running: createWaterMask|DataRetriever - ERROR|There was a problem in retrieving the file|SRTMSWBD\.003|SWBD'

  if [[ "$RUN_FILE_NAME" != "run_01_reference" ]]; then
    return 1
  fi

  if [[ "$ALLOW_SYNTHETIC_WATERMASK" != "1" ]]; then
    return 1
  fi

  if [[ ! -f "$LOG_FILE" ]]; then
    return 1
  fi

  # Recover only the known offline water-mask failure modes observed in this
  # experiment: missing Earthdata credentials or SWBD retrieval failure.
  if ! grep -Eq "$watermask_failure_pattern" "$LOG_FILE"; then
    return 1
  fi

  if [[ ! -f "$like_image" || ! -f "$like_image.xml" ]]; then
    echo "Synthetic water-mask fallback could not find template image: $like_image" >&2
    return 1
  fi

  if [[ ! -f "$SYNTHETIC_WATERMASK_SCRIPT" ]]; then
    echo "Synthetic water-mask helper script not found: $SYNTHETIC_WATERMASK_SCRIPT" >&2
    return 1
  fi

  echo "Earthdata credentials are unavailable. Creating a synthetic all-land water mask."
  "$CONDA_BIN" run -n "$CONDA_ENV" python "$SYNTHETIC_WATERMASK_SCRIPT" \
    --like-image "$like_image" \
    --output "$output_mask" \
    --fill-value 1 \
    --force \
    --report "$report_path"
}

echo "Executing stripmap stack run file"
echo "Scratch root: $SCRATCH_ROOT"
echo "Run file:     $RUN_FILE"
echo "Log file:     $LOG_FILE"
echo "Conda env:    $CONDA_ENV"
echo "Conda bin:    $CONDA_BIN"
echo "ISCE2 share:  $ISCE2_SHARE"
echo "PYTHONPATH:   $PYTHONPATH"
echo "PATH prefix:  $STRIPMAP_STACK_DIR"

set -o pipefail
"$CONDA_BIN" run -n "$CONDA_ENV" bash "$RUN_FILE" 2>&1 | tee "$LOG_FILE"
RUN_STATUS=${PIPESTATUS[0]}

if [[ "$RUN_STATUS" -eq 0 ]]; then
  exit 0
fi

if recover_reference_watermask; then
  echo "Recovered $RUN_FILE_NAME with a synthetic all-land water mask."
  exit 0
fi

exit "$RUN_STATUS"
