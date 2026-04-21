#!/usr/bin/env bash
set -euo pipefail

REPO='/mnt/d/Code/Insar_management_system_v2'
RUN_ROOT='/mnt/d/PyINT_POOL_TEST/LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_cases/run_20260420T093322Z'
PYTHON_BIN='/home/administrator/miniconda3/envs/isce2/bin/python'
SCAN_SCRIPT="$REPO/.codex_tmp/scan_init_offsetm_patch.py"
GAMMA_ENV="$REPO/backend/app/pyint_pipeline/pyint_gamma_env.sh"

. "$GAMMA_ENV" >/dev/null 2>&1
export PATH="/usr/local/GAMMA_SOFTWARE-20240627/ISP/bin:/usr/local/GAMMA_SOFTWARE-20240627/ISP/scripts:$PATH"

"$PYTHON_BIN" "$SCAN_SCRIPT" "$RUN_ROOT" "$@"
