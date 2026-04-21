#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-${1:-}}"
PROJECT="${PROJECT:-pyint_stage}"
MASTER_DATE="${MASTER_DATE:-}"
START_DATE="${START_DATE:-}"
END_DATE="${END_DATE:-}"
PREPARED_DEM="${PREPARED_DEM:-/mnt/d/DEM/COPDEM_GLO30_China_4326_DEM}"

SCRATCHDIR="$ROOT"
TEMPLATEDIR="$ROOT/templates"
DEMDIR="$ROOT/dem_store"
PROJECT_DIR="$SCRATCHDIR/$PROJECT"
LOG_DIR="$ROOT/logs"
PYINT_HOME='/mnt/d/Code/Insar_management_system_v2/third_party/PyINT'
PYINT_SCRIPT_DIR="$PYINT_HOME/pyint"
PYTHON_BIN='/home/administrator/miniconda3/envs/isce2/bin/python'
GAMMA_ENV='/mnt/d/Code/Insar_management_system_v2/backend/app/pyint_pipeline/pyint_gamma_env.sh'
TEMPLATE_PATH="$TEMPLATEDIR/$PROJECT.template"

die() {
  echo "$1" >&2
  exit 1
}

run_stage() {
  local stage="$1"
  shift
  local stdout_log="$LOG_DIR/${stage}.stdout.log"
  local stderr_log="$LOG_DIR/${stage}.stderr.log"
  echo "[stage] $stage"
  if "$@" >"$stdout_log" 2>"$stderr_log"; then
    echo "[ok] $stage"
  else
    local rc=$?
    echo "[fail] $stage (rc=$rc)" >&2
    echo "stdout: $stdout_log" >&2
    echo "stderr: $stderr_log" >&2
    exit "$rc"
  fi
}

run_single_coreg() {
  local slave_date="$1"
  mkdir -p "$PROJECT_DIR/SLC" "$PROJECT_DIR/RSLC" "$PROJECT_DIR/DEM" "$PROJECT_DIR/ifgrams"
  run_stage "coreg_${slave_date}" "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/coreg_gamma.py" "$PROJECT" "$slave_date"
}

[ -n "$ROOT" ] || die "ROOT is required"
[ -n "$MASTER_DATE" ] || die "MASTER_DATE is required"
[ -n "$START_DATE" ] || die "START_DATE is required"
[ -n "$END_DATE" ] || die "END_DATE is required"
[ -d "$ROOT" ] || die "Experiment root not found: $ROOT"
[ -d "$PROJECT_DIR/DOWNLOAD" ] || die "DOWNLOAD directory not found: $PROJECT_DIR/DOWNLOAD"
[ -f "$PREPARED_DEM" ] || die "Prepared DEM not found: $PREPARED_DEM"
[ -f "$GAMMA_ENV" ] || die "Gamma env script not found: $GAMMA_ENV"
[ -x "$PYTHON_BIN" ] || die "Python not found: $PYTHON_BIN"

. "$GAMMA_ENV" >/dev/null 2>&1
export SCRATCHDIR
export TEMPLATEDIR
export DEMDIR
export PATH="/home/administrator/miniconda3/envs/isce2/bin:$PYINT_SCRIPT_DIR:$PATH"
export PYTHONPATH="$PYINT_HOME${PYTHONPATH:+:$PYTHONPATH}"
export PYINT_LT1_PRECISE_ORBIT_ENABLED='false'
export PYINT_LT1_PRECISE_ORBIT_MODE='bridge'
export PYINT_LT1_PRECISE_ORBIT_STRICT='false'
export PYINT_LT1_PRECISE_ORBIT_VALIDATE_WITH_ORB_FILT='false'
export PYINT_LT1_PRECISE_ORBIT_BACKUP='false'
unset PYINT_LT1_PRECISE_ORBIT_HELPER
unset PYINT_LT1_PRECISE_ORBIT_MANIFEST

mkdir -p "$TEMPLATEDIR" "$DEMDIR" "$LOG_DIR"
mkdir -p "$PROJECT_DIR/SLC" "$PROJECT_DIR/RSLC" "$PROJECT_DIR/DEM" "$PROJECT_DIR/ifgrams"

cat >"$TEMPLATE_PATH" <<EOF
# Auto-generated LT-1 pool multiscene test
satelite=LT
masterDate=$MASTER_DATE
range_looks=2
azimuth_looks=2
download_data=0
raw2slc_all=1
raw2slc_all_parallel=1
coreg_all=1
coreg_all_parallel=1
select_pairs=1
network_method=sbas
startDate=$START_DATE
endDate=$END_DATE
max_tb=50000
max_sb=50000
min_tb=1
diff_all=0
unwrap_all=0
geocode_all=0
atmcor_all=0
load_data=0
prepared_dem_source=$PREPARED_DEM
fabdem_dir=-
opentopo_dem_type=-
opentopo_api_key=-
EOF

if [ "${2:-}" = "--single-coreg" ] || [ "${1:-}" = "--single-coreg" ]; then
  local_date="${3:-${2:-}}"
  [ -n "$local_date" ] || die "Usage: ROOT=... MASTER_DATE=... START_DATE=... END_DATE=... $0 --single-coreg YYYYMMDD"
  run_single_coreg "$local_date"
  echo "[done] single_coreg=$local_date"
  exit 0
fi

run_stage down2slc_all "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/down2slc_LT1_all.py" "$PROJECT" --parallel 1
run_stage makedem_pyint "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/makedem_pyint.py" "$PROJECT"
run_stage generate_rdc_dem "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/generate_rdc_dem.py" "$PROJECT"
run_stage coreg_gamma_all "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/coreg_gamma_all.py" "$PROJECT" --parallel 1
run_stage select_pairs "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/select_pairs.py" "$PROJECT"

echo "[done] project=$PROJECT"
echo "[done] root=$ROOT"
