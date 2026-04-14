#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <mintpy-command> [args...]" >&2
  echo "Example: $0 prep_isce.py -h" >&2
  exit 1
fi

CONDA_BIN="${CONDA_BIN:-/home/administrator/miniconda3/bin/conda}"
MINTPY_ENV="${MINTPY_ENV:-isce2_mintpy}"

if [[ ! -x "$CONDA_BIN" ]]; then
  echo "Missing conda binary: $CONDA_BIN" >&2
  exit 1
fi

echo "MintPy command in unified env"
echo "Conda:      $CONDA_BIN"
echo "Target env: $MINTPY_ENV"
echo "Command:    $*"

"$CONDA_BIN" run -n "$MINTPY_ENV" "$@"
