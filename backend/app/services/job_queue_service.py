import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import and_, or_, select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..config import read_int_env
from ..models import SystemJobORM


JOB_STATUS_READY = "READY"
JOB_STATUS_RUNNING = "RUNNING"
JOB_STATUS_COMPLETED = "COMPLETED"
JOB_STATUS_FAILED = "FAILED"
JOB_STATUS_RETRY = "RETRY"

JOB_QUEUE_MAX_PAYLOAD_BYTES = read_int_env(
    "JOB_QUEUE_MAX_PAYLOAD_BYTES",
    512 * 1024,
    minimum=1024,
    maximum=10 * 1024 * 1024,
)
JOB_QUEUE_MAX_ATTEMPTS = read_int_env(
    "JOB_QUEUE_MAX_ATTEMPTS",
    10,
    minimum=1,
    maximum=100,
)
JOB_QUEUE_MAX_PRIORITY_ABS = read_int_env(
    "JOB_QUEUE_MAX_PRIORITY_ABS",
    1000,
    minimum=1,
    maximum=100000,
)
JOB_QUEUE_MAX_ID_LENGTH = read_int_env(
    "JOB_QUEUE_MAX_ID_LENGTH",
    128,
    minimum=16,
    maximum=512,
)
JOB_QUEUE_MAX_TYPE_LENGTH = read_int_env(
    "JOB_QUEUE_MAX_TYPE_LENGTH",
    64,
    minimum=8,
    maximum=256,
)
JOB_QUEUE_STALE_RUNNING_SECONDS = read_int_env(
    "JOB_QUEUE_STALE_RUNNING_SECONDS",
    300,
    minimum=30,
    maximum=7 * 24 * 60 * 60,
)


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _normalize_job_type(job_type: str) -> str:
    normalized = str(job_type or "").strip().upper()
    if not normalized:
        raise ValueError("job_type must not be empty.")
    if len(normalized) > JOB_QUEUE_MAX_TYPE_LENGTH:
        raise ValueError(
            f"job_type is too long (max {JOB_QUEUE_MAX_TYPE_LENGTH} chars)."
        )
    return normalized


def _normalize_optional_text(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) > JOB_QUEUE_MAX_ID_LENGTH:
        raise ValueError(f"{field_name} is too long (max {JOB_QUEUE_MAX_ID_LENGTH} chars).")
    return normalized


