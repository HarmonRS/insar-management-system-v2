#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENERIC_EXPORTER="$SCRIPT_DIR/export_mintpy_publish_products_ubuntu2404.sh"

export MINTPY_RUNNER="${MINTPY_RUNNER:-$SCRIPT_DIR/run_mintpy_unified_env_ubuntu2404.sh}"

bash "$GENERIC_EXPORTER" "$@"
