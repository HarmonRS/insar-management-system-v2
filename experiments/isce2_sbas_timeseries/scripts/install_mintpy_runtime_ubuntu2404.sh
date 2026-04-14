#!/usr/bin/env bash
set -euo pipefail

CONDA_BIN="${CONDA_BIN:-/home/administrator/miniconda3/bin/conda}"
TARGET_ENV="${TARGET_ENV:-mintpy}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
USE_TUNA_MIRROR="${USE_TUNA_MIRROR:-1}"
CHANNEL_CONDA_FORGE="${CHANNEL_CONDA_FORGE:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge}"
CHANNEL_MAIN="${CHANNEL_MAIN:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main}"
CHANNEL_R="${CHANNEL_R:-https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r}"

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
  "$CONDA_BIN" env list | awk '{print $1}' | grep -Fxq "$TARGET_ENV"
}

echo "MintPy runtime bootstrap"
echo "Conda:       $CONDA_BIN"
echo "Target env:  $TARGET_ENV"
echo "Python:      $PYTHON_VERSION"
echo "Use mirror:  $USE_TUNA_MIRROR"

if env_exists; then
  echo "Environment $TARGET_ENV already exists. Installing or updating MintPy."
  "$CONDA_BIN" install -y -n "$TARGET_ENV" "${channel_args[@]}" mintpy
else
  echo "Creating environment $TARGET_ENV with MintPy."
  "$CONDA_BIN" create -y -n "$TARGET_ENV" "${channel_args[@]}" "python=$PYTHON_VERSION" mintpy
fi

echo "Verifying MintPy import"
"$CONDA_BIN" run -n "$TARGET_ENV" python -c "import mintpy; print(mintpy.__file__)"
echo "MintPy runtime bootstrap complete"
