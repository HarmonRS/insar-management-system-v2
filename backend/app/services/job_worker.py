import asyncio
import os
import socket
import uuid
import time
from typing import Set

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import func

from ..models import SystemJobORM
from .job_queue_service import job_queue_service, JOB_STATUS_RETRY
from .job_handlers import get_job_handler
from .task_service import task_service
from .workflow_service import workflow_service
from .. import database
from ..config import settings
from ..models import SystemWorkerHeartbeatORM

IDL_JOB_TYPES = {"IDL_RUN_IMPORT", "IDL_RUN_DINSAR", "WATER_GEOCODE", "WATER_FLOOD"}


def _default_worker_id() -> str:
    host = socket.gethostname()
    pid = os.getpid()
    rand = uuid.uuid4().hex[:6]
    return f"{host}:{pid}:{rand}"


def _new_session():
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


async def _touch_worker(worker_id: str) -> None:
    host = socket.gethostname()
    pid = os.getpid()
    async with _new_session() as db:
        stmt = pg_insert(SystemWorkerHeartbeatORM).values(
            worker_id=worker_id,
            hostname=host,
            pid=pid,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["worker_id"],
            set_={
                "hostname": host,
                "pid": pid,
                "last_seen": func.now(),
            },
        )
        await db.execute(stmt)
        await db.commit()


async def _run_job(job: SystemJobORM) -> None:
    handler = get_job_handler(job.job_type)
    if handler is None:
        msg = f"No handler registered for job type: {job.job_type}"
        status = await job_queue_service.mark_failed(job, msg)
        if job.task_id:
            if status == JOB_STATUS_RETRY:
                await task_service.update_task(job.task_id, message=f"任务失败，稍后重试: {msg}")
            else:
                await task_service.update_task(job.task_id, status="FAILED", message=msg)
        if job.workflow_run_id and job.workflow_step_id and status != JOB_STATUS_RETRY:
            await workflow_service.mark_step_failed(
                job.workflow_run_id,
                job.workflow_step_id,
                msg,
            )
        return

    try:
        await handler(job)
        await job_queue_service.mark_completed(job.job_id)
        if job.workflow_run_id and job.workflow_step_id:
            await workflow_service.mark_step_completed(
                job.workflow_run_id,
                job.workflow_step_id,
            )
    except Exception as exc:
        err = f"{exc}"
        retry_delay_seconds = 30
        if (job.job_type or "").upper() in IDL_JOB_TYPES:
            retry_delay_seconds = int(settings.IDL_JOB_RETRY_DELAY_SECONDS)
        status = await job_queue_service.mark_failed(
            job,
            err,
            retry_delay_seconds=retry_delay_seconds,
        )
        if job.task_id:
            if status == JOB_STATUS_RETRY:
                await task_service.update_task(job.task_id, message=f"任务失败，稍后重试: {err}")
            else:
                current_task = await task_service.get_task(job.task_id)
                final_status = "FAILED"
                if current_task and current_task.status == "CANCELLED":
                    final_status = "CANCELLED"
                await task_service.update_task(job.task_id, status=final_status, message=err)
        if job.workflow_run_id and job.workflow_step_id and status != JOB_STATUS_RETRY:
            await workflow_service.mark_step_failed(
                job.workflow_run_id,
                job.workflow_step_id,
                err,
            )


async def run_worker_loop(
    poll_interval: float = 1.0,
    concurrency: int = 1,
    worker_id: str = "",
) -> None:
    """
    Main loop for DB-backed job worker.
    """
    if not worker_id:
        worker_id = _default_worker_id()

    concurrency = max(1, int(concurrency))
    sem = asyncio.Semaphore(concurrency)
    active: Set[asyncio.Task] = set()
    job_heartbeat_interval = float(settings.JOB_WORKER_JOB_HEARTBEAT_INTERVAL)
    stale_recover_interval = float(settings.JOB_WORKER_STALE_RECOVER_INTERVAL)
    stale_running_seconds = int(settings.JOB_WORKER_STALE_RUNNING_SECONDS)

    async def _job_heartbeat_loop(job_id: str) -> None:
        safe_interval = max(1.0, job_heartbeat_interval)
        while True:
            await asyncio.sleep(safe_interval)
            try:
                await job_queue_service.heartbeat(job_id)
            except Exception as exc:
                print(f"[WARN] heartbeat: {exc}")

    async def _wrap(job: SystemJobORM):
        heartbeat_task = asyncio.create_task(_job_heartbeat_loop(job.job_id))
        try:
            async with sem:
                await _run_job(job)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    last_heartbeat = 0.0
    last_recover = 0.0
    heartbeat_interval = float(settings.JOB_WORKER_HEARTBEAT_INTERVAL)

    while True:
        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_interval:
            try:
                await _touch_worker(worker_id)
            except Exception as exc:
                print(f"[WARN] worker cleanup: {exc}")
            last_heartbeat = now
        if now - last_recover >= max(5.0, stale_recover_interval):
            try:
                recovered = await job_queue_service.recover_stale_running_jobs(stale_running_seconds)
                if (recovered.get("recovered", 0) or recovered.get("failed", 0)):
                    print(
                        f"[*] Recovered stale jobs: retry={recovered.get('recovered', 0)} "
                        f"failed={recovered.get('failed', 0)}"
                    )
            except Exception as exc:
                print(f"[WARN] recover_stale: {exc}")
            last_recover = now

        done = {t for t in active if t.done()}
        for t in done:
            active.remove(t)
            try:
                t.result()
            except Exception as exc:
                print(f"[WARN] worker poll: {exc}")

        if len(active) < concurrency:
            job = await job_queue_service.claim_next_job(worker_id)
            if job:
                task = asyncio.create_task(_wrap(job))
                active.add(task)
                continue

        await asyncio.sleep(poll_interval)
