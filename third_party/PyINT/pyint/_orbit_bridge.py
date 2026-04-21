import os
import subprocess
import sys


TRUE_VALUES = {"1", "true", "yes", "on"}


def _read_bool_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in TRUE_VALUES


def apply_precise_orbit(date_text, slc_par_paths, work_dir=None, operation_tag="raw2slc"):
    helper_path = str(os.getenv("PYINT_LT1_PRECISE_ORBIT_HELPER") or "").strip()
    manifest_json = str(os.getenv("PYINT_LT1_PRECISE_ORBIT_MANIFEST") or "").strip()
    enabled = _read_bool_env("PYINT_LT1_PRECISE_ORBIT_ENABLED", False)
    strict = _read_bool_env("PYINT_LT1_PRECISE_ORBIT_STRICT", True)

    result = {
        "enabled": enabled,
        "helper_path": helper_path,
        "manifest_json": manifest_json,
        "summary_json": "",
        "returncode": 0,
        "stdout": "",
        "stderr": "",
        "command": [],
        "applied_files": [],
        "status": "disabled",
    }
    if not enabled:
        return result
    if not helper_path or not os.path.isfile(helper_path):
        message = "PYINT_LT1_PRECISE_ORBIT_HELPER is missing or does not exist."
        result.update({"status": "missing_helper", "stderr": message})
        if strict:
            raise RuntimeError(message)
        return result
    if not manifest_json:
        message = "PYINT_LT1_PRECISE_ORBIT_MANIFEST is empty."
        result.update({"status": "missing_manifest", "stderr": message})
        if strict:
            raise RuntimeError(message)
        return result

    files = []
    for path in slc_par_paths or []:
        text = str(path or "").strip()
        if text and os.path.isfile(text):
            files.append(text)
    result["applied_files"] = files
    if not files:
        result["status"] = "skipped"
        return result

    summary_json = os.path.join(str(work_dir or os.getcwd()), "orbit_bridge_summary.json")
    command = [
        sys.executable,
        helper_path,
        "--date",
        str(date_text or "").strip(),
        "--manifest-json",
        manifest_json,
        "--summary-json",
        summary_json,
        "--operation-tag",
        str(operation_tag or "raw2slc"),
    ]
    for path in files:
        command.extend(["--slc-par", path])
    result["summary_json"] = summary_json
    result["command"] = command

    proc = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    result.update(
        {
            "returncode": int(proc.returncode),
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "status": "applied" if proc.returncode == 0 else "failed",
        }
    )
    if proc.returncode != 0 and strict:
        raise RuntimeError(result["stderr"] or result["stdout"] or "Precise orbit bridge failed.")
    return result
