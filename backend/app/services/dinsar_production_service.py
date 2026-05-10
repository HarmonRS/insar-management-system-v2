from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..config import settings
from ..models import (
    DinsarProductionExecutionORM,
    DinsarProductionRunItemORM,
    DinsarProductionRunORM,
    SystemJobORM,
    SystemTaskORM,
    TaskLogORM,
    WorkflowArtifactORM,
    WorkflowRunORM,
    WorkflowStepORM,
)
from .envi_service import RUNTIME_DIR, _collect_task_folders, _resolve_dinsar_pair_identity, _to_local_path
from .task_service import task_service
from .workflow_service import workflow_service


TASK_TYPE_DINSAR_PRODUCTION = "IDL_RUN_DINSAR"
TASK_TYPE_ISCE2_DINSAR_PRODUCTION = "ISCE2_RUN"
TASK_TYPE_PYINT_DINSAR_PRODUCTION = "PYINT_RUN"
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
RERUN_MODE_UNFINISHED_ONLY = "unfinished_only"
RERUN_MODE_RERUN_ALL = "rerun_all"
VALID_RERUN_MODES = {
    RERUN_MODE_UNFINISHED_ONLY,
    RERUN_MODE_RERUN_ALL,
}

CURRENT_POINTER_FILENAME = "current.json"
CURRENT_POINTER_DIRNAME = "current"
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
_SAFE_POINTER_RE = re.compile(r"[^0-9A-Za-z._-]+")


def _task_type_for_engine(engine_code: str) -> str:
    normalized = str(engine_code or "").strip().lower()
    if normalized == "sarscape":
        return TASK_TYPE_DINSAR_PRODUCTION
    if normalized == "isce2":
        return TASK_TYPE_ISCE2_DINSAR_PRODUCTION
    if normalized in {"pyint", "gamma"}:
        return TASK_TYPE_PYINT_DINSAR_PRODUCTION
    raise ValueError(f"Unsupported engine for D-InSAR production run: {engine_code}")


def _workflow_name_for_engine(engine_code: str) -> str:
    normalized = str(engine_code or "").strip().lower()
    if normalized == "sarscape":
        return "dinsar_sarscape_production"
    if normalized == "isce2":
        return "dinsar_isce2_production"
    if normalized in {"pyint", "gamma"}:
        return "dinsar_pyint_gamma_production"
    raise ValueError(f"Unsupported engine for D-InSAR production run: {engine_code}")


def _workflow_step_name_for_engine(engine_code: str) -> str:
    normalized = str(engine_code or "").strip().lower()
    if normalized == "sarscape":
        return RUNS_STEP_NAME
    if normalized == "isce2":
        return "Execute ISCE2 D-InSAR items"
    if normalized in {"pyint", "gamma"}:
        return "Execute PyINT/Gamma D-InSAR items"
    raise ValueError(f"Unsupported engine for D-InSAR production run: {engine_code}")


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


def _normalize_rerun_mode(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_RERUN_MODES:
        return normalized
    return RERUN_MODE_UNFINISHED_ONLY


def _discover_run_items(root_dir: str) -> List[Dict[str, Any]]:
    task_folders = [root_dir] if _looks_like_task_dir(root_dir) else _collect_task_folders(root_dir)

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
                "results_root_dir": os.path.join(settings.DINSAR_PRODUCT_DIR, pair_key),
            }
        )
    return items


def _current_pointer_path_for_root(
    results_root_dir: str,
    *,
    engine_code: Optional[str] = None,
    profile_code: Optional[str] = None,
) -> str:
    pointer_dir = os.path.join(results_root_dir, CURRENT_POINTER_DIRNAME)
    if engine_code or profile_code:
        pointer_name = (
            f"{_sanitize_pointer_fragment(engine_code or 'engine', 'engine')}__"
            f"{_sanitize_pointer_fragment(profile_code or 'profile', 'profile')}.json"
        )
        return os.path.join(pointer_dir, pointer_name)
    return os.path.join(pointer_dir, CURRENT_POINTER_FILENAME)


