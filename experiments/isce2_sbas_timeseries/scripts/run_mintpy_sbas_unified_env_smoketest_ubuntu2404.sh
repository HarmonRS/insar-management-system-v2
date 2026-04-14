#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <smallbaseline-config-wsl> <mintpy-work-dir-wsl>" >&2
  exit 1
fi

CFG_PATH="$1"
WORK_DIR="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIFIED_RUNNER="$SCRIPT_DIR/run_mintpy_unified_env_ubuntu2404.sh"
PATCHED_APP="$SCRIPT_DIR/run_smallbaselineApp_patched.py"
STRICT_MASK_BUILDER="$SCRIPT_DIR/create_mintpy_all_ifgram_mask.py"

echo "MintPy SBAS unified-env smoketest"
echo "Config:   $CFG_PATH"
echo "Work dir: $WORK_DIR"

bash "$UNIFIED_RUNNER" python "$PATCHED_APP" "$CFG_PATH" --dir "$WORK_DIR" --dostep load_data

bash "$UNIFIED_RUNNER" python "$STRICT_MASK_BUILDER" \
  --ifgram-stack "$WORK_DIR/inputs/ifgramStack.h5" \
  --output "$WORK_DIR/maskAllValid.h5"

bash "$UNIFIED_RUNNER" python "$PATCHED_APP" "$CFG_PATH" --dir "$WORK_DIR" --start modify_network --end velocity
