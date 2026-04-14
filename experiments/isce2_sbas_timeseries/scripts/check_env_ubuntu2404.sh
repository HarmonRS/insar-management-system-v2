#!/usr/bin/env bash
set -euo pipefail

CONDA_BIN="${CONDA_BIN:-/home/administrator/miniconda3/bin/conda}"
REPO_ROOT="${REPO_ROOT:-/mnt/z/Code/Insar_management_system_v2}"
EXP_ROOT="${EXP_ROOT:-$REPO_ROOT/experiments/isce2_sbas_timeseries}"

echo "== repo =="
echo "$REPO_ROOT"
test -d "$REPO_ROOT"

echo "== experiment root =="
echo "$EXP_ROOT"
test -d "$EXP_ROOT"

echo "== python3 =="
python3 --version

echo "== conda env list =="
"$CONDA_BIN" env list

echo "== isce2 runtime =="
"$CONDA_BIN" run -n isce2 python -c "import sys; import isce; print(sys.executable); print(isce.__file__)"

echo "== Lutan1 sensor module =="
"$CONDA_BIN" run -n isce2 python -c "from isce.components.isceobj.Sensor import Lutan1; print(Lutan1.__file__)"

echo "== mintpy import check =="
"$CONDA_BIN" run -n isce2 python -c "import importlib.util; print('mintpy:present' if importlib.util.find_spec('mintpy') else 'mintpy:missing')"

echo "== candidate ISCE stack directories =="
find /home/administrator/miniconda3/envs/isce2 -maxdepth 6 \
  \( -iname 'stripmapStack' -o -iname 'topsStack' -o -iname 'stack' \) 2>/dev/null || true
