#!/usr/bin/env bash
set -u
set -o pipefail

BASE_ROOT='/mnt/d/PyINT_POOL_TEST/LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene'
CASES_ROOT='/mnt/d/PyINT_POOL_TEST/LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_cases'
PROJECT='pyint_stage'
MASTER_DATE='20230726'
SLAVE_DATES=(20230624 20230920)
SATELLITE='LT1A'
ORBIT_DIR='/mnt/d/orbit_pools/envi/LT1A'

REPO='/mnt/d/Code/Insar_management_system_v2'
PYINT_HOME="$REPO/third_party/PyINT"
PYINT_SCRIPT_DIR="$PYINT_HOME/pyint"
PYTHON_BIN='/home/administrator/miniconda3/envs/isce2/bin/python'
GAMMA_ENV="$REPO/backend/app/pyint_pipeline/pyint_gamma_env.sh"
ORBIT_HELPER="$REPO/backend/app/pyint_pipeline/apply_lt1_precise_orbit.py"
GAMMA_SCRIPT_DIR='/usr/local/GAMMA_SOFTWARE-20240627/ISP/scripts'

RUN_STAMP="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ROOT="$CASES_ROOT/run_$RUN_STAMP"
STATUS_FILE="$RUN_ROOT/stage_status.tsv"

fail() {
  echo "$1" >&2
  exit 1
}

ensure_file() {
  local path="$1"
  [ -f "$path" ] || fail "Required file not found: $path"
}

ensure_dir() {
  local path="$1"
  [ -d "$path" ] || fail "Required directory not found: $path"
}

write_status() {
  local case_name="$1"
  local stage="$2"
  local rc="$3"
  local stdout_log="$4"
  local stderr_log="$5"
  printf '%s\t%s\t%s\t%s\t%s\n' "$case_name" "$stage" "$rc" "$stdout_log" "$stderr_log" >>"$STATUS_FILE"
}

run_stage() {
  local case_name="$1"
  local stage="$2"
  shift 2
  local stdout_log="$RUN_ROOT/$case_name/logs/${stage}.stdout.log"
  local stderr_log="$RUN_ROOT/$case_name/logs/${stage}.stderr.log"
  local rc=0

  echo "[stage] $case_name :: $stage"
  "$@" >"$stdout_log" 2>"$stderr_log" || rc=$?
  write_status "$case_name" "$stage" "$rc" "$stdout_log" "$stderr_log"

  if [ "$rc" -eq 0 ]; then
    echo "[ok] $case_name :: $stage"
  else
    echo "[fail] $case_name :: $stage (rc=$rc)" >&2
  fi
  return "$rc"
}

link_or_copy_static() {
  local src="$1"
  local dst="$2"
  if [ -e "$src" ]; then
    ln -s "$src" "$dst"
  fi
}

build_scene_dir() {
  local case_root="$1"
  local scene_date="$2"
  local base_dir="$BASE_ROOT/$PROJECT/SLC/$scene_date"
  local case_dir="$case_root/$PROJECT/SLC/$scene_date"

  ensure_dir "$base_dir"
  mkdir -p "$case_dir"

  ensure_file "$base_dir/$scene_date.slc"
  ensure_file "$base_dir/$scene_date.slc.par"

  ln -s "$base_dir/$scene_date.slc" "$case_dir/$scene_date.slc"
  cp "$base_dir/$scene_date.slc.par" "$case_dir/$scene_date.slc.par"

  link_or_copy_static "$base_dir/${scene_date}_2rlks.amp" "$case_dir/${scene_date}_2rlks.amp"
  link_or_copy_static "$base_dir/${scene_date}_2rlks.amp.par" "$case_dir/${scene_date}_2rlks.amp.par"
  link_or_copy_static "$base_dir/${scene_date}_SLC_Tab" "$case_dir/${scene_date}_SLC_Tab"
  link_or_copy_static "$base_dir/down2slc.dat" "$case_dir/down2slc.dat"
  link_or_copy_static "$base_dir/t_${scene_date}" "$case_dir/t_${scene_date}"
}

build_case() {
  local case_name="$1"
  local case_root="$RUN_ROOT/$case_name"

  mkdir -p "$case_root/templates" "$case_root/logs" "$case_root/manifests"
  mkdir -p "$case_root/$PROJECT/SLC" "$case_root/$PROJECT/DEM" "$case_root/$PROJECT/RSLC" "$case_root/$PROJECT/ifgrams"
  mkdir -p "$case_root/dem_store"

  cp "$BASE_ROOT/templates/$PROJECT.template" "$case_root/templates/$PROJECT.template"
  cp -a "$BASE_ROOT/dem_store/$PROJECT" "$case_root/dem_store/"

  build_scene_dir "$case_root" "$MASTER_DATE"
  for slave_date in "${SLAVE_DATES[@]}"; do
    build_scene_dir "$case_root" "$slave_date"
  done

  printf '%s\n' "$case_root"
}

write_manifest() {
  local role="$1"
  local date_text="$2"
  local output_path="$3"
  local orbit_path="$ORBIT_DIR/${SATELLITE}_GpsData_GAS_C_${date_text}.txt"

  ensure_file "$orbit_path"
  cat >"$output_path" <<EOF
{
  "orbits": {
    "$role": {
      "satellite": "$SATELLITE",
      "date": "$date_text",
      "expected_name": "${SATELLITE}_GpsData_GAS_C_${date_text}.txt",
      "path": "$orbit_path"
    }
  }
}
EOF
}

