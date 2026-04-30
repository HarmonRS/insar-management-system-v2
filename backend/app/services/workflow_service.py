import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import database
from ..models import WorkflowRunORM, WorkflowStepORM
from .job_queue_service import job_queue_service


def _new_session() -> AsyncSession:
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


class WorkflowService:
    """
    Lightweight workflow orchestration service backed by DB.
    """

    @staticmethod
    def _collect_downstream_step_ids(
        target_step_id: str,
        steps: List[WorkflowStepORM],
    ) -> set[str]:
        reverse_graph: Dict[str, List[str]] = {}
        for step in steps:
            for dependency in step.depends_on or []:
                reverse_graph.setdefault(str(dependency), []).append(step.step_id)

        pending = [target_step_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            for child_step_id in reverse_graph.get(current, []):
                if child_step_id not in visited:
                    pending.append(child_step_id)
        return visited

    async def create_run(
        self,
        workflow_name: str,
        steps: List[Dict[str, Any]],
        params: Optional[Dict[str, Any]] = None,
        tags: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> str:
        gen_db = db is None
        if gen_db:
            db = _new_session()

        run_id = str(uuid.uuid4())
        try:
            run = WorkflowRunORM(
                run_id=run_id,
                workflow_name=workflow_name,
                status="RUNNING",
                params=params or {},
                tags=tags or {},
                created_by=created_by,
                started_at=datetime.utcnow(),
            )
            db.add(run)

            for step in steps:
                depends_on = step.get("depends_on") or []
                status = "READY" if not depends_on else "PENDING"
                step_params = {
                    "job_type": step.get("job_type"),
                    "payload": step.get("payload") or {},
                    "task_id": step.get("task_id"),
                    "optional": bool(step.get("optional", False)),
                    "max_attempts": step.get("max_attempts"),
                }
                db.add(
                    WorkflowStepORM(
                        run_id=run_id,
                        step_id=step.get("step_id"),
                        step_name=step.get("step_name") or step.get("step_id"),
                        status=status,
                        depends_on=depends_on,
                        params=step_params,
                    )
                )

            if gen_db:
                await db.commit()
            else:
                await db.flush()
        finally:
            if gen_db:
                await db.close()

        await self.enqueue_ready_steps(run_id, db=None if gen_db else db)
        return run_id

    async def enqueue_ready_steps(self, run_id: str, db: Optional[AsyncSession] = None) -> None:
        gen_db = db is None
        if gen_db:
            db = _new_session()

        try:
            result = await db.execute(
                select(WorkflowStepORM).where(
                    WorkflowStepORM.run_id == run_id,
                    WorkflowStepORM.status == "READY",
                )
            )
            ready_steps = result.scalars().all()

            for step in ready_steps:
                params = step.params or {}
                job_type = params.get("job_type")
                if not job_type:
                    continue
                payload = params.get("payload") or {}
                task_id = params.get("task_id")
                max_attempts = params.get("max_attempts")
                await job_queue_service.create_job(
                    job_type,
                    payload=payload,
                    workflow_run_id=run_id,
                    workflow_step_id=step.step_id,
                    task_id=task_id,
                    max_attempts=max_attempts if max_attempts is not None else 3,
                    db=db,
                )
                step.status = "RUNNING"
                step.started_at = datetime.utcnow()

            if gen_db:
                await db.commit()
            else:
                await db.flush()
        finally:
            if gen_db:
                await db.close()

    async def mark_step_completed(
        self,
        run_id: str,
        step_id: str,
        outputs: Optional[Dict[str, Any]] = None,
        db: Optional[AsyncSession] = None,
    ) -> None:
        gen_db = db is None
        if gen_db:
            db = _new_session()

        try:
            result = await db.execute(
                select(WorkflowStepORM).where(
                    WorkflowStepORM.run_id == run_id,
                    WorkflowStepORM.step_id == step_id,
                )
            )
            step = result.scalar_one_or_none()
            if not step:
                return

            step.status = "COMPLETED"
            step.ended_at = datetime.utcnow()
            if outputs:
                step.outputs = outputs

            await self._advance_ready_steps(run_id, db)
            await db.flush()
            await self.enqueue_ready_steps(run_id, db=db)

            if gen_db:
                await db.commit()
            else:
                await db.flush()
        finally:
            if gen_db:
                await db.close()

    async def mark_step_failed(
        self,
        run_id: str,
        step_id: str,
        error: str,
        db: Optional[AsyncSession] = None,
    ) -> None:
        gen_db = db is None
        if gen_db:
            db = _new_session()

        try:
            result = await db.execute(
                select(WorkflowStepORM).where(
                    WorkflowStepORM.run_id == run_id,
                    WorkflowStepORM.step_id == step_id,
                )
            )
            step = result.scalar_one_or_none()
            if not step:
                return

            step.status = "FAILED"
            step.error = error
            step.ended_at = datetime.utcnow()

            await db.execute(
                update(WorkflowRunORM)
                .where(WorkflowRunORM.run_id == run_id)
                .values(status="FAILED", ended_at=datetime.utcnow())
            )

            if gen_db:
                await db.commit()
            else:
                await db.flush()
        finally:
            if gen_db:
                await db.close()

    async def retry_step(
        self,
        run_id: str,
        step_id: str,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        gen_db = db is None
        if gen_db:
            db = _new_session()

        try:
            run_result = await db.execute(
                select(WorkflowRunORM).where(WorkflowRunORM.run_id == run_id)
            )
            run = run_result.scalar_one_or_none()
            if run is None:
                raise ValueError(f"Workflow run not found: {run_id}")

            steps_result = await db.execute(
                select(WorkflowStepORM)
                .where(WorkflowStepORM.run_id == run_id)
                .order_by(WorkflowStepORM.id.asc())
            )
            steps = steps_result.scalars().all()
            if not steps:
                raise ValueError(f"Workflow run has no steps: {run_id}")

            step_map = {step.step_id: step for step in steps}
            target = step_map.get(step_id)
            if target is None:
                raise ValueError(f"Workflow step not found: {step_id}")

            if any(step.status == "RUNNING" for step in steps):
                raise ValueError("Workflow still has running steps and cannot be retried.")

            retryable_statuses = {"FAILED", "COMPLETED", "CANCELLED", "SKIPPED"}
            if target.status not in retryable_statuses:
                raise ValueError(
                    f"Workflow step '{step_id}' is not retryable from status '{target.status}'."
                )

            reset_step_ids = self._collect_downstream_step_ids(step_id, steps)
            for step in steps:
                if step.step_id not in reset_step_ids:
                    continue
                step.status = "READY" if step.step_id == step_id else "PENDING"
                step.error = None
                step.outputs = None
                step.started_at = None
                step.ended_at = None

            run.status = "RUNNING"
            run.ended_at = None

            if gen_db:
                await db.commit()
            else:
                await db.flush()
        finally:
            if gen_db:
                await db.close()

        await self.enqueue_ready_steps(run_id, db=None if gen_db else db)
        return {
            "run_id": run_id,
            "step_id": step_id,
            "reset_steps": sorted(reset_step_ids),
        }

    async def _advance_ready_steps(self, run_id: str, db: AsyncSession) -> None:
        result = await db.execute(
            select(WorkflowStepORM).where(WorkflowStepORM.run_id == run_id)
        )
        steps = result.scalars().all()

        completed = {s.step_id for s in steps if s.status == "COMPLETED"}
        pending = [s for s in steps if s.status == "PENDING"]

        for step in pending:
            deps = step.depends_on or []
            if all(dep in completed for dep in deps):
                step.status = "READY"

        # If all steps are terminal, complete run.
        terminal = {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED"}
        if all(s.status in terminal for s in steps):
            status = "COMPLETED"
            if any(s.status == "FAILED" for s in steps):
                status = "FAILED"
            await db.execute(
                update(WorkflowRunORM)
                .where(WorkflowRunORM.run_id == run_id)
                .values(status=status, ended_at=datetime.utcnow())
            )


workflow_service = WorkflowService()
