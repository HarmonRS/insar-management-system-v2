from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (
    DinsarProductionExecutionORM,
    DinsarProductionRunItemORM,
    DinsarProductionRunORM,
    DinsarResultORM,
    ResultProductORM,
    SystemJobORM,
    SystemTaskORM,
    TaskLogORM,
    WorkflowArtifactORM,
    WorkflowRunORM,
    WorkflowStepORM,
)
from .dinsar_production_service import dinsar_production_service


TERMINAL_TASK_STATUSES = {"COMPLETED", "FAILED", "PARTIAL_SUCCESS", "CANCELLED"}
MAINTENANCE_LIST_STATUSES = {"FAILED", "PARTIAL_SUCCESS", "CANCELLED", "PENDING", "RUNNING"}
ACTIVE_TASK_STATUSES = {"PENDING", "RUNNING"}
ACTIVE_JOB_STATUSES = {"READY", "RETRY", "RUNNING"}
DINSAR_TASK_TYPES = {"LANDSAR_RUN", "LANDSAR_CLUSTER_RUN", "PYINT_RUN", "IDL_RUN_DINSAR"}
SUPPORTED_CLEANUP_TASK_TYPES = DINSAR_TASK_TYPES | {"COPY_DATA"}
DEFAULT_TASK_TYPES = SUPPORTED_CLEANUP_TASK_TYPES | {"PAIRING_CACHE_REBUILD"}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _norm_status(value: Any) -> str:
    return str(value or "").strip().upper()


def _compact(value: Any, limit: int = 220) -> Optional[str]:
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _normalize_path_text(value: Any) -> str:
    return os.path.normpath(str(value or "").strip().strip('"').strip("'"))


def _path_exists(path: str) -> bool:
    return bool(path) and os.path.exists(path)


def _safe_count(rows: Iterable[Any]) -> int:
    return len(list(rows))


