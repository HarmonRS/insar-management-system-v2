#!/usr/bin/env bash
set -u
set -o pipefail

BASE_ROOT='/mnt/d/PyINT_POOL_TEST/LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene'
CASES_ROOT='/mnt/d/PyINT_POOL_TEST/LT1A_MONO_SYC_STRIP1_E129.6_N45.0_3scene_dem_cases'
PROJECT='pyint_stage'
MASTER_DATE='20230726'
SLAVE_DATES=(20230624 20230920)

REPO='/mnt/d/Code/Insar_management_system_v2'
PYINT_HOME="$REPO/third_party/PyINT"
PYINT_SCRIPT_DIR="$PYINT_HOME/pyint"
PYTHON_BIN='/home/administrator/miniconda3/envs/isce2/bin/python'
GAMMA_ENV="$REPO/backend/app/pyint_pipeline/pyint_gamma_env.sh"
GAMMA_SCRIPT_DIR='/usr/local/GAMMA_SOFTWARE-20240627/ISP/scripts'

RUN_STAMP="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
RUN_ROOT="$CASES_ROOT/run_$RUN_STAMP"
STATUS_FILE="$RUN_ROOT/stage_status.tsv"
CASE_FILTER="${CASE_FILTER:-}"

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

set_template_key() {
  local template_path="$1"
  local key="$2"
  local value="$3"

  "$PYTHON_BIN" -c '
from pathlib import Path
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text(encoding="utf-8").splitlines()
prefix = key + "="
updated = False
for idx, line in enumerate(lines):
    if line.startswith(prefix):
        lines[idx] = prefix + value
        updated = True
        break
if not updated:
    lines.append(prefix + value)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
' "$template_path" "$key" "$value"
}

build_case() {
  local case_name="$1"
  local dem_source="$2"
  local opentopo_dem_type="$3"
  local case_root="$RUN_ROOT/$case_name"
  local template_path

  mkdir -p "$case_root/templates" "$case_root/logs"
  mkdir -p "$case_root/$PROJECT/SLC" "$case_root/$PROJECT/DEM" "$case_root/$PROJECT/RSLC" "$case_root/$PROJECT/ifgrams"
  mkdir -p "$case_root/dem_store/$PROJECT"

  cp "$BASE_ROOT/templates/$PROJECT.template" "$case_root/templates/$PROJECT.template"
  template_path="$case_root/templates/$PROJECT.template"
  set_template_key "$template_path" "prepared_dem_source" "$dem_source"
  set_template_key "$template_path" "fabdem_dir" "-"
  set_template_key "$template_path" "opentopo_dem_type" "$opentopo_dem_type"
  set_template_key "$template_path" "opentopo_api_key" "-"

  build_scene_dir "$case_root" "$MASTER_DATE"
  for slave_date in "${SLAVE_DATES[@]}"; do
    build_scene_dir "$case_root" "$slave_date"
  done

  printf '%s\n' "$case_root"
}

run_case() {
  local case_name="$1"
  local dem_source="$2"
  local opentopo_dem_type="$3"
  local case_root
  local rc=0

  case_root="$(build_case "$case_name" "$dem_source" "$opentopo_dem_type")"

  export SCRATCHDIR="$case_root"
  export TEMPLATEDIR="$case_root/templates"
  export DEMDIR="$case_root/dem_store"

  run_stage "$case_name" "makedem_pyint" "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/makedem_pyint.py" "$PROJECT" || return $?
  run_stage "$case_name" "generate_rdc_dem" "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/generate_rdc_dem.py" "$PROJECT" || return $?

  for slave_date in "${SLAVE_DATES[@]}"; do
    run_stage "$case_name" "coreg_${slave_date}" "$PYTHON_BIN" "$PYINT_SCRIPT_DIR/coreg_gamma.py" "$PROJECT" "$slave_date" || rc=1
  done

  return "$rc"
}

should_run_case() {
  local case_name="$1"
  if [ -z "$CASE_FILTER" ]; then
    return 0
  fi
  case ",$CASE_FILTER," in
    *",$case_name,"*) return 0 ;;
    *) return 1 ;;
  esac
}

main() {
  ensure_dir "$BASE_ROOT"
  ensure_dir "$BASE_ROOT/$PROJECT/SLC"
  ensure_file "$BASE_ROOT/templates/$PROJECT.template"
  ensure_file "$GAMMA_ENV"
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
python=$PYTHON_BIN
pyint_home=$PYINT_HOME
EOF

  if should_run_case 'case_A_copdem_baseline'; then
    run_case 'case_A_copdem_baseline' '/mnt/d/DEM/COPDEM_GLO30_China_4326_DEM' '-'
  fi
  if should_run_case 'case_B_gmted2010_jp2'; then
    run_case 'case_B_gmted2010_jp2' '/mnt/d/DEM/GMTED2010.jp2' '-'
  fi
  if should_run_case 'case_C_opentopo_srtmgl1'; then
    run_case 'case_C_opentopo_srtmgl1' '-' 'SRTMGL1'
  fi

  echo "[done] run_root=$RUN_ROOT"
  echo "[done] status_file=$STATUS_FILE"
}

main "$@"