def _normalize_existing_file(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    normalized = os.path.normpath(os.path.abspath(_to_local_path(text)))
    return normalized if os.path.isfile(normalized) else ""


def _normalize_existing_dir(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    normalized = os.path.normpath(os.path.abspath(_to_local_path(text)))
    return normalized if os.path.isdir(normalized) else ""


def _has_completed_current_result(
    item_payload: Dict[str, Any],
    *,
    engine_code: str,
    profile_code: str,
) -> bool:
    pointer_path = _current_pointer_path_for_root(
        str(item_payload.get("results_root_dir") or ""),
        engine_code=engine_code,
        profile_code=profile_code,
    )
    if not os.path.isfile(pointer_path):
        return False

    try:
        with open(pointer_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp) or {}
    except Exception:
        return False

    if str(payload.get("status") or "").strip().upper() != EXECUTION_STATUS_COMPLETED:
        return False

    manifest_path = _normalize_existing_file(payload.get("manifest_path"))
    if manifest_path:
        return True

    output_dir = _normalize_existing_dir(payload.get("output_dir"))
    if output_dir and os.path.isfile(_execution_manifest_path(output_dir)):
        return True

    return False


def _select_run_items(
    root_dir: str,
    *,
    engine_code: str,
    profile_code: str,
    num_to_process: int,
    rerun_mode: Optional[str],
) -> Dict[str, Any]:
    discovered_items = _discover_run_items(root_dir)
    normalized_mode = _normalize_rerun_mode(rerun_mode)

    skipped_completed_count = 0
    selected_items: List[Dict[str, Any]] = []
    for item_payload in discovered_items:
        if (
            normalized_mode == RERUN_MODE_UNFINISHED_ONLY
            and _has_completed_current_result(
                item_payload,
                engine_code=engine_code,
                profile_code=profile_code,
            )
        ):
            skipped_completed_count += 1
            continue
        selected_items.append(dict(item_payload))

    if num_to_process > 0:
        selected_items = selected_items[:num_to_process]

    for order_index, item_payload in enumerate(selected_items, start=1):
        item_payload["order_index"] = order_index

    return {
        "items": selected_items,
        "rerun_mode": normalized_mode,
        "discovered_task_count": len(discovered_items),
        "skipped_completed_count": skipped_completed_count,
        "selected_task_count": len(selected_items),
    }


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


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _read_json_if_exists(path: str) -> Dict[str, Any]:
    text = str(path or "").strip()
    if not text or not os.path.isfile(text):
        return {}
    try:
        with open(text, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _maybe_join(base: str, *parts: str) -> str:
    text = str(base or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.join(text, *parts))


def _build_output_paths(
    *,
    engine_code: str,
    item: DinsarProductionRunItemORM,
    run_key: str,
    output_dir: str,
    manifest_path: Optional[str] = None,
) -> Dict[str, Any]:
    run_dir = os.path.normpath(str(output_dir or _execution_dir(item, run_key)))
    native_dir = _maybe_join(run_dir, "native")
    paths: Dict[str, Any] = {
        "run_dir": run_dir,
        "native_dir": native_dir,
        "assets_dir": _maybe_join(run_dir, "assets"),
        "quality_dir": _maybe_join(run_dir, "quality"),
        "manifest_path": str(manifest_path or "").strip(),
    }

    if str(engine_code or "").strip().lower() in {"pyint", "gamma"}:
        pair_key = _first_text(item.pair_key, os.path.basename(os.path.dirname(os.path.dirname(run_dir))))
        project_name = f"{pair_key}_{run_key}" if pair_key and run_key else ""
        work_root = _maybe_join(settings.PYINT_WORK_ROOT, pair_key, run_key)
        project_dir = _maybe_join(work_root, project_name) if project_name else ""

        summary_payload = _read_json_if_exists(_maybe_join(native_dir, "pyint_run_summary.json"))
        summary_project_dir = _first_text(summary_payload.get("project_dir"))
        project_dir = summary_project_dir or project_dir

        master_date = _first_text(summary_payload.get("master_date"))
        slave_date = _first_text(summary_payload.get("slave_date"))
        pair_name = f"{master_date}-{slave_date}" if master_date and slave_date else ""
        ifgrams_dir = _maybe_join(project_dir, "ifgrams", pair_name) if pair_name else _maybe_join(project_dir, "ifgrams")

        paths.update(
            {
                "work_dir": work_root,
                "project_dir": project_dir,
                "ifgrams_dir": ifgrams_dir,
                "reflatten_dir": _maybe_join(run_dir, "gamma_reflatten"),
                "native_reflatten_dir": _maybe_join(native_dir, "reflatten"),
                "pyint_summary_path": _maybe_join(native_dir, "pyint_run_summary.json"),
                "stdout_log": _maybe_join(work_root, "pyint.stdout.log"),
                "stderr_log": _maybe_join(work_root, "pyint.stderr.log"),
            }
        )

    return paths


def _sanitize_pointer_fragment(value: str, default: str) -> str:
    text = _SAFE_POINTER_RE.sub("_", str(value or "").strip()).strip("._")
    return text or default


def _current_pointer_path(
    item: DinsarProductionRunItemORM,
    *,
    engine_code: Optional[str] = None,
    profile_code: Optional[str] = None,
) -> str:
    return _current_pointer_path_for_root(
        item.results_root_dir,
        engine_code=engine_code,
        profile_code=profile_code,
    )


def _execution_manifest_path(execution_dir: str) -> str:
    return os.path.join(execution_dir, EXECUTION_MANIFEST_FILENAME)


def _safe_epoch(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(value.timestamp())


def _runtime_id_for_engine(engine_code: Optional[str]) -> Optional[str]:
    normalized = str(engine_code or "").strip().lower()
    if normalized == "isce2":
        return settings.ISCE2_RUNTIME_ID or None
    if normalized in {"pyint", "gamma"}:
        return settings.PYINT_RUNTIME_ID or None
    return None


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
    async def reconcile_run_with_task(
        self,
        run: DinsarProductionRunORM,
        task: Optional[SystemTaskORM],
        *,
        db: AsyncSession,
    ) -> bool:
        if task is None:
            return False

        run_status = str(run.status or "").strip().upper()
        task_status = str(task.status or "").strip().upper()
        if run_status in TERMINAL_RUN_STATUSES:
            return False
        if task_status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            return False

        if task_status == "COMPLETED":
            next_status = RUN_STATUS_COMPLETED
        elif task_status == "CANCELLED":
            next_status = RUN_STATUS_CANCELLED
            run.cancel_requested = True
        else:
            next_status = RUN_STATUS_FAILED

        summary_payload = dict(run.summary_json or {})
        summary_payload["reconciled_from_task_status"] = task_status
        latest_message = str(task.message or "").strip() or f"Reconciled from task status {task_status}"
        run.status = next_status
        run.summary_json = summary_payload
        run.latest_message = latest_message
        run.ended_at = run.ended_at or _utcnow()
        await self.refresh_run_counters(run, db=db, latest_message=latest_message)
        return True

    async def create_run(
        self,
        *,
        engine_code: str,
        profile_code: str,
        root_dir: str,
        num_to_process: int,
        rerun_mode: Optional[str],
        timeout_seconds: Optional[int],
        extra: Optional[Dict[str, Any]],
        created_by: Optional[str],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        normalized_engine = str(engine_code or "").strip().lower()
        normalized_profile = str(profile_code or "").strip()
        task_type = _task_type_for_engine(normalized_engine)
        workflow_name = _workflow_name_for_engine(normalized_engine)
        workflow_step_name = _workflow_step_name_for_engine(normalized_engine)

        normalized_root = _normalize_dir(root_dir, "root_dir")
        selection = await asyncio.to_thread(
            _select_run_items,
            normalized_root,
            engine_code=normalized_engine,
            profile_code=normalized_profile,
            num_to_process=max(0, int(num_to_process or 0)),
            rerun_mode=rerun_mode,
        )
        item_payloads = selection["items"]
        if not item_payloads:
            if (
                selection["discovered_task_count"] > 0
                and selection["skipped_completed_count"] > 0
                and selection["rerun_mode"] == RERUN_MODE_UNFINISHED_ONLY
            ):
                raise ValueError(
                    f"All discovered Task_* directories already have completed "
                    f"{normalized_engine}/{normalized_profile} results under: {normalized_root}"
                )
            raise ValueError(f"No Task_* directories found under: {normalized_root}")

        run_id = str(uuid.uuid4())
        if normalized_engine == "sarscape":
            mode = "custom" if normalized_profile == "custom6" else "metatask"
        else:
            mode = "managed"
        task_name = f"D-InSAR production: {normalized_engine}/{normalized_profile}"
        task_params = {
            "engine_code": normalized_engine,
            "profile": normalized_profile,
            "root_dir": normalized_root,
            "num_to_process": int(num_to_process or 0),
            "rerun_mode": selection["rerun_mode"],
            "timeout_seconds": timeout_seconds,
            "extra": dict(extra or {}),
            "mode": mode,
            "production_run_id": run_id,
        }

        task_id: Optional[str] = None
        try:
            task_id = await task_service.create_task(
                task_type=task_type,
                task_name=task_name,
                params=task_params,
                db=db,
            )

            run = DinsarProductionRunORM(
                run_id=run_id,
                task_id=task_id,
                product_family="dinsar",
                engine_code=normalized_engine,
                profile_code=normalized_profile,
                mode=mode,
                source_root=normalized_root,
                publish_root_dir=settings.DINSAR_PRODUCT_DIR,
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
                    "discovered_task_count": selection["discovered_task_count"],
                    "skipped_completed_count": selection["skipped_completed_count"],
                    "rerun_mode": selection["rerun_mode"],
                    "product_family": "dinsar",
                    "publish_root_dir": settings.DINSAR_PRODUCT_DIR,
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
                workflow_name=workflow_name,
                steps=[
                    {
                        "step_id": RUNS_STEP_ID,
                        "step_name": workflow_step_name,
                        "job_type": task_type,
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
            (
                f"[queued] run_id={run_id} profile={normalized_profile} root={normalized_root} "
                f"items={len(item_payloads)} rerun_mode={selection['rerun_mode']} "
                f"skipped_completed={selection['skipped_completed_count']}"
            ),
        )
        return {
            "run_id": run_id,
            "task_id": task_id,
            "workflow_run_id": run.workflow_run_id,
            "status": run.status,
            "selected_task_count": len(item_payloads),
            "discovered_task_count": selection["discovered_task_count"],
            "skipped_completed_count": selection["skipped_completed_count"],
            "rerun_mode": selection["rerun_mode"],
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
        run_ids = [run.run_id for run in runs if run.run_id]
        pending_reconcile = [
            run
            for run in runs
            if run.task_id and str(run.status or "").strip().upper() not in TERMINAL_RUN_STATUSES
        ]
        if pending_reconcile:
            task_ids = [run.task_id for run in pending_reconcile if run.task_id]
            task_result = await db.execute(
                select(SystemTaskORM).where(SystemTaskORM.task_id.in_(task_ids))
            )
            tasks_by_id = {task.task_id: task for task in task_result.scalars().all()}
            changed = False
            for run in pending_reconcile:
                changed = await self.reconcile_run_with_task(
                    run,
                    tasks_by_id.get(run.task_id),
                    db=db,
                ) or changed
            if changed:
                await db.commit()
        items_by_run_id: Dict[str, List[DinsarProductionRunItemORM]] = {}
        if run_ids:
            items_result = await db.execute(
                select(DinsarProductionRunItemORM)
                .where(DinsarProductionRunItemORM.run_id.in_(run_ids))
                .order_by(DinsarProductionRunItemORM.order_index.asc(), DinsarProductionRunItemORM.id.asc())
            )
            for item in items_result.scalars().all():
                items_by_run_id.setdefault(item.run_id, []).append(item)
        return {
            "runs": [
                {
                    "run_id": run.run_id,
                    "product_family": run.product_family,
                    "engine": run.engine_code,
                    "profile_code": run.profile_code,
                    "status": _public_run_status(run.status),
                    "raw_status": run.status,
                    "started_at": _safe_epoch(run.started_at or run.created_at),
                    "ended_at": _safe_epoch(run.ended_at),
                    "task_id": run.task_id,
                    "workflow_run_id": run.workflow_run_id,
                    "root_dir": run.source_root,
                    "publish_root_dir": run.publish_root_dir,
                    "message": run.latest_message,
                    "total_items": run.total_items,
                    "completed_items": run.completed_items,
                    "failed_items": run.failed_items,
                    "skipped_items": run.skipped_items,
                    "items": [
                        {
                            "task_name": item.task_name,
                            "task_alias": item.task_alias,
                            "pair_key": item.pair_key,
                            "status": item.status,
                            "current_step": item.current_step,
                            "latest_run_key": item.latest_run_key,
                            "latest_output_dir": item.latest_output_dir,
                            "latest_manifest_path": item.latest_manifest_path,
                            "last_error": item.last_error,
                            "paths": _build_output_paths(
                                engine_code=run.engine_code,
                                item=item,
                                run_key=str(item.latest_run_key or ""),
                                output_dir=str(item.latest_output_dir or _execution_dir(item, str(item.latest_run_key or ""))),
                                manifest_path=item.latest_manifest_path,
                            )
                            if item.latest_run_key
                            else {},
                        }
                        for item in items_by_run_id.get(run.run_id, [])[:5]
                    ],
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

    def read_run_log(self, run_id: str, *, max_bytes: int = 200 * 1024) -> Dict[str, Any]:
        log_path = _run_log_path(run_id)
        if not os.path.isfile(log_path):
            raise FileNotFoundError(log_path)
        size_bytes = os.path.getsize(log_path)
        truncated = size_bytes > max_bytes
        with open(log_path, "r", encoding="utf-8", errors="replace") as fp:
            if truncated:
                fp.seek(size_bytes - max_bytes)
                content = "...[日志已截断，仅显示末尾]...\n" + fp.read()
            else:
                content = fp.read()
        return {
            "run_id": run_id,
            "content": content,
            "size_bytes": size_bytes,
            "truncated": truncated,
            "log_path": log_path,
        }

    def delete_run_log(self, run_id: str) -> bool:
        log_path = _run_log_path(run_id)
        if not os.path.isfile(log_path):
            return False
        os.unlink(log_path)
        return True

    async def delete_run_record(
        self,
        run_id: str,
        *,
        db: AsyncSession,
    ) -> Optional[Dict[str, Any]]:
        run = await self.get_run(run_id, db)
        if run is None:
            return None
        if str(run.status or "").strip().upper() not in TERMINAL_RUN_STATUSES:
            raise ValueError("Cannot delete a pending or running production run.")

        task_id = str(run.task_id or "").strip()
        workflow_run_id = str(run.workflow_run_id or "").strip()
        if task_id:
            task_result = await db.execute(
                select(SystemTaskORM).where(SystemTaskORM.task_id == task_id)
            )
            task = task_result.scalar_one_or_none()
            if task is not None and str(task.status or "").strip().upper() in {"PENDING", "RUNNING"}:
                raise ValueError("Cannot delete a production run with a pending or running task.")

        deleted = {
            "run_id": run.run_id,
            "task_id": task_id or None,
            "workflow_run_id": workflow_run_id or None,
        }
        await db.execute(delete(DinsarProductionExecutionORM).where(DinsarProductionExecutionORM.run_id == run.run_id))
        await db.execute(delete(DinsarProductionRunItemORM).where(DinsarProductionRunItemORM.run_id == run.run_id))
        await db.execute(delete(DinsarProductionRunORM).where(DinsarProductionRunORM.run_id == run.run_id))

        if task_id:
            await db.execute(delete(TaskLogORM).where(TaskLogORM.task_id == task_id))
            await db.execute(delete(SystemJobORM).where(SystemJobORM.task_id == task_id))
            await db.execute(delete(SystemTaskORM).where(SystemTaskORM.task_id == task_id))
        if workflow_run_id:
            await db.execute(delete(SystemJobORM).where(SystemJobORM.workflow_run_id == workflow_run_id))
            await db.execute(delete(WorkflowArtifactORM).where(WorkflowArtifactORM.run_id == workflow_run_id))
            await db.execute(delete(WorkflowStepORM).where(WorkflowStepORM.run_id == workflow_run_id))
            await db.execute(delete(WorkflowRunORM).where(WorkflowRunORM.run_id == workflow_run_id))

        await db.commit()
        try:
            deleted["log_deleted"] = await asyncio.to_thread(self.delete_run_log, run_id)
        except OSError:
            deleted["log_deleted"] = False
        return deleted

    def build_execution_manifest(
        self,
        *,
        run: DinsarProductionRunORM,
        item: DinsarProductionRunItemORM,
        execution: DinsarProductionExecutionORM,
        primary_file: str,
        source_files: List[str],
        native_output_dir: Optional[str],
        metrics: Optional[Dict[str, Any]],
    ) -> str:
        manifest_payload = {
            "format_version": 1,
            "run_id": run.run_id,
            "product_family": run.product_family or "dinsar",
            "run_key": execution.run_key,
            "task_id": run.task_id,
            "engine_code": run.engine_code,
            "profile_code": run.profile_code,
            "runtime_id": _runtime_id_for_engine(run.engine_code),
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
            "results_root_dir": item.results_root_dir,
            "publish_root_dir": run.publish_root_dir,
            "output_dir": execution.output_dir,
            "native_output_dir": str(native_output_dir or execution.output_dir),
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
        run: DinsarProductionRunORM,
        item: DinsarProductionRunItemORM,
        execution: DinsarProductionExecutionORM,
        manifest_path: str,
        primary_file: str,
        source_files: List[str],
        native_output_dir: Optional[str],
    ) -> str:
        pointer_payload = {
            "format_version": 1,
            "product_family": run.product_family or "dinsar",
            "engine_code": run.engine_code,
            "profile_code": run.profile_code,
            "runtime_id": _runtime_id_for_engine(run.engine_code),
            "run_key": execution.run_key,
            "execution_id": execution.execution_id,
            "status": EXECUTION_STATUS_COMPLETED,
            "output_dir": execution.output_dir,
            "native_output_dir": str(native_output_dir or execution.output_dir),
            "manifest_path": manifest_path,
            "primary_file": primary_file,
            "source_files": source_files,
            "updated_at": _utc_text(),
        }
        pointer_path = _current_pointer_path(
            item,
            engine_code=run.engine_code,
            profile_code=run.profile_code,
        )
        return _write_json(pointer_path, pointer_payload)

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
