from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from ..config import read_int_env
from ..database import get_db
from ..models import AuthUserORM, WorkflowRunORM, WorkflowStepORM
from ..services.workflow_service import workflow_service
from .dependencies import _require_admin

router = APIRouter()
WORKFLOW_MAX_STEPS = read_int_env(
    "WORKFLOW_MAX_STEPS",
    200,
    minimum=1,
    maximum=5000,
)
WORKFLOW_MAX_DEPENDS = read_int_env(
    "WORKFLOW_MAX_DEPENDS",
    32,
    minimum=0,
    maximum=500,
)
WORKFLOW_TEXT_MAX_LENGTH = read_int_env(
    "WORKFLOW_TEXT_MAX_LENGTH",
    128,
    minimum=16,
    maximum=1024,
)


class WorkflowStepCreate(BaseModel):
    step_id: str = Field(max_length=WORKFLOW_TEXT_MAX_LENGTH)
    step_name: Optional[str] = Field(default=None, max_length=WORKFLOW_TEXT_MAX_LENGTH)
    job_type: str = Field(max_length=WORKFLOW_TEXT_MAX_LENGTH)
    payload: Optional[dict] = None
    depends_on: List[str] = Field(default_factory=list)
    optional: bool = False
    task_id: Optional[str] = Field(default=None, max_length=WORKFLOW_TEXT_MAX_LENGTH)

    @field_validator("step_id", "job_type", mode="before")
    @classmethod
    def _normalize_required_text(cls, value):
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("Field must not be empty.")
        return normalized

    @field_validator("step_name", "task_id", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @field_validator("depends_on", mode="before")
    @classmethod
    def _validate_depends_on(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("depends_on must be a list.")
        if len(value) > WORKFLOW_MAX_DEPENDS:
            raise ValueError(f"depends_on exceeds max count ({WORKFLOW_MAX_DEPENDS}).")

        normalized: List[str] = []
        for raw in value:
            dep = str(raw or "").strip()
            if not dep:
                continue
            if len(dep) > WORKFLOW_TEXT_MAX_LENGTH:
                raise ValueError(
                    f"depends_on item is too long (max {WORKFLOW_TEXT_MAX_LENGTH} chars)."
                )
            if dep not in normalized:
                normalized.append(dep)
        return normalized


class WorkflowRunCreate(BaseModel):
    workflow_name: str = Field(max_length=WORKFLOW_TEXT_MAX_LENGTH)
    steps: List[WorkflowStepCreate]
    params: Optional[dict] = None
    tags: Optional[dict] = None
    created_by: Optional[str] = Field(default=None, max_length=WORKFLOW_TEXT_MAX_LENGTH)

    @field_validator("workflow_name", mode="before")
    @classmethod
    def _normalize_workflow_name(cls, value):
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("workflow_name must not be empty.")
        return normalized

    @field_validator("created_by", mode="before")
    @classmethod
    def _normalize_created_by(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_steps(self):
        if not self.steps:
            raise ValueError("steps must not be empty.")
        if len(self.steps) > WORKFLOW_MAX_STEPS:
            raise ValueError(f"steps exceeds max count ({WORKFLOW_MAX_STEPS}).")

        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id values must be unique within one workflow run.")

        step_id_set = set(step_ids)
        graph = {}
        for step in self.steps:
            graph[step.step_id] = list(step.depends_on or [])
            for dep in step.depends_on:
                if dep not in step_id_set:
                    raise ValueError(
                        f"Step '{step.step_id}' depends on unknown step_id '{dep}'."
                    )
                if dep == step.step_id:
                    raise ValueError(
                        f"Step '{step.step_id}' cannot depend on itself."
                    )

        # Detect dependency cycles early to avoid runs stuck in PENDING forever.
        visited = set()
        in_stack = set()

        def _dfs(node: str) -> None:
            if node in in_stack:
                raise ValueError(f"Workflow dependency cycle detected at step '{node}'.")
            if node in visited:
                return
            in_stack.add(node)
            for dep in graph.get(node, []):
                _dfs(dep)
            in_stack.remove(node)
            visited.add(node)

        for step_id in step_ids:
            _dfs(step_id)
        return self


@router.post("/workflow/runs")
async def create_workflow_run(request: WorkflowRunCreate, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    创建一个工作流实例并自动入队可执行步骤。
    """
    steps_payload = [
        {
            "step_id": s.step_id,
            "step_name": s.step_name or s.step_id,
            "job_type": s.job_type,
            "payload": s.payload or {},
            "depends_on": s.depends_on or [],
            "optional": s.optional,
            "task_id": s.task_id,
        }
        for s in request.steps
    ]
    try:
        run_id = await workflow_service.create_run(
            workflow_name=request.workflow_name,
            steps=steps_payload,
            params=request.params,
            tags=request.tags,
            created_by=request.created_by,
        )
        return {"run_id": run_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflow/runs/{run_id}")
async def get_workflow_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """
    获取工作流运行状态与步骤列表。
    """
    run_res = await db.execute(
        select(WorkflowRunORM).where(WorkflowRunORM.run_id == run_id)
    )
    run = run_res.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found.")

    steps_res = await db.execute(
        select(WorkflowStepORM).where(WorkflowStepORM.run_id == run_id).order_by(WorkflowStepORM.id)
    )
    steps = steps_res.scalars().all()

    return {
        "run": {
            "run_id": run.run_id,
            "workflow_name": run.workflow_name,
            "status": run.status,
            "params": run.params,
            "tags": run.tags,
            "created_by": run.created_by,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
        },
        "steps": [
            {
                "step_id": s.step_id,
                "step_name": s.step_name,
                "status": s.status,
                "depends_on": s.depends_on,
                "params": s.params,
                "outputs": s.outputs,
                "error": s.error,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
            }
            for s in steps
        ],
    }
