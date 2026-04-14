#!/usr/bin/env bash
set -euo pipefail

CONDA_BIN="${CONDA_BIN:-/home/administrator/miniconda3/bin/conda}"
CONDA_ENV="${CONDA_ENV:-isce2}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
PACKAGES=(
  matplotlib
)

if [[ ! -x "$CONDA_BIN" ]]; then
  echo "Missing conda binary: $CONDA_BIN" >&2
  exit 1
fi

echo "ISCE2 stack runtime bootstrap"
echo "Conda: $CONDA_BIN"
echo "Env:   $CONDA_ENV"
echo "Index: $PIP_INDEX_URL"

for pkg in "${PACKAGES[@]}"; do
  echo "Installing $pkg into $CONDA_ENV"
  "$CONDA_BIN" run -n "$CONDA_ENV" python -m pip install -i "$PIP_INDEX_URL" "$pkg"
done

echo "Runtime bootstrap complete"
