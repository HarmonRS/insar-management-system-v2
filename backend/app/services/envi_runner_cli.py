"""CLI runner for ENVI workflows in a subprocess.

Usage (called by job_handlers.py):
    python -m backend.app.services.envi_runner_cli \
        --workflow import \
        --root-dir Z:/Test_data/Test_IDL_1 \
        [--num-to-process 0] \
        [--timeout-seconds 14400]

Outputs a single JSON line to stdout on success.
Errors go to stderr with non-zero exit code.
"""
from __future__ import annotations

import argparse
import json
import sys

from ..config import ensure_project_env_loaded


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ENVI/SARscape workflow in isolated process."
    )
    parser.add_argument(
        "--workflow",
        required=True,
        choices=["import", "dinsar", "dinsar_custom"],
    )
    parser.add_argument("--root-dir", required=True)
    parser.add_argument("--num-to-process", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=None)
    parser.add_argument("--job-id", type=str, default=None)
    return parser.parse_args()


def main() -> int:
    ensure_project_env_loaded()
    args = _parse_args()
    try:
        from .envi_service import run_workflow

        record = run_workflow(
            workflow=args.workflow,
            root_dir=args.root_dir,
            num_to_process=args.num_to_process,
            timeout=args.timeout_seconds or 14400,
            job_id=args.job_id,
        )
        print(json.dumps(record, ensure_ascii=False))
        return 0
    except Exception as exc:
        err = {
            "ok": False,
            "error": str(exc),
            "workflow": args.workflow,
        }
        print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
