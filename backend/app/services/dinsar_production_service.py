from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..models import (
    DinsarProductionExecutionORM,
    DinsarProductionRunItemORM,
    DinsarProductionRunORM,
)
from .envi_service import RUNTIME_DIR, _collect_task_folders, _resolve_dinsar_pair_identity, _to_local_path
from .task_service import task_service
from .workflow_service import workflow_service


TASK_TYPE_DINSAR_PRODUCTION = "IDL_RUN_DINSAR"
RUN_STATUS_PENDING = "PENDING"
RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_COMPLETED = "COMPLETED"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_CANCELLED = "CANCELLED"
RUN_ITEM_STATUS_PENDING = "PENDING"
RUN_ITEM_STATUS_RUNNING = "RUNNING"
RUN_ITEM_STATUS_COMPLETED = "COMPLETED"
RUN_ITEM_STATUS_FAILED = "FAILED"
RUN_ITEM_STATUS_SKIPPED = "SKIPPED"
RUN_ITEM_STATUS_CANCELLED = "CANCELLED"
EXECUTION_STATUS_PENDING = "PENDING"
EXECUTION_STATUS_RUNNING = "RUNNING"
EXECUTION_STATUS_COMPLETED = "COMPLETED"
EXECUTION_STATUS_FAILED = "FAILED"
EXECUTION_STATUS_CANCELLED = "CANCELLED"

CURRENT_POINTER_FILENAME = "current.json"
EXECUTION_MANIFEST_FILENAME = "execution_manifest.json"
RUNS_STEP_ID = "execute_items"
RUNS_STEP_NAME = "Execute ENVI D-InSAR items"
TERMINAL_RUN_STATUSES = {
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
}
TERMINAL_ITEM_STATUSES = {
    RUN_ITEM_STATUS_COMPLETED,
    RUN_ITEM_STATUS_FAILED,
    RUN_ITEM_STATUS_SKIPPED,
    RUN_ITEM_STATUS_CANCELLED,
}


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _utc_text(value: Optional[datetime] = None) -> str:
    stamp = value or _utcnow()
    return stamp.isoformat(timespec="seconds") + "Z"


def _normalize_dir(path: str, label: str) -> str:
    normalized = os.path.normpath(os.path.abspath(_to_local_path(path)))
    if not os.path.isdir(normalized):
        raise ValueError(f"{label} does not exist: {path}")
    return normalized


def _ensure_dir(path: str) -> str:
    normalized = os.path.normpath(os.path.abspath(path))
    os.makedirs(normalized, exist_ok=True)
    return normalized