class OpsMaintenanceService:
    async def list_tasks(
        self,
        db: AsyncSession,
        *,
        task_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        safe_limit = max(1, min(int(limit or 50), 200))
        safe_offset = max(0, int(offset or 0))
        task_types = [task_type.strip().upper()] if task_type else sorted(DEFAULT_TASK_TYPES)
        status_filter = status.strip().upper() if status else ""

        conditions = [SystemTaskORM.task_type.in_(task_types)]
        if status_filter:
            conditions.append(SystemTaskORM.status == status_filter)
        else:
            conditions.append(SystemTaskORM.status.in_(sorted(MAINTENANCE_LIST_STATUSES)))

        result = await db.execute(
            select(SystemTaskORM)
            .where(*conditions)
            .order_by(SystemTaskORM.updated_at.desc(), SystemTaskORM.id.desc())
            .offset(safe_offset)
            .limit(safe_limit)
        )
        tasks = list(result.scalars().all())
        items = [await self._build_task_summary(db, task) for task in tasks]
        abnormal_items = [
            item
            for item in items
            if item.get("issue_level") in {"warning", "danger"}
            or _norm_status(item.get("status")) in {"FAILED", "PARTIAL_SUCCESS", "CANCELLED"}
        ]
        return {
            "items": abnormal_items,
            "limit": safe_limit,
            "offset": safe_offset,
            "returned": len(abnormal_items),
        }

    async def diagnose_task(self, db: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
        task = await self._get_task(db, task_id)
        if task is None:
            return None
        jobs = await self._get_jobs(db, task.task_id)
        run = await self._get_production_run_for_task(db, task, jobs)
        item_counts: Dict[str, int] = {}
        execution_counts: Dict[str, int] = {}
        disk_paths: List[Dict[str, Any]] = []
        products: List[Dict[str, Any]] = []

        if run is not None:
            item_counts = await self._status_counts(db, DinsarProductionRunItemORM, run.run_id)
            execution_counts = await self._status_counts(db, DinsarProductionExecutionORM, run.run_id)
            disk_paths.extend(await self._collect_run_disk_paths(db, run))
            products = await self._collect_result_products(db, run)
        related_tasks = await self._related_copy_tasks_for_run(db, run) if run is not None else []

        copy_dest = self._copy_task_dest_dir(task)
        if copy_dest:
            disk_paths.append(self._path_payload(copy_dest, "task_pool"))

        recent_logs = await self._recent_logs(db, task.task_id)
        findings, cleanup_supported, cleanup_blockers = self._diagnose_findings(
            task=task,
            jobs=jobs,
            run=run,
            item_counts=item_counts,
            execution_counts=execution_counts,
            disk_paths=disk_paths,
        )
        return {
            "task": self._task_payload(task),
            "jobs": [self._job_payload(job) for job in jobs],
            "production_run": self._run_payload(run) if run else None,
            "production_item_counts": item_counts,
            "production_execution_counts": execution_counts,
            "result_products": products,
            "related_tasks": [self._task_payload(item) for item in related_tasks],
            "recent_logs": recent_logs,
            "disk_paths": disk_paths,
            "diagnosis": {
                "summary": findings[0] if findings else "未发现明显异常。",
                "findings": findings,
                "cleanup_supported": cleanup_supported,
                "cleanup_blockers": cleanup_blockers,
            },
        }

    async def cleanup_preview(self, db: AsyncSession, task_id: str) -> Optional[Dict[str, Any]]:
        diagnosis = await self.diagnose_task(db, task_id)
        if diagnosis is None:
            return None

        task = diagnosis["task"]
        task_type = _norm_status(task.get("task_type"))
        task_status = _norm_status(task.get("status"))
        cleanup_supported = bool(diagnosis["diagnosis"].get("cleanup_supported"))
        blockers = list(diagnosis["diagnosis"].get("cleanup_blockers") or [])
        if task_type not in SUPPORTED_CLEANUP_TASK_TYPES:
            blockers.append(f"第一版暂不支持清理任务类型 {task_type}。")
        if task_status in ACTIVE_TASK_STATUSES:
            blockers.append("任务仍处于活动状态，不能清理。")

        db_counts = await self._cleanup_db_counts(db, diagnosis)
        disk_deletes = self._cleanup_disk_targets(diagnosis)
        blocked = bool(blockers) or not cleanup_supported
        return {
            "task_id": task_id,
            "blocked": blocked,
            "blockers": blockers,
            "cleanup_supported": cleanup_supported and not blocked,
            "database_deletes": db_counts,
            "disk_deletes": disk_deletes,
        }

    async def cleanup_task(
        self,
        db: AsyncSession,
        task_id: str,
        *,
        options: Dict[str, bool],
    ) -> Optional[Dict[str, Any]]:
        preview = await self.cleanup_preview(db, task_id)
        if preview is None:
            return None
        if preview.get("blocked"):
            raise ValueError("; ".join(preview.get("blockers") or ["清理被阻止。"]))

        deleted_db: Dict[str, int] = {}
        task = await self._get_task(db, task_id)
        if task is None:
            return None
        jobs = await self._get_jobs(db, task_id)
        run = await self._get_production_run_for_task(db, task, jobs)

        delete_logs = bool(options.get("delete_logs", True))
        delete_task_records = bool(options.get("delete_task_records", True))
        delete_production_records = bool(options.get("delete_production_records", True))
        delete_result_products = bool(options.get("delete_result_products", True))
        delete_task_pool_dir = bool(options.get("delete_task_pool_dir", True))
        related_copy_tasks = await self._related_copy_tasks_for_run(db, run) if run is not None else []

        if delete_result_products and run is not None:
            products = await self._result_product_orms_for_run(db, run)
            product_ids = [item.product_id for item in products]
            compat_ids = await self._compat_ids_for_products(db, product_ids)
            if product_ids:
                result = await db.execute(delete(ResultProductORM).where(ResultProductORM.product_id.in_(product_ids)))
                deleted_db["result_products"] = int(result.rowcount or 0)
            if compat_ids:
                result = await db.execute(delete(DinsarResultORM).where(DinsarResultORM.id.in_(compat_ids)))
                deleted_db["dinsar_results"] = int(result.rowcount or 0)

        if delete_production_records and run is not None:
            deleted_run = await dinsar_production_service.delete_run_record(run.run_id, db=db)
            deleted_db["dinsar_production_runs"] = 1 if deleted_run else 0
            deleted_db["dinsar_production_run_items"] = int(preview["database_deletes"].get("dinsar_production_run_items", 0))
            deleted_db["dinsar_production_executions"] = int(preview["database_deletes"].get("dinsar_production_executions", 0))
            deleted_db["system_jobs"] = int(preview["database_deletes"].get("system_jobs", 0))
            deleted_db["system_tasks"] = 1
            deleted_db["task_logs"] = int(preview["database_deletes"].get("task_logs", 0))
            if delete_task_pool_dir:
                related_deleted = await self._delete_related_tasks(db, related_copy_tasks)
                for key, value in related_deleted.items():
                    deleted_db[key] = int(deleted_db.get(key, 0)) + int(value or 0)
        else:
            if delete_logs:
                result = await db.execute(delete(TaskLogORM).where(TaskLogORM.task_id == task_id))
                deleted_db["task_logs"] = int(result.rowcount or 0)
            if delete_task_records:
                result = await db.execute(delete(SystemJobORM).where(SystemJobORM.task_id == task_id))
                deleted_db["system_jobs"] = int(result.rowcount or 0)
                result = await db.execute(delete(SystemTaskORM).where(SystemTaskORM.task_id == task_id))
                deleted_db["system_tasks"] = int(result.rowcount or 0)
                await db.commit()

        disk_result = await self._delete_disk_targets(preview, options)
        return {
            "task_id": task_id,
            "deleted_database": deleted_db,
            "deleted_disk": disk_result,
        }

    async def _delete_related_tasks(
        self,
        db: AsyncSession,
        tasks: List[SystemTaskORM],
    ) -> Dict[str, int]:
        task_ids = [task.task_id for task in tasks if task.task_id]
        if not task_ids:
            return {}
        result = await db.execute(delete(TaskLogORM).where(TaskLogORM.task_id.in_(task_ids)))
        logs = int(result.rowcount or 0)
        result = await db.execute(delete(SystemJobORM).where(SystemJobORM.task_id.in_(task_ids)))
        jobs = int(result.rowcount or 0)
        result = await db.execute(delete(SystemTaskORM).where(SystemTaskORM.task_id.in_(task_ids)))
        task_count = int(result.rowcount or 0)
        await db.commit()
        return {
            "related_task_logs": logs,
            "related_system_jobs": jobs,
            "related_system_tasks": task_count,
        }

    async def _get_task(self, db: AsyncSession, task_id: str) -> Optional[SystemTaskORM]:
        result = await db.execute(select(SystemTaskORM).where(SystemTaskORM.task_id == str(task_id or "").strip()))
        return result.scalar_one_or_none()

    async def _get_jobs(self, db: AsyncSession, task_id: str) -> List[SystemJobORM]:
        result = await db.execute(
            select(SystemJobORM)
            .where(SystemJobORM.task_id == str(task_id or "").strip())
            .order_by(SystemJobORM.updated_at.desc(), SystemJobORM.id.desc())
        )
        return list(result.scalars().all())

    async def _get_production_run_for_task(
        self,
        db: AsyncSession,
        task: SystemTaskORM,
        jobs: List[SystemJobORM],
    ) -> Optional[DinsarProductionRunORM]:
        candidates: List[str] = []
        params = task.params or {}
        if isinstance(params, dict):
            for key in ("production_run_id", "run_id"):
                if params.get(key):
                    candidates.append(str(params[key]))
        for job in jobs:
            payload = job.payload or {}
            if isinstance(payload, dict):
                for key in ("production_run_id", "run_id", "dinsar_run_id"):
                    if payload.get(key):
                        candidates.append(str(payload[key]))
        if candidates:
            result = await db.execute(select(DinsarProductionRunORM).where(DinsarProductionRunORM.run_id.in_(candidates)))
            run = result.scalar_one_or_none()
            if run is not None:
                return run
        result = await db.execute(select(DinsarProductionRunORM).where(DinsarProductionRunORM.task_id == task.task_id))
        return result.scalar_one_or_none()

    async def _build_task_summary(self, db: AsyncSession, task: SystemTaskORM) -> Dict[str, Any]:
        jobs = await self._get_jobs(db, task.task_id)
        run = await self._get_production_run_for_task(db, task, jobs)
        item_counts: Dict[str, int] = {}
        execution_counts: Dict[str, int] = {}
        if run is not None:
            item_counts = await self._status_counts(db, DinsarProductionRunItemORM, run.run_id)
            execution_counts = await self._status_counts(db, DinsarProductionExecutionORM, run.run_id)
        findings, cleanup_supported, blockers = self._diagnose_findings(
            task=task,
            jobs=jobs,
            run=run,
            item_counts=item_counts,
            execution_counts=execution_counts,
            disk_paths=[],
        )
        log_count = await self._count(db, select(func.count()).select_from(TaskLogORM).where(TaskLogORM.task_id == task.task_id))
        return {
            **self._task_payload(task),
            "run_id": run.run_id if run else None,
            "job_id": jobs[0].job_id if jobs else None,
            "job_status": jobs[0].status if jobs else None,
            "issue_level": "danger" if _norm_status(task.status) == "FAILED" else ("warning" if findings else "info"),
            "issue_summary": findings[0] if findings else _compact(task.message, 160),
            "cleanup_supported": cleanup_supported,
            "cleanup_blocked_reason": "; ".join(blockers) if blockers else "",
            "counts": {
                "jobs": len(jobs),
                "logs": log_count,
                "run_items": sum(item_counts.values()),
                "executions": sum(execution_counts.values()),
                "completed_items": item_counts.get("COMPLETED", 0),
                "failed_items": item_counts.get("FAILED", 0),
                "pending_items": item_counts.get("PENDING", 0),
                "running_items": item_counts.get("RUNNING", 0),
            },
        }

    async def _status_counts(self, db: AsyncSession, model: Any, run_id: str) -> Dict[str, int]:
        result = await db.execute(
            select(model.status, func.count())
            .where(model.run_id == run_id)
            .group_by(model.status)
        )
        return {str(status or "UNKNOWN").upper(): int(count or 0) for status, count in result.all()}

    async def _recent_logs(self, db: AsyncSession, task_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            select(TaskLogORM)
            .where(TaskLogORM.task_id == task_id)
            .order_by(TaskLogORM.timestamp.desc(), TaskLogORM.id.desc())
            .limit(30)
        )
        return [
            {
                "id": item.id,
                "level": item.log_level,
                "message": _compact(item.message, 500),
                "timestamp": _dt(item.timestamp),
            }
            for item in result.scalars().all()
        ]

    async def _collect_run_disk_paths(self, db: AsyncSession, run: DinsarProductionRunORM) -> List[Dict[str, Any]]:
        paths: Dict[str, Dict[str, Any]] = {}
        if run.source_root:
            paths[_normalize_path_text(run.source_root)] = self._path_payload(run.source_root, "task_pool")
        result = await db.execute(select(DinsarProductionExecutionORM).where(DinsarProductionExecutionORM.run_id == run.run_id))
        for execution in result.scalars().all():
            if execution.output_dir:
                publish_dir = self._publish_package_dir(execution.output_dir)
                paths[publish_dir] = self._path_payload(publish_dir, "production_result")
        log_path = dinsar_production_service.read_run_log(run.run_id, max_bytes=1).get("path")
        if log_path:
            paths[_normalize_path_text(log_path)] = self._path_payload(log_path, "run_log")
        return list(paths.values())

    async def _collect_result_products(self, db: AsyncSession, run: DinsarProductionRunORM) -> List[Dict[str, Any]]:
        products = await self._result_product_orms_for_run(db, run)
        return [
            {
                "product_id": item.product_id,
                "display_name": item.display_name,
                "status": item.status,
                "health_status": item.health_status,
                "publish_dir": item.publish_dir,
                "manifest_path": item.manifest_path,
            }
            for item in products
        ]

    async def _result_product_orms_for_run(self, db: AsyncSession, run: DinsarProductionRunORM) -> List[ResultProductORM]:
        result = await db.execute(select(DinsarProductionExecutionORM).where(DinsarProductionExecutionORM.run_id == run.run_id))
        dirs = [self._publish_package_dir(item.output_dir) for item in result.scalars().all() if item.output_dir]
        clauses = []
        for path in dirs:
            clauses.append(ResultProductORM.publish_dir == path)
            clauses.append(ResultProductORM.native_output_dir.like(path + "%"))
            clauses.append(ResultProductORM.manifest_path.like(path + "%"))
            clauses.append(ResultProductORM.primary_asset_path.like(path + "%"))
        if not clauses:
            return []
        products = await db.execute(select(ResultProductORM).where(or_(*clauses)))
        by_id: Dict[str, ResultProductORM] = {}
        for product in products.scalars().all():
            by_id[product.product_id] = product
        return list(by_id.values())

    async def _compat_ids_for_products(self, db: AsyncSession, product_ids: List[str]) -> List[int]:
        if not product_ids:
            return []
        result = await db.execute(select(DinsarResultORM).where(DinsarResultORM.compat_product_id.in_(product_ids)))
        return [int(item.id) for item in result.scalars().all()]

    def _publish_package_dir(self, output_dir: str) -> str:
        normalized = _normalize_path_text(output_dir)
        marker = os.sep + "runs" + os.sep
        if marker.lower() in normalized.lower():
            lower = normalized.lower()
            index = lower.index(marker.lower())
            return normalized[:index]
        return normalized

    def _copy_task_dest_dir(self, task: SystemTaskORM) -> str:
        params = task.params if isinstance(task.params, dict) else {}
        return _normalize_path_text(params.get("dest_dir")) if params.get("dest_dir") else ""

    async def _related_copy_tasks_for_run(
        self,
        db: AsyncSession,
        run: Optional[DinsarProductionRunORM],
    ) -> List[SystemTaskORM]:
        source_root = _normalize_path_text(run.source_root if run is not None else "")
        if not source_root:
            return []
        result = await db.execute(
            select(SystemTaskORM)
            .where(SystemTaskORM.task_type == "COPY_DATA")
            .order_by(SystemTaskORM.updated_at.desc(), SystemTaskORM.id.desc())
            .limit(1000)
        )
        tasks = []
        for task in result.scalars().all():
            if _normalize_path_text(self._copy_task_dest_dir(task)).lower() == source_root.lower():
                tasks.append(task)
        return tasks

    def _diagnose_findings(
        self,
        *,
        task: SystemTaskORM,
        jobs: List[SystemJobORM],
        run: Optional[DinsarProductionRunORM],
        item_counts: Dict[str, int],
        execution_counts: Dict[str, int],
        disk_paths: List[Dict[str, Any]],
    ) -> tuple[List[str], bool, List[str]]:
        findings: List[str] = []
        blockers: List[str] = []
        status = _norm_status(task.status)
        if status == "FAILED":
            findings.append("任务已失败，需要人工确认后清理。")
        elif status == "PARTIAL_SUCCESS":
            findings.append("任务部分成功，清理前请确认保留策略。")
        elif status == "CANCELLED":
            findings.append("任务已取消，可按需清理残留记录和目录。")
        elif status in ACTIVE_TASK_STATUSES:
            blockers.append("任务仍处于活动状态。")

        active_jobs = [job for job in jobs if _norm_status(job.status) in ACTIVE_JOB_STATUSES]
        stale_cutoff = _utcnow_naive() - timedelta(seconds=int(getattr(settings, "JOB_WORKER_STALE_RUNNING_SECONDS", 7200)))
        stale_jobs = [
            job for job in active_jobs
            if job.heartbeat_at is not None and job.heartbeat_at < stale_cutoff
        ]
        if stale_jobs:
            findings.append(f"发现 {len(stale_jobs)} 个心跳超时 job。")
        if active_jobs and not stale_jobs:
            blockers.append("仍存在活动 job。")

        residual_running = item_counts.get("RUNNING", 0) + execution_counts.get("RUNNING", 0)
        if run is not None and residual_running:
            findings.append(f"生产 run 中仍有 {residual_running} 个 RUNNING 残留。")
        if run is not None and item_counts.get("PENDING", 0):
            findings.append(f"生产 run 中仍有 {item_counts.get('PENDING', 0)} 个 PENDING 项未执行。")

        missing_paths = [item for item in disk_paths if item.get("path") and not item.get("exists")]
        if missing_paths:
            findings.append(f"有 {len(missing_paths)} 个登记路径已不存在。")

        cleanup_supported = _norm_status(task.task_type) in SUPPORTED_CLEANUP_TASK_TYPES and not blockers
        return findings, cleanup_supported, blockers

    async def _cleanup_db_counts(self, db: AsyncSession, diagnosis: Dict[str, Any]) -> Dict[str, int]:
        task_id = diagnosis["task"]["task_id"]
        run = diagnosis.get("production_run") or {}
        run_id = run.get("run_id")
        workflow_run_id = run.get("workflow_run_id")
        counts = {
            "system_tasks": await self._count(db, select(func.count()).select_from(SystemTaskORM).where(SystemTaskORM.task_id == task_id)),
            "system_jobs": await self._count(db, select(func.count()).select_from(SystemJobORM).where(SystemJobORM.task_id == task_id)),
            "task_logs": await self._count(db, select(func.count()).select_from(TaskLogORM).where(TaskLogORM.task_id == task_id)),
            "related_system_tasks": 0,
            "related_system_jobs": 0,
            "related_task_logs": 0,
            "dinsar_production_runs": 0,
            "dinsar_production_run_items": 0,
            "dinsar_production_executions": 0,
            "result_products": len(diagnosis.get("result_products") or []),
            "dinsar_results": 0,
            "workflow_runs": 0,
            "workflow_steps": 0,
            "workflow_artifacts": 0,
        }
        if run_id:
            counts["dinsar_production_runs"] = await self._count(db, select(func.count()).select_from(DinsarProductionRunORM).where(DinsarProductionRunORM.run_id == run_id))
            counts["dinsar_production_run_items"] = await self._count(db, select(func.count()).select_from(DinsarProductionRunItemORM).where(DinsarProductionRunItemORM.run_id == run_id))
            counts["dinsar_production_executions"] = await self._count(db, select(func.count()).select_from(DinsarProductionExecutionORM).where(DinsarProductionExecutionORM.run_id == run_id))
            run_obj = await self._get_run_by_id(db, run_id)
            related_tasks = await self._related_copy_tasks_for_run(db, run_obj)
            related_task_ids = [item.task_id for item in related_tasks if item.task_id]
            counts["related_system_tasks"] = len(related_task_ids)
            if related_task_ids:
                counts["related_system_jobs"] = await self._count(db, select(func.count()).select_from(SystemJobORM).where(SystemJobORM.task_id.in_(related_task_ids)))
                counts["related_task_logs"] = await self._count(db, select(func.count()).select_from(TaskLogORM).where(TaskLogORM.task_id.in_(related_task_ids)))
        if workflow_run_id:
            counts["workflow_runs"] = await self._count(db, select(func.count()).select_from(WorkflowRunORM).where(WorkflowRunORM.run_id == workflow_run_id))
            counts["workflow_steps"] = await self._count(db, select(func.count()).select_from(WorkflowStepORM).where(WorkflowStepORM.run_id == workflow_run_id))
            counts["workflow_artifacts"] = await self._count(db, select(func.count()).select_from(WorkflowArtifactORM).where(WorkflowArtifactORM.run_id == workflow_run_id))
        return counts

    async def _get_run_by_id(self, db: AsyncSession, run_id: str) -> Optional[DinsarProductionRunORM]:
        result = await db.execute(select(DinsarProductionRunORM).where(DinsarProductionRunORM.run_id == run_id))
        return result.scalar_one_or_none()

    async def _count(self, db: AsyncSession, stmt: Any) -> int:
        return int((await db.execute(stmt)).scalar_one() or 0)

    def _cleanup_disk_targets(self, diagnosis: Dict[str, Any]) -> List[Dict[str, Any]]:
        targets: Dict[str, Dict[str, Any]] = {}
        for item in diagnosis.get("disk_paths") or []:
            path = _normalize_path_text(item.get("path"))
            if not path:
                continue
            payload = self._path_payload(path, item.get("kind") or "unknown")
            payload["allowed"] = self._is_allowed_delete_path(path)
            targets[path.lower()] = payload
        return list(targets.values())

    def _path_payload(self, path: str, kind: str) -> Dict[str, Any]:
        normalized = _normalize_path_text(path)
        exists = _path_exists(normalized)
        return {
            "path": normalized,
            "kind": kind,
            "exists": exists,
            "allowed": self._is_allowed_delete_path(normalized),
        }

    def _is_allowed_delete_path(self, path: str) -> bool:
        normalized = _normalize_path_text(path)
        if not normalized:
            return False
        roots = [
            settings.DINSAR_TASK_POOL_ROOT,
            settings.DINSAR_PRODUCT_DIR,
            os.path.join(settings.PROJECT_ROOT, "backend", "runtime", "dinsar_production"),
        ]
        full = os.path.abspath(normalized)
        for root in roots:
            root_text = _normalize_path_text(root)
            if not root_text:
                continue
            root_full = os.path.abspath(root_text)
            if full == root_full:
                return False
            try:
                if os.path.commonpath([full, root_full]) == root_full:
                    return True
            except ValueError:
                continue
        return False

    async def _delete_disk_targets(self, preview: Dict[str, Any], options: Dict[str, bool]) -> Dict[str, Any]:
        delete_production_dirs = bool(options.get("delete_production_dirs", True))
        delete_task_pool_dir = bool(options.get("delete_task_pool_dir", True))
        deleted: List[str] = []
        missing: List[str] = []
        skipped: List[str] = []
        failed: List[Dict[str, str]] = []
        for item in preview.get("disk_deletes") or []:
            kind = item.get("kind")
            path = _normalize_path_text(item.get("path"))
            if not item.get("allowed"):
                skipped.append(path)
                continue
            if kind == "task_pool" and not delete_task_pool_dir:
                skipped.append(path)
                continue
            if kind == "production_result" and not delete_production_dirs:
                skipped.append(path)
                continue
            if not _path_exists(path):
                missing.append(path)
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    Path(path).unlink()
                deleted.append(path)
            except OSError as exc:
                failed.append({"path": path, "error": str(exc)})
        return {
            "deleted": deleted,
            "missing": missing,
            "skipped": skipped,
            "failed": failed,
        }

    def _task_payload(self, task: SystemTaskORM) -> Dict[str, Any]:
        return {
            "id": task.id,
            "task_id": task.task_id,
            "task_type": task.task_type,
            "task_name": task.task_name,
            "status": task.status,
            "progress": task.progress,
            "message": _compact(task.message),
            "params": task.params,
            "created_at": _dt(task.created_at),
            "updated_at": _dt(task.updated_at),
            "started_at": _dt(task.started_at),
            "ended_at": _dt(task.ended_at),
        }

    def _job_payload(self, job: SystemJobORM) -> Dict[str, Any]:
        return {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "locked_by": job.locked_by,
            "locked_at": _dt(job.locked_at),
            "heartbeat_at": _dt(job.heartbeat_at),
            "started_at": _dt(job.started_at),
            "finished_at": _dt(job.finished_at),
            "last_error": _compact(job.last_error),
        }

    def _run_payload(self, run: DinsarProductionRunORM) -> Dict[str, Any]:
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "workflow_run_id": run.workflow_run_id,
            "engine_code": run.engine_code,
            "profile_code": run.profile_code,
            "mode": run.mode,
            "source_root": run.source_root,
            "publish_root_dir": run.publish_root_dir,
            "status": run.status,
            "total_items": run.total_items,
            "completed_items": run.completed_items,
            "failed_items": run.failed_items,
            "skipped_items": run.skipped_items,
            "latest_message": _compact(run.latest_message),
            "created_at": _dt(run.created_at),
            "updated_at": _dt(run.updated_at),
            "started_at": _dt(run.started_at),
            "ended_at": _dt(run.ended_at),
        }


ops_maintenance_service = OpsMaintenanceService()