apply_bridge_once() {
  local case_name="$1"
  local case_root="$2"
  local stage_name="$3"
  local date_text="$4"
  local role="$5"
  local validate_flag="$6"
  local strict_flag="$7"
  shift 7
  local manifest_path="$case_root/manifests/${role}_${date_text}.json"
  local summary_path="$case_root/logs/${stage_name}.summary.json"
  local -a cmd

  write_manifest "$role" "$date_text" "$manifest_path"

  cmd=(
    "$PYTHON_BIN" "$ORBIT_HELPER"
    "--date" "$date_text"
    "--role" "$role"
    "--manifest-json" "$manifest_path"
    "--summary-json" "$summary_path"
    "--backup"
  )

  if [ "$validate_flag" = "true" ]; then
    cmd+=("--validate-with-orb-filt")
  else
    cmd+=("--no-validate-with-orb-filt")
  fi

  if [ "$strict_flag" = "true" ]; then
    cmd+=("--strict")
  else
    cmd+=("--no-strict")
  fi

  while [ "$#" -gt 0 ]; do
    cmd+=("--slc-par" "$1")
    shift
  done

  run_stage "$case_name" "$stage_name" "${cmd[@]}"
  return $?
}

run_generate_and_coreg() {
  local case_name="$1"
  local case_root="$2"
  local rc=0

  export SCRATCHDIR="$case_root"
  export TEMPLATEDIR="$case_root/templates"
  export DEMDIR="$case_root/dem_store"

  run_stage "$case_name" "generate_rdc_dem" "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/generate_rdc_dem.py" "$PROJECT" || return $?

  for slave_date in "${SLAVE_DATES[@]}"; do
    run_stage "$case_name" "coreg_${slave_date}" "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/coreg_gamma.py" "$PROJECT" "$slave_date" || rc=1
  done

  return "$rc"
}

run_case_a() {
  local case_name='case_A_baseline'
  local case_root
  case_root="$(build_case "$case_name")"
  run_generate_and_coreg "$case_name" "$case_root" || true
}

run_case_c() {
  local case_name='case_C_precise_orbit_rewrite'
  local case_root
  case_root="$(build_case "$case_name")"

  apply_bridge_once \
    "$case_name" "$case_root" "orbit_bridge_master" "$MASTER_DATE" "master" "false" "true" \
    "$case_root/$PROJECT/SLC/$MASTER_DATE/$MASTER_DATE.slc.par" || return 1

  for slave_date in "${SLAVE_DATES[@]}"; do
    apply_bridge_once \
      "$case_name" "$case_root" "orbit_bridge_${slave_date}" "$slave_date" "slave" "false" "true" \
      "$case_root/$PROJECT/SLC/$slave_date/$slave_date.slc.par" || return 1
  done

  run_generate_and_coreg "$case_name" "$case_root" || true
}

run_case_d() {
  local case_name='case_D_precise_orbit_validate'
  local case_root
  case_root="$(build_case "$case_name")"

  apply_bridge_once \
    "$case_name" "$case_root" "orbit_bridge_master" "$MASTER_DATE" "master" "false" "true" \
    "$case_root/$PROJECT/SLC/$MASTER_DATE/$MASTER_DATE.slc.par" || return 1

  for slave_date in "${SLAVE_DATES[@]}"; do
    apply_bridge_once \
      "$case_name" "$case_root" "orbit_bridge_${slave_date}" "$slave_date" "slave" "false" "true" \
      "$case_root/$PROJECT/SLC/$slave_date/$slave_date.slc.par" || return 1
  done

  apply_bridge_once \
    "$case_name" "$case_root" "orbit_validate_master" "$MASTER_DATE" "master" "true" "false" \
    "$case_root/$PROJECT/SLC/$MASTER_DATE/$MASTER_DATE.slc.par" || true

  for slave_date in "${SLAVE_DATES[@]}"; do
    apply_bridge_once \
      "$case_name" "$case_root" "orbit_validate_${slave_date}" "$slave_date" "slave" "true" "false" \
      "$case_root/$PROJECT/SLC/$slave_date/$slave_date.slc.par" || true
  done

  run_generate_and_coreg "$case_name" "$case_root" || true
}

main() {
  ensure_dir "$BASE_ROOT"
  ensure_dir "$BASE_ROOT/$PROJECT/SLC"
  ensure_dir "$BASE_ROOT/dem_store/$PROJECT"
  ensure_file "$BASE_ROOT/templates/$PROJECT.template"
  ensure_file "$GAMMA_ENV"
  ensure_file "$ORBIT_HELPER"
  ensure_file "$PYTHON_BIN"

  mkdir -p "$RUN_ROOT"
  printf 'case\tstage\trc\tstdout_log\tstderr_log\n' >"$STATUS_FILE"

  . "$GAMMA_ENV" >/dev/null 2>&1
  export PATH="/home/administrator/miniconda3/envs/isce2/bin:$GAMMA_SCRIPT_DIR:$PYINT_SCRIPT_DIR:$PATH"
  export PYTHONPATH="$PYINT_HOME${PYTHONPATH:+:$PYTHONPATH}"

  cat >"$RUN_ROOT/run_info.txt" <<EOF
run_root=$RUN_ROOT
base_root=$BASE_ROOT
project=$PROJECT
master=$MASTER_DATE
slaves=${SLAVE_DATES[*]}
satellite=$SATELLITE
orbit_dir=$ORBIT_DIR
python=$PYTHON_BIN
pyint_home=$PYINT_HOME
EOF

  run_case_a
  run_case_c
  run_case_d

  echo "[done] run_root=$RUN_ROOT"
  echo "[done] status_file=$STATUS_FILE"
}

main "$@"