def _write_json(path: str, payload: Dict[str, Any]) -> str:
    target = os.path.normpath(os.path.abspath(path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return target


def _looks_like_task_dir(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "master")) and os.path.isdir(os.path.join(path, "slave"))


def _discover_run_items(root_dir: str, num_to_process: int) -> List[Dict[str, Any]]:
    task_folders = [root_dir] if _looks_like_task_dir(root_dir) else _collect_task_folders(root_dir)
    if num_to_process > 0:
        task_folders = task_folders[:num_to_process]

    items: List[Dict[str, Any]] = []
    for order_index, folder in enumerate(task_folders, start=1):
        task_name = os.path.basename(folder)
        task_alias, pair_key, pair_meta = _resolve_dinsar_pair_identity(folder, task_name)
        items.append(
            {
                "order_index": order_index,
                "task_name": task_name,
                "task_alias": task_alias,
                "pair_key": pair_key,
                "pair_uid": pair_meta.get("pair_uid") or pair_meta.get("scene_pair_uid"),
                "network_run_id": pair_meta.get("network_run_id"),
                "network_edge_id": pair_meta.get("network_edge_id"),
                "policy_version": pair_meta.get("policy_version"),
                "selection_strategy": pair_meta.get("selection_strategy"),
                "source_task_dir": folder,
                "results_root_dir": os.path.join(folder, "dinsar_results"),
            }
        )
    return items


def _run_log_path(run_id: str) -> str:
    return os.path.join(RUNTIME_DIR, f"{run_id}.log")


def _append_run_log_sync(run_id: str, message: str) -> str:
    _ensure_dir(RUNTIME_DIR)
    log_path = _run_log_path(run_id)
    line = str(message or "").rstrip()
    if not line:
        return log_path
    with open(log_path, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    return log_path


def _kill_process_tree_sync(pid: int) -> None:
    try:
        import psutil

        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        psutil.wait_procs(children + [parent], timeout=10)
        return
    except ImportError:
        pass
    except Exception:
        pass

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception:
        pass


def _execution_dir(item: DinsarProductionRunItemORM, run_key: str) -> str:
    return os.path.join(item.results_root_dir, "runs", run_key)


def _current_pointer_path(item: DinsarProductionRunItemORM) -> str:
    return os.path.join(item.results_root_dir, CURRENT_POINTER_FILENAME)


def _execution_manifest_path(execution_dir: str) -> str:
    return os.path.join(execution_dir, EXECUTION_MANIFEST_FILENAME)


def _safe_epoch(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(value.timestamp())


def _public_run_status(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if normalized == RUN_STATUS_COMPLETED:
        return "success"
    if normalized == RUN_STATUS_FAILED:
        return "failed"
    if normalized == RUN_STATUS_CANCELLED:
        return "cancelled"
    if normalized == RUN_STATUS_RUNNING:
        return "running"
    return "pending"


class DinsarProductionService:
    async def create_run(
        self,
        *,
        engine_code: str,
        profile_code: str,
        root_dir: str,
        num_to_process: int,
        timeout_seconds: Optional[int],
        extra: Optional[Dict[str, Any]],
        created_by: Optional[str],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        normalized_engine = str(engine_code or "").strip().lower()
        normalized_profile = str(profile_code or "").strip()
        if normalized_engine != "sarscape":
            raise ValueError(f"Unsupported engine for D-InSAR production run: {engine_code}")

        normalized_root = _normalize_dir(root_dir, "root_dir")
        item_payloads = await asyncio.to_thread(
            _discover_run_items,
            normalized_root,
            max(0, int(num_to_process or 0)),
        )
        if not item_payloads:
            raise ValueError(f"No Task_* directories found under: {normalized_root}")

        run_id = str(uuid.uuid4())
        mode = "custom" if normalized_profile == "custom6" else "metatask"
        task_name = f"D-InSAR production: {normalized_engine}/{normalized_profile}"
        task_params = {
            "engine_code": normalized_engine,
            "profile": normalized_profile,
            "root_dir": normalized_root,
            "num_to_process": int(num_to_process or 0),
            "timeout_seconds": timeout_seconds,
            "extra": dict(extra or {}),
            "mode": mode,
            "production_run_id": run_id,
        }

        task_id: Optional[str] = None
        try:
            task_id = await task_service.create_task(
                task_type=TASK_TYPE_DINSAR_PRODUCTION,
                task_name=task_name,
                params=task_params,
                db=db,
            )

            run = DinsarProductionRunORM(
                run_id=run_id,
                task_id=task_id,
                engine_code=normalized_engine,
                profile_code=normalized_profile,
                mode=mode,
                source_root=normalized_root,
                status=RUN_STATUS_PENDING,
                cancel_requested=False,
                total_items=len(item_payloads),
                completed_items=0,
                failed_items=0,
                skipped_items=0,
                latest_message="Queued",
                params_json=task_params,
                summary_json={
                    "phase": "queued",
                    "selected_task_count": len(item_payloads),
                },
                created_by=created_by,
            )
            db.add(run)
            await db.flush()

            for item_payload in item_payloads:
                db.add(
                    DinsarProductionRunItemORM(
                        run_id=run_id,
                        order_index=item_payload["order_index"],
                        task_name=item_payload["task_name"],
                        task_alias=item_payload["task_alias"],
                        pair_key=item_payload["pair_key"],
                        pair_uid=item_payload["pair_uid"],
                        network_run_id=item_payload["network_run_id"],
                        network_edge_id=item_payload["network_edge_id"],
                        policy_version=item_payload["policy_version"],
                        selection_strategy=item_payload["selection_strategy"],
                        source_task_dir=item_payload["source_task_dir"],
                        results_root_dir=item_payload["results_root_dir"],
                        status=RUN_ITEM_STATUS_PENDING,
                    )
                )

            await db.flush()

            workflow_run_id = await workflow_service.create_run(
                workflow_name="dinsar_sarscape_production",
                steps=[
                    {
                        "step_id": RUNS_STEP_ID,
                        "step_name": RUNS_STEP_NAME,
                        "job_type": TASK_TYPE_DINSAR_PRODUCTION,
                        "payload": {"production_run_id": run_id},
                        "task_id": task_id,
                        "max_attempts": 1,
                    }
                ],
                params={
                    "production_run_id": run_id,
                    "engine_code": normalized_engine,
                    "profile_code": normalized_profile,
                    "root_dir": normalized_root,
                },
                tags={
                    "engine_code": normalized_engine,
                    "profile_code": normalized_profile,
                    "source_root": normalized_root,
                },
                created_by=created_by,
                db=db,
            )
            run.workflow_run_id = workflow_run_id
            await db.commit()
            await db.refresh(run)
        except Exception as exc:
            await db.rollback()
            if task_id:
                try:
                    await task_service.update_task(
                        task_id,
                        status="FAILED",
                        message=f"Failed to create D-InSAR production run: {exc}",
                    )
                except Exception:
                    pass
            raise

        await asyncio.to_thread(
            _append_run_log_sync,
            run_id,
            f"[queued] run_id={run_id} profile={normalized_profile} root={normalized_root} items={len(item_payloads)}",
        )
        return {
            "run_id": run_id,
            "task_id": task_id,
            "workflow_run_id": run.workflow_run_id,
            "status": run.status,
            "selected_task_count": len(item_payloads),
        }

    async def list_runs(
        self,
        db: AsyncSession,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(200, int(limit or 20)))
        safe_offset = max(0, int(offset or 0))
        total_result = await db.execute(select(func.count(DinsarProductionRunORM.id)))
        total = int(total_result.scalar_one() or 0)
        stmt = (
            select(DinsarProductionRunORM)
            .order_by(DinsarProductionRunORM.created_at.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        result = await db.execute(stmt)
        runs = result.scalars().all()
        return {
            "runs": [
                {
                    "run_id": run.run_id,
                    "engine": run.engine_code,
                    "profile_code": run.profile_code,
                    "status": _public_run_status(run.status),
                    "raw_status": run.status,
                    "started_at": _safe_epoch(run.started_at or run.created_at),
                    "ended_at": _safe_epoch(run.ended_at),
                    "task_id": run.task_id,
                    "workflow_run_id": run.workflow_run_id,
                    "root_dir": run.source_root,
                    "message": run.latest_message,
                    "total_items": run.total_items,
                    "completed_items": run.completed_items,
                    "failed_items": run.failed_items,
                    "skipped_items": run.skipped_items,
                }
                for run in runs
            ],
            "total": total,
        }

    async def get_run(self, run_id: str, db: AsyncSession) -> Optional[DinsarProductionRunORM]:
        result = await db.execute(
            select(DinsarProductionRunORM).where(DinsarProductionRunORM.run_id == str(run_id or "").strip())
        )
        return result.scalar_one_or_none()

    async def get_run_by_task_id(self, task_id: str, db: AsyncSession) -> Optional[DinsarProductionRunORM]:
        result = await db.execute(
            select(DinsarProductionRunORM).where(DinsarProductionRunORM.task_id == str(task_id or "").strip())
        )
        return result.scalar_one_or_none()

    async def list_run_items(self, run_id: str, db: AsyncSession) -> List[DinsarProductionRunItemORM]:
        result = await db.execute(
            select(DinsarProductionRunItemORM)
            .where(DinsarProductionRunItemORM.run_id == run_id)
            .order_by(DinsarProductionRunItemORM.order_index.asc(), DinsarProductionRunItemORM.id.asc())
        )
        return result.scalars().all()

    async def request_cancel(self, task_id: str, *, db: AsyncSession) -> Optional[DinsarProductionRunORM]:
        run = await self.get_run_by_task_id(task_id, db)
        if run is None or run.status in TERMINAL_RUN_STATUSES:
            return run
        run.cancel_requested = True
        run.latest_message = "Cancellation requested"
        await db.commit()
        await asyncio.to_thread(_append_run_log_sync, run.run_id, "[cancel] cancellation requested")
        return run

    async def refresh_run_counters(
        self,
        run: DinsarProductionRunORM,
        *,
        db: AsyncSession,
        latest_message: Optional[str] = None,
    ) -> DinsarProductionRunORM:
        rows = await db.execute(
            select(DinsarProductionRunItemORM.status, func.count(DinsarProductionRunItemORM.id))
            .where(DinsarProductionRunItemORM.run_id == run.run_id)
            .group_by(DinsarProductionRunItemORM.status)
        )
        counts = {str(status or "").upper(): int(count or 0) for status, count in rows.fetchall()}
        run.completed_items = counts.get(RUN_ITEM_STATUS_COMPLETED, 0)
        run.failed_items = counts.get(RUN_ITEM_STATUS_FAILED, 0)
        run.skipped_items = counts.get(RUN_ITEM_STATUS_SKIPPED, 0)
        if latest_message is not None:
            run.latest_message = latest_message
        return run

    async def mark_run_started(
        self,
        run: DinsarProductionRunORM,
        *,
        db: AsyncSession,
        message: str,
    ) -> None:
        if run.started_at is None:
            run.started_at = _utcnow()
        run.status = RUN_STATUS_RUNNING
        run.latest_message = message
        await self.refresh_run_counters(run, db=db)
        await db.commit()

    async def begin_item_execution(
        self,
        *,
        run: DinsarProductionRunORM,
        item: DinsarProductionRunItemORM,
        run_key: str,
        db: AsyncSession,
    ) -> DinsarProductionExecutionORM:
        output_dir = _execution_dir(item, run_key)
        _ensure_dir(output_dir)
        execution = DinsarProductionExecutionORM(
            execution_id=run_key,
            run_id=run.run_id,
            item_id=item.id,
            run_key=run_key,
            status=EXECUTION_STATUS_RUNNING,
            output_dir=output_dir,
            log_path=_run_log_path(run.run_id),
            started_at=_utcnow(),
        )
        db.add(execution)
        item.status = RUN_ITEM_STATUS_RUNNING
        item.current_step = "queued"
        item.attempt_count = int(item.attempt_count or 0) + 1
        item.last_error = None
        item.latest_run_key = run_key
        item.latest_output_dir = output_dir
        item.latest_log_path = execution.log_path
        item.started_at = item.started_at or _utcnow()
        run.status = RUN_STATUS_RUNNING
        run.latest_message = f"Running {item.task_alias or item.task_name}"
        await db.commit()
        await db.refresh(execution)
        return execution

    async def set_execution_pid(
        self,
        execution_id: str,
        pid: int,
        *,
        db: AsyncSession,
    ) -> None:
        result = await db.execute(
            select(DinsarProductionExecutionORM).where(DinsarProductionExecutionORM.execution_id == execution_id)
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            return
        execution.subprocess_pid = int(pid or 0) or None
        await db.commit()

    async def update_item_step(
        self,
        item_id: int,
        step_name: str,
        *,
        db: AsyncSession,
    ) -> None:
        result = await db.execute(select(DinsarProductionRunItemORM).where(DinsarProductionRunItemORM.id == item_id))
        item = result.scalar_one_or_none()
        if item is None:
            return
        item.current_step = str(step_name or "").strip() or None
        await db.commit()

    async def mark_item_completed(
        self,
        *,
        run: DinsarProductionRunORM,
        item: DinsarProductionRunItemORM,
        execution: DinsarProductionExecutionORM,
        manifest_path: str,
        metrics: Optional[Dict[str, Any]],
        db: AsyncSession,
    ) -> None:
        now = _utcnow()
        execution.status = EXECUTION_STATUS_COMPLETED
        execution.manifest_path = manifest_path
        execution.metrics_json = metrics or {}
        execution.ended_at = now
        item.status = RUN_ITEM_STATUS_COMPLETED
        item.current_step = "completed"
        item.latest_manifest_path = manifest_path
        item.metrics_json = metrics or {}
        item.ended_at = now
        item.last_error = None
        await self.refresh_run_counters(
            run,
            db=db,
            latest_message=f"Completed {item.task_alias or item.task_name}",
        )
        await db.commit()

    async def mark_item_failed(
        self,
        *,
        run: DinsarProductionRunORM,
        item: DinsarProductionRunItemORM,
        execution: DinsarProductionExecutionORM,
        error_message: str,
        db: AsyncSession,
    ) -> None:
        now = _utcnow()
        execution.status = EXECUTION_STATUS_FAILED
        execution.error_message = error_message
        execution.ended_at = now
        item.status = RUN_ITEM_STATUS_FAILED
        item.current_step = "failed"
        item.last_error = error_message
        item.ended_at = now
        await self.refresh_run_counters(
            run,
            db=db,
            latest_message=f"Failed {item.task_alias or item.task_name}: {error_message}",
        )
        await db.commit()

    async def mark_item_cancelled(
        self,
        *,
        run: DinsarProductionRunORM,
        item: DinsarProductionRunItemORM,
        execution: DinsarProductionExecutionORM,
        error_message: str,
        db: AsyncSession,
    ) -> None:
        now = _utcnow()
        execution.status = EXECUTION_STATUS_CANCELLED
        execution.error_message = error_message
        execution.ended_at = now
        item.status = RUN_ITEM_STATUS_CANCELLED
        item.current_step = "cancelled"
        item.last_error = error_message
        item.ended_at = now
        run.status = RUN_STATUS_CANCELLED
        run.cancel_requested = True
        await self.refresh_run_counters(run, db=db, latest_message=error_message)
        await db.commit()

    async def finalize_run(
        self,
        run: DinsarProductionRunORM,
        *,
        db: AsyncSession,
        status: str,
        summary_payload: Dict[str, Any],
        latest_message: str,
    ) -> None:
        run.status = status
        run.summary_json = summary_payload
        run.latest_message = latest_message
        run.ended_at = _utcnow()
        await self.refresh_run_counters(run, db=db, latest_message=latest_message)
        await db.commit()

    def append_run_log(self, run_id: str, message: str) -> str:
        return _append_run_log_sync(run_id, message)

    def build_execution_manifest(
        self,
        *,
        run: DinsarProductionRunORM,
        item: DinsarProductionRunItemORM,
        execution: DinsarProductionExecutionORM,
        primary_file: str,
        source_files: List[str],
        metrics: Optional[Dict[str, Any]],
    ) -> str:
        manifest_payload = {
            "format_version": 1,
            "run_id": run.run_id,
            "run_key": execution.run_key,
            "task_id": run.task_id,
            "engine_code": run.engine_code,
            "profile_code": run.profile_code,
            "mode": run.mode,
            "task_name": item.task_name,
            "task_alias": item.task_alias,
            "pair_key": item.pair_key,
            "pair_uid": item.pair_uid,
            "network_run_id": item.network_run_id,
            "network_edge_id": item.network_edge_id,
            "policy_version": item.policy_version,
            "selection_strategy": item.selection_strategy,
            "source_root": run.source_root,
            "source_task_dir": item.source_task_dir,
            "output_dir": execution.output_dir,
            "primary_file": primary_file,
            "source_files": source_files,
            "status": EXECUTION_STATUS_COMPLETED,
            "metrics": metrics or {},
            "created_at": _utc_text(execution.started_at or _utcnow()),
            "finished_at": _utc_text(),
        }
        manifest_path = _execution_manifest_path(execution.output_dir)
        return _write_json(manifest_path, manifest_payload)

    def write_current_pointer(
        self,
        *,
        item: DinsarProductionRunItemORM,
        execution: DinsarProductionExecutionORM,
        manifest_path: str,
        primary_file: str,
        source_files: List[str],
    ) -> str:
        pointer_payload = {
            "format_version": 1,
            "run_key": execution.run_key,
            "execution_id": execution.execution_id,
            "status": EXECUTION_STATUS_COMPLETED,
            "output_dir": execution.output_dir,
            "manifest_path": manifest_path,
            "primary_file": primary_file,
            "source_files": source_files,
            "updated_at": _utc_text(),
        }
        return _write_json(_current_pointer_path(item), pointer_payload)

    async def get_active_execution_by_task_id(
        self,
        task_id: str,
        *,
        db: AsyncSession,
    ) -> Optional[DinsarProductionExecutionORM]:
        run = await self.get_run_by_task_id(task_id, db)
        if run is None:
            return None
        result = await db.execute(
            select(DinsarProductionExecutionORM)
            .where(
                DinsarProductionExecutionORM.run_id == run.run_id,
                DinsarProductionExecutionORM.status == EXECUTION_STATUS_RUNNING,
            )
            .order_by(DinsarProductionExecutionORM.started_at.desc(), DinsarProductionExecutionORM.id.desc())
        )
        return result.scalars().first()

    async def kill_active_execution_by_task_id(
        self,
        task_id: str,
        *,
        db: AsyncSession,
    ) -> Optional[int]:
        execution = await self.get_active_execution_by_task_id(task_id, db=db)
        if execution is None or not execution.subprocess_pid:
            return None
        pid = int(execution.subprocess_pid)
        await asyncio.to_thread(_kill_process_tree_sync, pid)
        return pid


dinsar_production_service = DinsarProductionService()
