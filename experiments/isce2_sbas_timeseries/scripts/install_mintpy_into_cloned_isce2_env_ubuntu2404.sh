#!/usr/bin/env bash
set -euo pipefail

CONDA_BIN="${CONDA_BIN:-/home/administrator/miniconda3/bin/conda}"
SOURCE_ENV="${SOURCE_ENV:-isce2}"
TARGET_ENV="${TARGET_ENV:-isce2_mintpy}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
BOOTSTRAP_MODE="${BOOTSTRAP_MODE:-clone}"
USE_TUNA_MIRROR="${USE_TUNA_MIRROR:-1}"
CHANNEL_CONDA_FORGE="${CHANNEL_CONDA_FORGE:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge}"
CHANNEL_MAIN="${CHANNEL_MAIN:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main}"
CHANNEL_R="${CHANNEL_R:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r}"
MINTPY_SPEC="${MINTPY_SPEC:-mintpy}"
CLONE_OFFLINE="${CLONE_OFFLINE:-1}"

if [[ ! -x "$CONDA_BIN" ]]; then
  echo "Missing conda binary: $CONDA_BIN" >&2
  exit 1
fi

channel_args=()
if [[ "$USE_TUNA_MIRROR" == "1" ]]; then
  channel_args=(
    --override-channels
    -c "$CHANNEL_CONDA_FORGE"
    -c "$CHANNEL_MAIN"
    -c "$CHANNEL_R"
  )
fi

env_exists() {
  "$CONDA_BIN" env list | awk '{print $1}' | grep -Fxq "$1"
}

echo "Unified ISCE2 + MintPy runtime bootstrap"
echo "Conda:        $CONDA_BIN"
echo "Source env:   $SOURCE_ENV"
echo "Target env:   $TARGET_ENV"
echo "Python:       $PYTHON_VERSION"
echo "Mode:         $BOOTSTRAP_MODE"
echo "MintPy spec:  $MINTPY_SPEC"
echo "Use mirror:   $USE_TUNA_MIRROR"
echo "Clone offline:$CLONE_OFFLINE"

if ! env_exists "$SOURCE_ENV"; then
  echo "Missing source environment: $SOURCE_ENV" >&2
  exit 1
fi

if [[ "$BOOTSTRAP_MODE" != "clone" && "$BOOTSTRAP_MODE" != "recreate" ]]; then
  echo "Unsupported BOOTSTRAP_MODE: $BOOTSTRAP_MODE" >&2
  exit 1
fi

if env_exists "$TARGET_ENV"; then
  echo "Environment $TARGET_ENV already exists. Reusing it."
else
  if [[ "$BOOTSTRAP_MODE" == "clone" ]]; then
    echo "Cloning $SOURCE_ENV into $TARGET_ENV"
    clone_args=("${channel_args[@]}" -y -n "$TARGET_ENV" --clone "$SOURCE_ENV")
    if [[ "$CLONE_OFFLINE" == "1" ]]; then
      clone_args+=(--offline)
    fi
    "$CONDA_BIN" create "${clone_args[@]}"
  else
    tmp_export="$(mktemp)"
    tmp_conda_specs="$(mktemp)"
    tmp_pip_specs="$(mktemp)"
    trap 'rm -f "$tmp_export" "$tmp_conda_specs" "$tmp_pip_specs"' EXIT

    echo "Exporting $SOURCE_ENV into a recreate spec"
    "$CONDA_BIN" env export -n "$SOURCE_ENV" --no-builds > "$tmp_export"

    awk \
      '
      /^dependencies:/ {
        in_dependencies = 1
        next
      }
      /^prefix:/ {
        exit
      }
      in_dependencies == 1 && /^  - pip:$/ {
        exit
      }
      in_dependencies == 1 && /^  - / {
        print substr($0, 5)
      }
    ' "$tmp_export" > "$tmp_conda_specs"

    awk \
      '
      /^  - pip:$/ {
        in_pip = 1
        next
      }
      /^prefix:/ {
        exit
      }
      in_pip == 1 && /^    - / {
        print substr($0, 7)
      }
    ' "$tmp_export" > "$tmp_pip_specs"

    mapfile -t conda_specs < "$tmp_conda_specs"
    if [[ ${#conda_specs[@]} -eq 0 ]]; then
      echo "Failed to extract conda dependency specs from $SOURCE_ENV export" >&2
      exit 1
    fi

    echo "Recreating $TARGET_ENV from exported dependency list"
    "$CONDA_BIN" create -y -n "$TARGET_ENV" "${channel_args[@]}" "${conda_specs[@]}"

    if [[ -s "$tmp_pip_specs" ]]; then
      mapfile -t pip_specs < "$tmp_pip_specs"
      echo "Reinstalling exported pip packages into $TARGET_ENV"
      "$CONDA_BIN" run -n "$TARGET_ENV" python -m pip install "${pip_specs[@]}"
    fi
  fi
fi

echo "Installing MintPy into $TARGET_ENV"
"$CONDA_BIN" install -y -n "$TARGET_ENV" "${channel_args[@]}" "$MINTPY_SPEC"

echo "Verifying unified runtime imports"
"$CONDA_BIN" run -n "$TARGET_ENV" python -c "import sys; import isce; import mintpy; import h5py; print(sys.executable); print(isce.__file__); print(mintpy.__file__); print('h5py=' + h5py.__version__)"

echo "Unified runtime bootstrap complete"
