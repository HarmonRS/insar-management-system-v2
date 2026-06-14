"""Inspect and optionally clean generated runtime data.

The script is deliberately conservative:
- dry-run is the default;
- protected paths are collected from database product/artifact references;
- project source, local software, external data pools, and license files are never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import ensure_project_env_loaded, read_int_env, settings  # noqa: E402


ACTIVE_STATUSES = {"PENDING", "READY", "RUNNING", "PROCESSING", "IN_PROGRESS", "QUEUED", "RETRY"}


@dataclass(frozen=True)
class CleanupRoot:
    label: str
    path: Path
    default_retention_days: int
    mode: str = "children"
    protect_db_references: bool = False
    enabled_by_default: bool = True


@dataclass
class Candidate:
    label: str
    path: Path
    reason: str
    size_bytes: int
    files: int
    dirs: int
    last_write: datetime | None
    protected: bool = False
    deleted: bool = False
    error: str = ""


def _norm(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def _measure(path: Path) -> tuple[int, int, int, datetime | None]:
    if not path.exists():
        return 0, 0, 0, None
    if path.is_file():
        stat = path.stat()
        return stat.st_size, 1, 0, datetime.fromtimestamp(stat.st_mtime)

    size = 0
    files = 0
    dirs = 0
    last_write = datetime.fromtimestamp(path.stat().st_mtime)
    for entry in path.rglob("*"):
        try:
            stat = entry.stat()
        except OSError:
            continue
        if entry.is_dir():
            dirs += 1
        else:
            files += 1
            size += stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime)
        if mtime > last_write:
            last_write = mtime
    return size, files, dirs, last_write


def _format_gb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 ** 3):.3f} GB"


def _candidate_children(root: CleanupRoot, cutoff: datetime) -> list[Candidate]:
    path = _norm(root.path)
    if not path.exists() or not path.is_dir():
        return []
    candidates: list[Candidate] = []
    for child in path.iterdir():
        size, files, dirs, last_write = _measure(child)
        if last_write and last_write > cutoff:
            continue
        candidates.append(
            Candidate(
                label=root.label,
                path=_norm(child),
                reason=f"last_write <= {cutoff.isoformat(timespec='seconds')}",
                size_bytes=size,
                files=files,
                dirs=dirs,
                last_write=last_write,
            )
        )
    return candidates


def _candidate_root(root: CleanupRoot, cutoff: datetime) -> list[Candidate]:
    path = _norm(root.path)
    if not path.exists():
        return []
    size, files, dirs, last_write = _measure(path)
    if last_write and last_write > cutoff:
        return []
    return [
        Candidate(
            label=root.label,
            path=path,
            reason=f"root last_write <= {cutoff.isoformat(timespec='seconds')}",
            size_bytes=size,
            files=files,
            dirs=dirs,
            last_write=last_write,
        )
    ]


def _configured_roots() -> list[CleanupRoot]:
    runtime_dir = BACKEND_DIR / "runtime"
    return [
        CleanupRoot(
            "pyint_work",
            Path(settings.PYINT_WORK_ROOT or runtime_dir / "pyint_work"),
            read_int_env("RUNTIME_CLEANUP_PYINT_WORK_RETENTION_DAYS", 14),
        ),
        CleanupRoot(
            "pyint_dem",
            Path(settings.PYINT_DEM_ROOT or runtime_dir / "pyint_dem"),
            read_int_env("RUNTIME_CLEANUP_PYINT_DEM_RETENTION_DAYS", 60),
        ),
        CleanupRoot(
            "sbas_insar_production_runs",
            Path(settings.GAMMA_SBAS_WORK_ROOT or runtime_dir / "sbas_insar_production") / "runs",
            read_int_env("RUNTIME_CLEANUP_SBAS_RUN_RETENTION_DAYS", 30),
            protect_db_references=True,
        ),
        CleanupRoot(
            "gamma_ipta_trials",
            Path(settings.GAMMA_SBAS_TRIAL_ROOT or runtime_dir / "gamma_ipta_trials"),
            read_int_env("RUNTIME_CLEANUP_TRIAL_RETENTION_DAYS", 7),
        ),
        CleanupRoot(
            "idl_worker_logs",
            Path(settings.IDL_WORKER_RUNTIME_DIR or runtime_dir / "idl_worker"),
            read_int_env("RUNTIME_CLEANUP_IDL_LOG_RETENTION_DAYS", 30),
        ),
        CleanupRoot(
            "wsl_jobs",
            Path(settings.WSL_BROKER_JOB_ROOT or runtime_dir / "wsl_jobs"),
            read_int_env("RUNTIME_CLEANUP_WSL_JOB_RETENTION_DAYS", 14),
        ),
        CleanupRoot(
            "timeseries_work",
            Path(settings.TIMESERIES_WORK_ROOT or runtime_dir / "timeseries_work"),
            read_int_env("RUNTIME_CLEANUP_TIMESERIES_WORK_RETENTION_DAYS", 30),
        ),
        CleanupRoot(
            "image_cache",
            Path(settings.CACHE_DIR),
            read_int_env("RUNTIME_CLEANUP_IMAGE_CACHE_RETENTION_DAYS", 60),
        ),
        CleanupRoot(
            "frontend_dist",
            PROJECT_ROOT / "frontend" / "dist",
            read_int_env("RUNTIME_CLEANUP_FRONTEND_DIST_RETENTION_DAYS", 0),
            mode="root",
            enabled_by_default=False,
        ),
        CleanupRoot(
            "logs",
            PROJECT_ROOT / "logs",
            read_int_env("RUNTIME_CLEANUP_LOG_RETENTION_DAYS", 30),
        ),
        CleanupRoot(
            "nginx_temp",
            PROJECT_ROOT / "nginx" / "temp",
            0,
        ),
    ]


async def _db_rows(query: str) -> list[tuple]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    url = str(settings.DATABASE_URL or "").strip()
    if not url:
        return []
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(query))
            return list(result.all())
    finally:
        await engine.dispose()


async def _active_status_summary() -> dict[str, int]:
    queries = {
        "system_jobs": "select count(*) from system_jobs where upper(status) in ('PENDING','READY','RUNNING','PROCESSING','IN_PROGRESS','QUEUED','RETRY')",
        "system_tasks": "select count(*) from system_tasks where upper(status) in ('PENDING','RUNNING','PROCESSING','IN_PROGRESS','QUEUED')",
        "workflow_runs": "select count(*) from workflow_runs where upper(status) in ('PENDING','RUNNING','PROCESSING','IN_PROGRESS','QUEUED')",
    }
    summary: dict[str, int] = {}
    for key, query in queries.items():
        try:
            rows = await _db_rows(query)
            summary[key] = int(rows[0][0] or 0) if rows else 0
        except Exception:
            summary[key] = -1
    return summary


async def _protected_paths() -> set[Path]:
    protected: set[Path] = set()
    queries = [
        "select publish_dir, manifest_path, source_primary_path, native_output_dir, preview_path, primary_asset_path from result_products",
        "select absolute_path from result_assets",
        "select path from workflow_artifacts",
        "select storage_root from result_catalog_states",
    ]
    for query in queries:
        try:
            rows = await _db_rows(query)
        except Exception as exc:
            print(f"[WARN] Could not read protected paths: {exc}")
            continue
        for row in rows:
            for value in row:
                text = str(value or "").strip()
                if not text:
                    continue
                try:
                    protected.add(_norm(text))
                except OSError:
                    continue
    return protected


def _apply_protection(candidates: Iterable[Candidate], protected: set[Path]) -> list[Candidate]:
    output: list[Candidate] = []
    for candidate in candidates:
        path = candidate.path
        for protected_path in protected:
            if path == protected_path or _is_under(protected_path, path) or _is_under(path, protected_path):
                candidate.protected = True
                candidate.reason = f"database reference protects {protected_path}"
                break
        output.append(candidate)
    return output


def _delete(candidate: Candidate) -> None:
    try:
        if candidate.path.is_dir():
            shutil.rmtree(candidate.path)
        else:
            candidate.path.unlink()
        candidate.deleted = True
    except Exception as exc:
        candidate.error = f"{type(exc).__name__}: {exc}"


async def collect_candidates(include_optional: bool, include_external_runtime: bool) -> tuple[list[Candidate], dict[str, int]]:
    ensure_project_env_loaded()
    now = datetime.now()
    candidates: list[Candidate] = []
    for root in _configured_roots():
        if not root.enabled_by_default and not include_optional:
            continue
        root_path = _norm(root.path)
        if not _is_under(root_path, PROJECT_ROOT):
            if not include_external_runtime:
                print(f"[SKIP] {root.label}: outside project root: {root_path}")
                continue
        cutoff = now - timedelta(days=max(0, int(root.default_retention_days)))
        if root.mode == "root":
            items = _candidate_root(root, cutoff)
        else:
            items = _candidate_children(root, cutoff)
        candidates.extend(items)

    protected = await _protected_paths()
    candidates = _apply_protection(candidates, protected)
    active_summary = await _active_status_summary()
    if any(value > 0 for value in active_summary.values()):
        for candidate in candidates:
            if candidate.label in {"pyint_work", "sbas_insar_production_runs", "wsl_jobs", "timeseries_work"}:
                candidate.protected = True
                candidate.reason = f"active database state present: {active_summary}"
    return candidates, active_summary


def print_report(candidates: list[Candidate], active_summary: dict[str, int]) -> None:
    total = sum(item.size_bytes for item in candidates)
    deletable = sum(item.size_bytes for item in candidates if not item.protected)
    print(f"[INFO] Active DB state: {active_summary}")
    print(f"[INFO] Candidates: {len(candidates)}, total={_format_gb(total)}, deletable={_format_gb(deletable)}")
    for item in sorted(candidates, key=lambda x: x.size_bytes, reverse=True):
        status = "PROTECTED" if item.protected else ("DELETED" if item.deleted else "DELETE_CANDIDATE")
        last_write = item.last_write.isoformat(timespec="seconds") if item.last_write else "unknown"
        print(
            f"{status}\t{item.label}\t{_format_gb(item.size_bytes)}\t"
            f"files={item.files}\tdirs={item.dirs}\tlast={last_write}\t{item.path}\t{item.reason}"
        )
        if item.error:
            print(f"  ERROR: {item.error}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect and clean generated runtime data.")
    parser.add_argument("--delete", action="store_true", help="Actually delete unprotected candidates.")
    parser.add_argument("--include-optional", action="store_true", help="Include optional targets such as frontend/dist.")
    parser.add_argument("--include-external-runtime", action="store_true", help="Also inspect configured runtime roots outside the project.")
    parser.add_argument("--yes", action="store_true", help="Required together with --delete.")
    args = parser.parse_args()

    candidates, active_summary = await collect_candidates(args.include_optional, args.include_external_runtime)
    if args.delete and not args.yes:
        print("[ERROR] --delete requires --yes")
        print_report(candidates, active_summary)
        return 2

    if args.delete:
        for candidate in candidates:
            if candidate.protected:
                continue
            _delete(candidate)

    print_report(candidates, active_summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
