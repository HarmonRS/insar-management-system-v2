"""Inspect likely SARscape SBAS/E-SBAS ENVI task names.

This script does not execute processing tasks. It only asks envipyengine to
instantiate task definitions and read their parameters.

Examples:
    python scripts/verify_sarscape_sbas_tasks.py
    python scripts/verify_sarscape_sbas_tasks.py --task SARsInSARStackSBASGenerateConnectionGraph --parameters
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect SARscape SBAS/E-SBAS ENVI task availability."
    )
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Task name to inspect. May be repeated. Defaults to built-in candidates.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON report.",
    )
    parser.add_argument(
        "--parameters",
        action="store_true",
        help="Also inspect task parameters. This can be slow for some SARscape tasks.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Timeout in seconds when --parameters is used.",
    )
    return parser.parse_args()


def main() -> int:
    repo_root = _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from backend.app.config import ensure_project_env_loaded
    from backend.app.services.envi_service import (
        inspect_sarscape_sbas_tasks,
        inspect_sarscape_sbas_tasks_subprocess,
    )

    ensure_project_env_loaded()
    args = _parse_args()
    if args.parameters:
        report = inspect_sarscape_sbas_tasks_subprocess(
            args.task or None,
            include_parameters=True,
            timeout_seconds=max(10, int(args.timeout or 120)),
        )
    else:
        report = inspect_sarscape_sbas_tasks(args.task or None)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("SARscape SBAS/E-SBAS task inspection")
        print(f"ok: {report.get('ok')}")
        print(f"candidate_source: {report.get('candidate_source')}")
        print(f"include_parameters: {report.get('include_parameters')}")
        print(f"available: {report.get('available_count')} / {report.get('task_count')}")
        error = str(report.get("error") or "").strip()
        if error:
            print(f"error: {error}")
        print()
        for item in report.get("tasks") or []:
            status = "OK" if item.get("available") else "MISS"
            print(f"[{status}] {item.get('name')}")
            if item.get("available"):
                required = item.get("required_input_names") or []
                outputs = item.get("output_names") or []
                print(f"  required inputs: {required}")
                print(f"  outputs: {outputs}")
            else:
                print(f"  error: {item.get('error')}")

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
