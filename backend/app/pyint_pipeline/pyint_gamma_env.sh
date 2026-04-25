#!/usr/bin/env bash
set -euo pipefail

_pyint_legacy_profile="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/deploy/wsl/profiles/gamma_env.sh"
if [ ! -f "${_pyint_legacy_profile}" ]; then
  echo "Gamma profile not found: ${_pyint_legacy_profile}" >&2
  return 1 2>/dev/null || exit 1
fi

. "${_pyint_legacy_profile}"
unset _pyint_legacy_profile

return 0 2>/dev/null || exit 0