def _normalize_priority(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 0
    return min(JOB_QUEUE_MAX_PRIORITY_ABS, max(-JOB_QUEUE_MAX_PRIORITY_ABS, parsed))


def _normalize_max_attempts(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 3
    return min(JOB_QUEUE_MAX_ATTEMPTS, max(1, parsed))


def _normalize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("Job payload must be a JSON object (dict).")

    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Job payload must be JSON-serializable.") from exc

    payload_bytes = len(serialized.encode("utf-8"))
    if payload_bytes > JOB_QUEUE_MAX_PAYLOAD_BYTES:
        raise ValueError(
            f"Job payload too large ({payload_bytes} bytes), "
            f"max allowed is {JOB_QUEUE_MAX_PAYLOAD_BYTES} bytes."
        )

    return payload


class JobQueueService:
    """
    DB-backed job queue service (PostgreSQL).
    """

    async def create_job(
        self,
        job_type: str,
        payload: Optional[Dict[str, Any]] = None,
        priority: int = 0,
        max_attempts: int = 3,
        workflow_run_id: Optional[str] = None,
        workflow_step_id: Optional[str] = None,
        task_id: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> str:
        gen_db = db is None
        if gen_db:
            db = _new_session()

        try:
            normalized_job_type = _normalize_job_type(job_type)
            normalized_payload = _normalize_payload(payload)
            normalized_priority = _normalize_priority(priority)
            normalized_max_attempts = _normalize_max_attempts(max_attempts)
            normalized_workflow_run_id = _normalize_optional_text(workflow_run_id, "workflow_run_id")
            normalized_workflow_step_id = _normalize_optional_text(workflow_step_id, "workflow_step_id")
            normalized_task_id = _normalize_optional_text(task_id, "task_id")

            job_id = str(uuid.uuid4())
            job = SystemJobORM(
                job_id=job_id,
                job_type=normalized_job_type,
                status=JOB_STATUS_READY,
                priority=normalized_priority,
                payload=normalized_payload,
                max_attempts=normalized_max_attempts,
                workflow_run_id=normalized_workflow_run_id,
                workflow_step_id=normalized_workflow_step_id,
                task_id=normalized_task_id,
            )
            db.add(job)
            if gen_db:
                await db.commit()
            else:
                await db.flush()
            return job_id
        finally:
            if gen_db:
                await db.close()

    async def claim_next_job(
        self,
        worker_id: str,
        lock_timeout_seconds: int = 1800,
        db: Optional[AsyncSession] = None,
    ) -> Optional[SystemJobORM]:
        gen_db = db is None
        if gen_db:
            db = _new_session()

        try:
            lock_timeout_seconds = int(lock_timeout_seconds)
            async with db.begin():
                result = await db.execute(
                    text(
                        """
                        SELECT id
                        FROM system_jobs
                        WHERE status IN ('READY', 'RETRY')
                          AND (next_run_at IS NULL OR next_run_at <= NOW())
                          AND (
                                locked_by IS NULL OR
                                locked_at IS NULL OR
                                locked_at < NOW() - make_interval(secs => :lock_timeout_seconds)
                              )
                        ORDER BY priority DESC, id ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                        """
                    ),
                    {"lock_timeout_seconds": lock_timeout_seconds},
                )
                row = result.first()
                if not row:
                    return None

                job_pk = row[0]
                await db.execute(
                    update(SystemJobORM)
                    .where(SystemJobORM.id == job_pk)
                    .values(
                        status=JOB_STATUS_RUNNING,
                        locked_by=worker_id,
                        locked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        heartbeat_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    )
                )

            job_result = await db.execute(
                select(SystemJobORM).where(SystemJobORM.id == job_pk)
            )
            return job_result.scalar_one_or_none()
        finally:
            if gen_db:
                await db.close()

    async def heartbeat(self, job_id: str, db: Optional[AsyncSession] = None) -> None:
        gen_db = db is None
        if gen_db:
            db = _new_session()
        try:
            await db.execute(
                update(SystemJobORM)
                .where(SystemJobORM.job_id == job_id)
                .values(heartbeat_at=datetime.now(timezone.utc).replace(tzinfo=None))
            )
            await db.commit()
        finally:
            if gen_db:
                await db.close()

    async def recover_stale_running_jobs(
        self,
        stale_seconds: int = JOB_QUEUE_STALE_RUNNING_SECONDS,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, int]:
        gen_db = db is None
        if gen_db:
            db = _new_session()
        try:
            safe_stale_seconds = max(30, int(stale_seconds))
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cutoff = now - timedelta(seconds=safe_stale_seconds)

            result = await db.execute(
                select(SystemJobORM).where(
                    SystemJobORM.status == JOB_STATUS_RUNNING,
                    or_(
                        SystemJobORM.heartbeat_at < cutoff,
                        and_(
                            SystemJobORM.heartbeat_at.is_(None),
                            SystemJobORM.locked_at < cutoff,
                        ),
                        and_(
                            SystemJobORM.heartbeat_at.is_(None),
                            SystemJobORM.locked_at.is_(None),
                        ),
                    ),
                )
            )
            stale_jobs = result.scalars().all()
            if not stale_jobs:
                return {"recovered": 0, "failed": 0}

            recovered = 0
            failed = 0
            for job in stale_jobs:
                attempts = int(job.attempts or 0) + 1
                if attempts < int(job.max_attempts or 1):
                    status = JOB_STATUS_RETRY
                    next_run_at = now + timedelta(seconds=5)
                    recovered += 1
                    finished_at = None
                else:
                    status = JOB_STATUS_FAILED
                    next_run_at = None
                    failed += 1
                    finished_at = now

                await db.execute(
                    update(SystemJobORM)
                    .where(SystemJobORM.id == job.id)
                    .values(
                        status=status,
                        attempts=attempts,
                        next_run_at=next_run_at,
                        last_error=f"Recovered stale RUNNING job after {safe_stale_seconds}s timeout.",
                        finished_at=finished_at,
                        locked_by=None,
                        locked_at=None,
                        heartbeat_at=None,
                    )
                )
            await db.commit()
            return {"recovered": recovered, "failed": failed}
        finally:
            if gen_db:
                await db.close()

    async def mark_completed(
        self,
        job_id: str,
        db: Optional[AsyncSession] = None,
    ) -> None:
        gen_db = db is None
        if gen_db:
            db = _new_session()
        try:
            await db.execute(
                update(SystemJobORM)
                .where(SystemJobORM.job_id == job_id)
                .values(
                    status=JOB_STATUS_COMPLETED,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    last_error=None,
                    locked_by=None,
                    locked_at=None,
                    heartbeat_at=None,
                )
            )
            await db.commit()
        finally:
            if gen_db:
                await db.close()

    async def mark_failed(
        self,
        job: SystemJobORM,
        error_message: str,
        retry_delay_seconds: int = 30,
        db: Optional[AsyncSession] = None,
    ) -> str:
        gen_db = db is None
        if gen_db:
            db = _new_session()
        try:
            attempts = (job.attempts or 0) + 1
            status = JOB_STATUS_FAILED
            next_run_at = None
            if attempts < (job.max_attempts or 0):
                status = JOB_STATUS_RETRY
                backoff = retry_delay_seconds * (2 ** max(attempts - 1, 0))
                next_run_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=backoff)

            await db.execute(
                update(SystemJobORM)
                .where(SystemJobORM.job_id == job.job_id)
                .values(
                    status=status,
                    attempts=attempts,
                    next_run_at=next_run_at,
                    last_error=error_message,
                    finished_at=datetime.now(timezone.utc).replace(tzinfo=None) if status == JOB_STATUS_FAILED else None,
                    locked_by=None,
                    locked_at=None,
                    heartbeat_at=None,
                )
            )
            await db.commit()
            return status
        finally:
            if gen_db:
                await db.close()


job_queue_service = JobQueueService()
