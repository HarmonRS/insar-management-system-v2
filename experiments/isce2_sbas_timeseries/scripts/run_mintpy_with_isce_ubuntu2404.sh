#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <mintpy-command> [args...]" >&2
  echo "Example: $0 prep_isce.py -h" >&2
  exit 1
fi

CONDA_BIN="${CONDA_BIN:-/home/administrator/miniconda3/bin/conda}"
MINTPY_ENV="${MINTPY_ENV:-mintpy}"
ISCE_SITE_PACKAGES="${ISCE_SITE_PACKAGES:-/home/administrator/miniconda3/envs/isce2/lib/python3.11/site-packages}"
ISCE_PACKAGE_DIR="${ISCE_PACKAGE_DIR:-$ISCE_SITE_PACKAGES/isce}"
ISCE_BRIDGE_DIR="${ISCE_BRIDGE_DIR:-$HOME/.cache/mintpy_isce_bridge}"

if [[ ! -x "$CONDA_BIN" ]]; then
  echo "Missing conda binary: $CONDA_BIN" >&2
  exit 1
fi

if [[ ! -d "$ISCE_SITE_PACKAGES" ]]; then
  echo "Missing ISCE site-packages directory: $ISCE_SITE_PACKAGES" >&2
  exit 1
fi

if [[ ! -d "$ISCE_PACKAGE_DIR" ]]; then
  echo "Missing ISCE package directory: $ISCE_PACKAGE_DIR" >&2
  exit 1
fi

mkdir -p "$ISCE_BRIDGE_DIR"
ln -sfn "$ISCE_PACKAGE_DIR" "$ISCE_BRIDGE_DIR/isce"

# Bridge only the top-level ISCE package into the MintPy env.
# The package itself extends sys.path to its internal components on import,
# which avoids shadowing MintPy's own numpy/h5py stack with the isce2 env.
export PYTHONPATH="$ISCE_BRIDGE_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "MintPy command bridge"
echo "Conda:        $CONDA_BIN"
echo "MintPy env:   $MINTPY_ENV"
echo "ISCE bridge:  $ISCE_BRIDGE_DIR -> $ISCE_PACKAGE_DIR"
echo "Command:      $*"

"$CONDA_BIN" run -n "$MINTPY_ENV" "$@"
