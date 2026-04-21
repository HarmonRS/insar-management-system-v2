#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: run_pyint_ab_case.sh <case_name> <pyint_home> <orbit_enabled:true|false> [unwrap:true|false] [geocode:true|false]" >&2
  exit 2
fi

case_name=$1
pyint_home=$2
orbit_enabled=$3
unwrap_enabled=${4:-false}
geocode_enabled=${5:-false}

repo=/mnt/d/Code/Insar_management_system_v2
task_name=Task_20230602_20230720
pair_key=lt1_20230602_20230720_ef1cd36538
task_dir=/mnt/d/Task_Pool/DInSAR/Task_260416_Gamma_PyINT/Task_20230602_20230720
manifest=/mnt/d/Code/Insar_management_system_v2/backend/runtime/pyint_input_assets/$pair_key/run_20260419T065041Z_pyint_lt1_gamma_dinsar/task_manifest.json
input_assets_dir=$(dirname "$manifest")
python_bin=/home/administrator/miniconda3/envs/isce2/bin/python
script=$repo/backend/app/pyint_pipeline/run_lt1_pyint_pipeline.py
pyint_app=$pyint_home/pyint/pyintApp.py
gamma_env=$repo/backend/app/pyint_pipeline/pyint_gamma_env.sh
prepared_dem=/mnt/d/DEM/COPDEM_GLO30_China_4326_DEM

case_root=/mnt/d/PyINT_AB/$case_name
run_root=$case_root/work
project_name=${pair_key}_${case_name}
project_dir=$run_root/$project_name
template_root=$run_root/templates
output_dir=$case_root/output
dem_root=$case_root/dem_cache

rm -rf "$case_root"
mkdir -p "$case_root"

cmd=(
  "$python_bin" "$script" "$task_dir"
  --project-dir "$project_dir"
  --template-root "$template_root"
  --output-dir "$output_dir"
  --pyint-home "$pyint_home"
  --pyint-app-script "$pyint_app"
  --python "$python_bin"
  --dem-root "$dem_root"
  --dem-mode prepared_file
  --prepared-dem-path "$prepared_dem"
  --project-name "$project_name"
  --gamma-env-script "$gamma_env"
  --pair-key "$pair_key"
  --task-alias "$task_name"
  --orbit-policy require_txt
  --input-assets-dir "$input_assets_dir"
  --input-assets-json "$manifest"
  --master-date 20230602
  --slave-date 20230720
  --time-baseline-days 48
  --range-looks 2
  --azimuth-looks 2
  --parallel-workers 1
  --lt1-precise-orbit-enabled "$orbit_enabled"
)

if [[ "$unwrap_enabled" == "true" ]]; then
  cmd+=(--unwrap)
else
  cmd+=(--no-unwrap)
fi

if [[ "$geocode_enabled" == "true" ]]; then
  cmd+=(--geocode)
else
  cmd+=(--no-geocode)
fi

printf 'CASE=%s\nPYINT_HOME=%s\nORBIT=%s\nUNWRAP=%s\nGEOCODE=%s\nCASE_ROOT=%s\n' \
  "$case_name" "$pyint_home" "$orbit_enabled" "$unwrap_enabled" "$geocode_enabled" "$case_root"

"${cmd[@]}"
