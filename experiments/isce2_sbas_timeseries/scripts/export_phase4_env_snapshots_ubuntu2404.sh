#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <output-dir-wsl>" >&2
  exit 1
fi

OUTPUT_DIR="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPORTER="$SCRIPT_DIR/export_conda_env_snapshot_ubuntu2404.sh"

bash "$EXPORTER" isce2 "$OUTPUT_DIR"
bash "$EXPORTER" isce2_mintpy_v1 "$OUTPUT_DIR"
