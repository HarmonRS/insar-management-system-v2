from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "SKIPPED"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tail(text: str, limit: int = 4000) -> str:
    if not text:
        return ""
    return text[-limit:]


def _normalize_step_ids(value: str | None) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    return {item.strip() for item in text.replace(";", ",").split(",") if item.strip()}


def _load_workflow_manifest(broker_manifest: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    payload = broker_manifest.get("payload") or {}
    workflow_manifest_path = payload.get("workflow_manifest_wsl") or payload.get("workflow_manifest")
    if not workflow_manifest_path:
        raise ValueError("payload.workflow_manifest_wsl is required")
    manifest_path = Path(str(workflow_manifest_path))
    return _load_json(manifest_path), manifest_path


def _state_path(workflow_manifest: dict[str, Any]) -> Path:
    state = workflow_manifest.get("state") or {}
    explicit = state.get("step_status_path")
    if explicit:
        return Path(str(explicit))
    run_root = Path(str(workflow_manifest.get("run_root_wsl") or workflow_manifest.get("run_root") or "."))
    return run_root / "state" / "step_status.json"


def _script_env(workflow_manifest: dict[str, Any], step: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    run_root = str(workflow_manifest.get("run_root_wsl") or workflow_manifest.get("run_root") or "")
    params = workflow_manifest.get("params") or {}
    env.update(
        {
            "GAMMA_SBAS_RUN_ROOT": run_root,
            "GAMMA_SBAS_MANIFEST": str(workflow_manifest.get("manifest_path_wsl") or ""),
            "GAMMA_SBAS_STEP_ID": str(step.get("id") or ""),
            "GAMMA_SBAS_STEP_NAME": str(step.get("name") or step.get("id") or ""),
            "GAMMA_SBAS_RLKS": str(params.get("rlks") or ""),
            "GAMMA_SBAS_AZLKS": str(params.get("azlks") or ""),
            "GAMMA_SBAS_MB_MODE": str(params.get("mb_mode") or ""),
            "GAMMA_SBAS_REFERENCE_WINDOW": str(params.get("reference_window") or ""),
        }
    )
    for key, value in (step.get("env") or {}).items():
        env[str(key)] = str(value)
    return env


def _selected_steps(steps: list[dict[str, Any]], only_steps: set[str], from_step: str | None, to_step: str | None) -> list[dict[str, Any]]:
    if only_steps:
        return [step for step in steps if str(step.get("id") or "") in only_steps]
    if not from_step and not to_step:
        return steps

    selected: list[dict[str, Any]] = []
    active = from_step is None
    for step in steps:
        step_id = str(step.get("id") or "")
        if step_id == from_step:
            active = True
        if active:
            selected.append(step)
        if step_id == to_step:
            break
    return selected


def _run_step(
    workflow_manifest: dict[str, Any],
    step: dict[str, Any],
    *,
    state: dict[str, Any],
    force: bool,
    dry_run: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    step_id = str(step.get("id") or "").strip()
    if not step_id:
        raise ValueError("workflow step id must not be empty")

    if step.get("enabled") is False:
        return {
            "id": step_id,
            "name": step.get("name") or step_id,
            "status": "SKIPPED",
            "skipped_reason": "step disabled in workflow manifest",
            "started_at": _utcnow(),
            "ended_at": _utcnow(),
        }

    previous = (state.get("steps") or {}).get(step_id) or {}
    if previous.get("status") == "COMPLETED" and not force:
        return {**previous, "status": "SKIPPED", "skipped_reason": "already completed"}

    script = Path(str(step.get("script_wsl") or step.get("script") or ""))
    if not script.is_file():
        raise FileNotFoundError(f"step script not found: {script}")

    log_path = Path(str(step.get("log_wsl") or step.get("log") or ""))
    if not log_path:
        run_root = Path(str(workflow_manifest.get("run_root_wsl") or "."))
        log_path = run_root / "logs" / f"{step_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = _utcnow()
    if dry_run:
        return {
            "id": step_id,
            "name": step.get("name") or step_id,
            "status": "DRY_RUN",
            "script": str(script),
            "log": str(log_path),
            "started_at": started_at,
            "ended_at": _utcnow(),
            "returncode": None,
        }

    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(script.parent),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
        env=_script_env(workflow_manifest, step),
    )
    log_path.write_text(
        "\n".join(
            [
                f"# step={step_id}",
                f"# started_at={started_at}",
                f"# ended_at={_utcnow()}",
                f"# returncode={proc.returncode}",
                "",
                "## stdout",
                proc.stdout or "",
                "",
                "## stderr",
                proc.stderr or "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "id": step_id,
        "name": step.get("name") or step_id,
        "status": "COMPLETED" if proc.returncode == 0 else "FAILED",
        "script": str(script),
        "log": str(log_path),
        "started_at": started_at,
        "ended_at": _utcnow(),
        "returncode": proc.returncode,
        "stdout_tail": _tail(proc.stdout),
        "stderr_tail": _tail(proc.stderr),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gamma SBAS manifest runner.")
    parser.add_argument("--manifest", required=True, help="WSL path to broker manifest.")
    parser.add_argument("--from-step", default="", help="First workflow step id to execute.")
    parser.add_argument("--to-step", default="", help="Last workflow step id to execute.")
    parser.add_argument("--only-steps", default="", help="Comma-separated step ids to execute.")
    parser.add_argument("--force", action="store_true", help="Run completed steps again.")
    parser.add_argument("--dry-run", action="store_true", help="Validate manifest and write dry-run state.")
    parser.add_argument("--timeout-seconds", type=int, default=43200)
    args = parser.parse_args()

    broker_manifest = _load_json(args.manifest)
    workflow_manifest, workflow_manifest_path = _load_workflow_manifest(broker_manifest)
    workflow_manifest["manifest_path_wsl"] = str(workflow_manifest_path)

    state_file = _state_path(workflow_manifest)
    state = _load_json(state_file) if state_file.is_file() else {
        "schema": "insar.gamma-sbas-step-status/v1",
        "run_id": workflow_manifest.get("run_id"),
        "steps": {},
    }
    state.setdefault("steps", {})
    state["updated_at"] = _utcnow()
    state["runner_manifest"] = args.manifest

    all_steps = list(workflow_manifest.get("steps") or [])
    selected = _selected_steps(
        all_steps,
        _normalize_step_ids(args.only_steps),
        str(args.from_step or "").strip() or None,
        str(args.to_step or "").strip() or None,
    )
    if not selected:
        raise ValueError("no workflow steps selected")

    overall_rc = 0
    executed: list[str] = []
    for step in selected:
        step_id = str(step.get("id") or "")
        result = _run_step(
            workflow_manifest,
            step,
            state=state,
            force=args.force,
            dry_run=args.dry_run,
            timeout_seconds=max(60, int(args.timeout_seconds or 43200)),
        )
        state["steps"][step_id] = result
        state["updated_at"] = _utcnow()
        _write_json(state_file, state)
        executed.append(step_id)
        if result.get("status") == "FAILED":
            overall_rc = int(result.get("returncode") or 1)
            break

    summary = {
        "runner": "gamma_sbas_runtime_v1",
        "operation": broker_manifest.get("operation"),
        "workflow_manifest": str(workflow_manifest_path),
        "state_path": str(state_file),
        "executed_steps": executed,
        "returncode": overall_rc,
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
