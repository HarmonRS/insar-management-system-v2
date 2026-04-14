#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <env-name> <output-dir-wsl>" >&2
  exit 1
fi

ENV_NAME="$1"
OUTPUT_DIR="$2"
CONDA_BIN="${CONDA_BIN:-/home/administrator/miniconda3/bin/conda}"

if [[ ! -x "$CONDA_BIN" ]]; then
  echo "Missing conda binary: $CONDA_BIN" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

SAFE_NAME="${ENV_NAME//[^A-Za-z0-9._-]/_}"
YAML_PATH="$OUTPUT_DIR/${SAFE_NAME}.no_builds.yml"
EXPLICIT_PATH="$OUTPUT_DIR/${SAFE_NAME}.explicit.txt"
LIST_PATH="$OUTPUT_DIR/${SAFE_NAME}.conda_list.txt"
RUNTIME_PATH="$OUTPUT_DIR/${SAFE_NAME}.runtime_versions.txt"

echo "Exporting conda environment snapshot"
echo "Env:        $ENV_NAME"
echo "Output dir: $OUTPUT_DIR"

"$CONDA_BIN" env export -n "$ENV_NAME" --no-builds > "$YAML_PATH"
"$CONDA_BIN" list -n "$ENV_NAME" --explicit > "$EXPLICIT_PATH"
"$CONDA_BIN" list -n "$ENV_NAME" > "$LIST_PATH"

"$CONDA_BIN" run -n "$ENV_NAME" python -c "
import importlib.util
import logging
import platform
import sys

logging.getLogger().setLevel(logging.WARNING)

def version_of(name):
    try:
        mod = __import__(name)
        return getattr(mod, '__version__', '<missing>')
    except Exception as exc:
        return f'<import failed: {exc}>'

for line in [
    f'python_executable={sys.executable}',
    f'python_version={platform.python_version()}',
    f'isce_present={importlib.util.find_spec(\"isce\") is not None}',
    f'mintpy_present={importlib.util.find_spec(\"mintpy\") is not None}',
    f'h5py_present={importlib.util.find_spec(\"h5py\") is not None}',
]:
    print(line)

if importlib.util.find_spec('isce') is not None:
    import isce
    print(f'isce_file={isce.__file__}')
    print(f'isce_version={getattr(isce, \"__version__\", \"<missing>\")}')

if importlib.util.find_spec('mintpy') is not None:
    import mintpy
    print(f'mintpy_file={mintpy.__file__}')
    print(f'mintpy_version={getattr(mintpy, \"__version__\", \"<missing>\")}')

if importlib.util.find_spec('h5py') is not None:
    import h5py
    print(f'h5py_version={h5py.__version__}')
" > "$RUNTIME_PATH"

echo "Wrote: $YAML_PATH"
echo "Wrote: $EXPLICIT_PATH"
echo "Wrote: $LIST_PATH"
echo "Wrote: $RUNTIME_PATH"
