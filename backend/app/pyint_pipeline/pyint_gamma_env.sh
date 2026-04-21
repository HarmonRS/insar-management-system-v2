#!/usr/bin/env bash

_pyint_gamma_die() {
  echo "$1" >&2
  return 1 2>/dev/null || exit 1
}

_pyint_gamma_home=""
if [ -n "${PYINT_GAMMA_HOME:-}" ] && [ -d "${PYINT_GAMMA_HOME}" ]; then
  _pyint_gamma_home="${PYINT_GAMMA_HOME}"
elif [ -n "${GAMMA_HOME:-}" ] && [ -d "${GAMMA_HOME}" ]; then
  _pyint_gamma_home="${GAMMA_HOME}"
else
  for _candidate in \
    /usr/local/GAMMA_SOFTWARE-20240627 \
    /usr/local/GAMMA_SOFTWARE-* \
    /opt/GAMMA_SOFTWARE-*; do
    [ -d "${_candidate}" ] || continue
    _pyint_gamma_home="${_candidate}"
    break
  done
fi

[ -n "${_pyint_gamma_home}" ] || _pyint_gamma_die "Gamma home not found."

export GAMMA_HOME="${_pyint_gamma_home}"
export MSP_HOME="${GAMMA_HOME}/MSP"
export ISP_HOME="${GAMMA_HOME}/ISP"
export DIFF_HOME="${GAMMA_HOME}/DIFF"
export DISP_HOME="${GAMMA_HOME}/DISP"
export LAT_HOME="${GAMMA_HOME}/LAT"
export IPTA_HOME="${GAMMA_HOME}/IPTA"
export GEO_HOME="${GAMMA_HOME}/GEO"

_pyint_gamma_prepend_path() {
  local _dir="$1"
  [ -d "${_dir}" ] || return 0
  case ":${PATH}:" in
    *":${_dir}:"*) ;;
    *) PATH="${_dir}:${PATH}" ;;
  esac
}

for _gamma_dir in \
  "${MSP_HOME}/bin" \
  "${ISP_HOME}/bin" \
  "${DIFF_HOME}/bin" \
  "${DISP_HOME}/bin" \
  "${LAT_HOME}/bin" \
  "${IPTA_HOME}/bin" \
  "${GEO_HOME}/bin" \
  "${MSP_HOME}/scripts" \
  "${ISP_HOME}/scripts" \
  "${DIFF_HOME}/scripts" \
  "${DISP_HOME}/scripts" \
  "${LAT_HOME}/scripts" \
  "${IPTA_HOME}/scripts" \
  "${GEO_HOME}/scripts"; do
  _pyint_gamma_prepend_path "${_gamma_dir}"
done

export PATH
export OS="linux64"
export HDF5_DISABLE_VERSION_CHECK="1"
export GNUTERM="${GNUTERM:-qt}"
export GAMMA_RASTER="${GAMMA_RASTER:-BMP}"
export PYTHONPATH=".:${GAMMA_HOME}${PYTHONPATH:+:${PYTHONPATH}}"

_pyint_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
_pyint_script_dir="${_pyint_repo_root}/third_party/PyINT/pyint"
_pyint_gamma_prepend_path "${_pyint_script_dir}"

unset _pyint_gamma_home
unset _gamma_dir
unset _pyint_repo_root
unset _pyint_script_dir
unset -f _pyint_gamma_prepend_path
unset -f _pyint_gamma_die
