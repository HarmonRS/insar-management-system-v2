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
        required=False,
        choices=["import", "dinsar", "dinsar_custom"],
    )
    parser.add_argument("--inspect-sarscape-sbas", action="store_true")
    parser.add_argument("--include-parameters", action="store_true")
    parser.add_argument("--task-name", action="append", default=[])
    parser.add_argument("--root-dir", required=False)
    parser.add_argument("--task-dir", required=False)
    parser.add_argument("--output-dir", required=False)
    parser.add_argument("--source-root", required=False)
    parser.add_argument("--num-to-process", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=None)
    parser.add_argument("--job-id", type=str, default=None)
    parser.add_argument("--run-key", type=str, default=None)
    parser.add_argument("--profile-code", type=str, default=None)
    return parser.parse_args()


def main() -> int:
    ensure_project_env_loaded()
    args = _parse_args()
    try:
        from .envi_service import (
            inspect_sarscape_sbas_tasks,
            run_single_task_workflow,
            run_workflow,
        )

        if args.inspect_sarscape_sbas:
            record = inspect_sarscape_sbas_tasks(
                args.task_name or None,
                include_parameters=bool(args.include_parameters),
            )
            print(json.dumps(record, ensure_ascii=False))
            return 0 if record.get("ok") else 2

        if not args.workflow:
            raise ValueError("--workflow is required unless --inspect-sarscape-sbas is used.")

        if args.task_dir:
            if not args.output_dir:
                raise ValueError("--output-dir is required when --task-dir is used.")
            record = run_single_task_workflow(
                workflow=args.workflow,
                task_dir=args.task_dir,
                output_dir=args.output_dir,
                source_root=args.source_root,
                timeout=args.timeout_seconds or 14400,
                job_id=args.job_id,
                run_key=args.run_key,
                profile_code=args.profile_code,
            )
        else:
            if not args.root_dir:
                raise ValueError("--root-dir is required when --task-dir is not used.")
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
