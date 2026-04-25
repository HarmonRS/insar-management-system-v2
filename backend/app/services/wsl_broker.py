from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .wsl_runtime_registry import WslRuntimeDefinition, get_wsl_runtime, wsl_runtime_registry
from .wsl_service import run_wsl_exec


_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _utcnow_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slugify(value: str, *, fallback: str = "job") -> str:
    text = _SLUG_RE.sub("_", str(value or "").strip()).strip("_")
    return text or fallback


@dataclass(frozen=True)
class WslManifestRef:
    runtime_id: str
    job_id: str
    operation: str
    manifest_path_windows: str
    manifest_path_wsl: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "job_id": self.job_id,
            "operation": self.operation,
            "manifest_path_windows": self.manifest_path_windows,
            "manifest_path_wsl": self.manifest_path_wsl,
        }


@dataclass(frozen=True)
class WslBrokerResult:
    runtime_id: str
    distro: str
    returncode: int
    argv: Sequence[str]
    manifest: WslManifestRef
    stdout: str
    stderr: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "distro": self.distro,
            "returncode": self.returncode,
            "argv": list(self.argv),
            "manifest": self.manifest.to_dict(),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class WslBroker:
    def __init__(self, *, job_root_windows: Optional[str] = None) -> None:
        self._job_root_windows = str(
            job_root_windows or wsl_runtime_registry.broker_job_root_windows
        ).strip()

    @property
    def job_root_windows(self) -> str:
        return self._job_root_windows

    def stage_manifest(
        self,
        *,
        runtime_id: str,
        operation: str,
        payload: Mapping[str, Any],
        job_id: Optional[str] = None,
    ) -> WslManifestRef:
        runtime = get_wsl_runtime(runtime_id)
        safe_operation = _slugify(operation, fallback="operation")
        safe_job_id = _slugify(job_id or f"{safe_operation}_{_utcnow_text()}")
        manifest_dir = Path(self.job_root_windows, runtime.runtime_id, safe_operation)
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{safe_job_id}.json"
        document = {
            "job_id": safe_job_id,
            "runtime_id": runtime.runtime_id,
            "engine_code": runtime.engine_code,
            "operation": operation,
            "created_at": _utcnow_text(),
            "payload": dict(payload or {}),
        }
        manifest_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return WslManifestRef(
            runtime_id=runtime.runtime_id,
            job_id=safe_job_id,
            operation=operation,
            manifest_path_windows=str(manifest_path),
            manifest_path_wsl=self._manifest_to_wsl(str(manifest_path)),
        )

    def _manifest_to_wsl(self, manifest_path_windows: str) -> str:
        drive = Path(manifest_path_windows).drive.rstrip(":").lower()
        tail = Path(manifest_path_windows).as_posix().split(":", 1)[-1]
        if not drive:
            return Path(manifest_path_windows).as_posix()
        return f"/mnt/{drive}{tail}"

    def build_runner_argv(
        self,
        *,
        runtime: WslRuntimeDefinition,
        manifest: WslManifestRef,
        extra_args: Optional[Sequence[str]] = None,
    ) -> list[str]:
        argv = list(runtime.entrypoint_argv())
        argv.extend(["--manifest", manifest.manifest_path_wsl])
        argv.extend(str(item) for item in (extra_args or []) if str(item))
        return argv

    def run_manifest(
        self,
        *,
        runtime_id: str,
        operation: str,
        payload: Mapping[str, Any],
        job_id: Optional[str] = None,
        extra_args: Optional[Sequence[str]] = None,
        timeout_seconds: int = 30,
        env: Optional[Dict[str, str]] = None,
    ) -> WslBrokerResult:
        runtime = get_wsl_runtime(runtime_id)
        manifest = self.stage_manifest(
            runtime_id=runtime_id,
            operation=operation,
            payload=payload,
            job_id=job_id,
        )
        argv = self.build_runner_argv(runtime=runtime, manifest=manifest, extra_args=extra_args)
        rc, stdout, stderr = run_wsl_exec(
            argv,
            distro=runtime.distro,
            timeout=max(30, int(timeout_seconds or 30)),
            env=env,
        )
        return WslBrokerResult(
            runtime_id=runtime.runtime_id,
            distro=runtime.distro,
            returncode=rc,
            argv=argv,
            manifest=manifest,
            stdout=stdout,
            stderr=stderr,
        )


wsl_broker = WslBroker()
