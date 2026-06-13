from __future__ import annotations

import asyncio
import mimetypes
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import String, cast, or_, select

from .. import database
from ..config import settings
from ..models import AuthUserORM, SystemJobORM, SystemTaskORM, TaskLogORM
from ..services.job_queue_service import job_queue_service
from ..services.landsar_sbas_service import landsar_sbas_service
from ..services.sbas_insar_production_service import sbas_insar_production_service
from ..services.task_service import task_service
from .dependencies import _require_admin


router = APIRouter(prefix="/sbas-insar-production", tags=["sbas-insar-production"])


def _new_session():
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


def _dt(value):
    return value.isoformat() if value else None


def _task_payload_matches(run_id: str):
    return or_(
        cast(SystemTaskORM.params, String).ilike(f"%{run_id}%"),
        SystemTaskORM.task_name.ilike(f"%{run_id}%"),
    )


def _job_payload_matches(run_id: str, task_ids: list[str]):
    conditions = [
        cast(SystemJobORM.payload, String).ilike(f"%{run_id}%"),
        SystemJobORM.workflow_run_id == run_id,
    ]
    if task_ids:
        conditions.append(SystemJobORM.task_id.in_(task_ids))
    return or_(*conditions)


async def _load_run_background_activity(run_id: str) -> dict:
    try:
        async with _new_session() as db:
            task_result = await db.execute(
                select(SystemTaskORM)
                .where(_task_payload_matches(run_id))
                .order_by(SystemTaskORM.created_at.desc(), SystemTaskORM.id.desc())
                .limit(10)
            )
            tasks = list(task_result.scalars().all())
            task_ids = [
                str(task.task_id or "").strip()
                for task in tasks
                if str(task.task_id or "").strip()
            ]
            job_result = await db.execute(
                select(SystemJobORM)
                .where(_job_payload_matches(run_id, task_ids))
                .order_by(SystemJobORM.created_at.desc(), SystemJobORM.id.desc())
                .limit(10)
            )
            jobs = list(job_result.scalars().all())
            if not task_ids:
                task_ids = sorted({
                    str(job.task_id or "").strip()
                    for job in jobs
                    if str(job.task_id or "").strip()
                })
            logs = []
            if task_ids:
                log_result = await db.execute(
                    select(TaskLogORM)
                    .where(TaskLogORM.task_id.in_(task_ids))
                    .order_by(TaskLogORM.timestamp.desc(), TaskLogORM.id.desc())
                    .limit(20)
                )
                logs = list(log_result.scalars().all())
    except Exception as exc:
        return {
            "schema": "insar.sbas-background-activity/v1",
            "error": str(exc),
            "tasks": [],
            "jobs": [],
            "task_logs": [],
            "active": False,
        }

    active_task_statuses = {"PENDING", "RUNNING"}
    active_job_statuses = {"READY", "PENDING", "RUNNING", "RETRY"}
    return {
        "schema": "insar.sbas-background-activity/v1",
        "tasks": [
            {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "task_name": task.task_name,
                "status": task.status,
                "progress": task.progress,
                "message": task.message,
                "created_at": _dt(task.created_at),
                "updated_at": _dt(task.updated_at),
                "started_at": _dt(task.started_at),
                "ended_at": _dt(task.ended_at),
            }
            for task in tasks
        ],
        "jobs": [
            {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": job.status,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "locked_by": job.locked_by,
                "locked_at": _dt(job.locked_at),
                "heartbeat_at": _dt(job.heartbeat_at),
                "created_at": _dt(job.created_at),
                "updated_at": _dt(job.updated_at),
                "started_at": _dt(job.started_at),
                "finished_at": _dt(job.finished_at),
                "last_error": job.last_error,
                "task_id": job.task_id,
            }
            for job in jobs
        ],
        "task_logs": [
            {
                "task_id": log.task_id,
                "level": log.log_level,
                "message": log.message,
                "timestamp": _dt(log.timestamp),
            }
            for log in logs
        ],
        "active": any(str(task.status or "").upper() in active_task_statuses for task in tasks)
        or any(str(job.status or "").upper() in active_job_statuses for job in jobs),
    }


def _load_wsl_process_summary(run_id: str) -> dict:
    distro = str(settings.GAMMA_SBAS_WSL_DISTRO or settings.WSL_DISTRO or "").strip()
    command = [
        "wsl.exe",
        *([] if not distro else ["-d", distro]),
        "--",
        "bash",
        "-lc",
        (
            "ps -eo pid,etime,stat,args --no-headers | "
            "grep -E 'gamma|SLC_interp|base_calc|mk_diff|mk_unw|ts_rate| mb |python|bash' | "
            "grep -v grep | head -20"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return {"available": False, "error": "wsl.exe not found", "processes": []}
    except Exception as exc:
        return {"available": False, "error": str(exc), "processes": []}

    processes = []
    for line in (completed.stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        parts = text.split(None, 3)
        processes.append(
            {
                "pid": parts[0] if len(parts) > 0 else "",
                "etime": parts[1] if len(parts) > 1 else "",
                "stat": parts[2] if len(parts) > 2 else "",
                "command": parts[3] if len(parts) > 3 else text,
                "matches_run": run_id in text,
            }
        )
    return {
        "available": completed.returncode in {0, 1},
        "returncode": completed.returncode,
        "distro": distro or None,
        "processes": processes,
        "stderr_tail": (completed.stderr or "")[-1200:],
    }


class SbasAoiBbox(BaseModel):
    min_lon: float = Field(ge=-180, le=180)
    min_lat: float = Field(ge=-90, le=90)
    max_lon: float = Field(ge=-180, le=180)
    max_lat: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def _validate_order(self):
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("aoi_bbox min values must be smaller than max values")
        return self


class SbasStackDiscoverRequest(BaseModel):
    sensor_family: str = Field(default="LT1", pattern="^(LT1|S1)$")
    source_roots: list[str] | None = None
    orbit_roots: list[str] | None = None
    min_scenes: int = Field(default=3, ge=2, le=100)
    require_orbits: bool = True
    include_scenes: bool = False
    limit: int = Field(default=30, ge=0, le=500)
    platform: str | None = Field(default=None, max_length=16)
    relative_orbit: str | None = Field(default=None, max_length=32)
    orbit_direction: str | None = Field(default=None, max_length=32)
    admin_region: str | None = Field(default=None, max_length=120)
    discovery_mode: str = Field(default="strict", pattern="^(strict|aoi)$")
    aoi_bbox: SbasAoiBbox | None = None
    min_aoi_coverage_ratio: float = Field(default=0.01, ge=0, le=1)
    min_common_overlap_ratio: float = Field(
        default_factory=lambda: float(settings.GAMMA_SBAS_MIN_COMMON_OVERLAP_RATIO or 0.30),
        ge=0,
        le=1,
    )

    @field_validator("source_roots", "orbit_roots", mode="before")
    @classmethod
    def _normalize_roots(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            items = [value]
        else:
            items = list(value)
        cleaned = [str(item or "").strip() for item in items if str(item or "").strip()]
        return cleaned or None

    @field_validator("sensor_family", mode="before")
    @classmethod
    def _normalize_sensor_family(cls, value):
        text = str(value or "LT1").strip().upper()
        if text in {"SENTINEL1", "SENTINEL-1"}:
            return "S1"
        return text or "LT1"

    @field_validator("platform", "relative_orbit", "orbit_direction", "admin_region", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("discovery_mode", mode="before")
    @classmethod
    def _normalize_discovery_mode(cls, value):
        text = str(value or "strict").strip().lower()
        return text or "strict"


class SbasMonitorPoint(BaseModel):
    point_id: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=120)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class SbasRunSubmitRequest(SbasStackDiscoverRequest):
    run_label: str | None = Field(default=None, max_length=120)
    dry_run: bool = True
    monitor_point_strategy: str = Field(default="auto_representative_points", max_length=64)
    monitor_points: list[SbasMonitorPoint] | None = None


class SbasBaselineAuditRequest(BaseModel):
    execute: bool = True
    rlks: int = Field(default=8, ge=1, le=64)
    azlks: int = Field(default=8, ge=1, le=64)
    max_delta_n: int = Field(default=1, ge=1, le=100)
    timeout_seconds: int = Field(default=21600, ge=60, le=86400)


class SbasItabDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reviewer: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class SbasCoregistrationRequest(BaseModel):
    execute: bool = False
    rlks: int = Field(default=8, ge=1, le=64)
    azlks: int = Field(default=8, ge=1, le=64)


class SbasCoregistrationJobRequest(BaseModel):
    rlks: int = Field(default=8, ge=1, le=64)
    azlks: int = Field(default=8, ge=1, le=64)
    timeout_seconds: int = Field(default=43200, ge=60, le=172800)


class SbasRdcDemRequest(BaseModel):
    execute: bool = False
    rlks: int = Field(default=8, ge=1, le=64)


class SbasRdcDemJobRequest(BaseModel):
    rlks: int = Field(default=8, ge=1, le=64)
    timeout_seconds: int = Field(default=43200, ge=60, le=172800)


class SbasInterferogramsRequest(BaseModel):
    execute: bool = False
    rlks: int = Field(default=8, ge=1, le=64)
    azlks: int = Field(default=8, ge=1, le=64)
    unwrap_threshold: float = Field(default=0.20, ge=0.01, le=0.95)


class SbasInterferogramsJobRequest(BaseModel):
    rlks: int = Field(default=8, ge=1, le=64)
    azlks: int = Field(default=8, ge=1, le=64)
    unwrap_threshold: float = Field(default=0.20, ge=0.01, le=0.95)
    timeout_seconds: int = Field(default=43200, ge=60, le=172800)


class SbasIptaTimeseriesRequest(BaseModel):
    execute: bool = False
    rlks: int = Field(default=8, ge=1, le=64)
    reference_window: int = Field(default=16, ge=1, le=256)
    mb_mode: int = Field(default=0, ge=0, le=2)


class SbasIptaTimeseriesJobRequest(BaseModel):
    rlks: int = Field(default=8, ge=1, le=64)
    reference_window: int = Field(default=16, ge=1, le=256)
    mb_mode: int = Field(default=0, ge=0, le=2)
    timeout_seconds: int = Field(default=43200, ge=60, le=172800)


class SbasWorkflowPrepareRequest(BaseModel):
    force: bool = False
    rlks: int = Field(default=8, ge=1, le=64)
    azlks: int = Field(default=8, ge=1, le=64)
    reference_window: int = Field(default=16, ge=1, le=256)
    mb_mode: int = Field(default=0, ge=0, le=2)


class SbasWorkflowJobRequest(SbasWorkflowPrepareRequest):
    from_step: str | None = Field(default=None, max_length=64)
    to_step: str | None = Field(default=None, max_length=64)
    only_steps: list[str] | None = None
    timeout_seconds: int = Field(default=172800, ge=60, le=604800)


class LandsarSbasAutoWorkflowRequest(SbasStackDiscoverRequest):
    sensor_family: str = Field(default="LT1", pattern="^LT1$")
    require_orbits: bool = False
    run_label: str | None = Field(default=None, max_length=160)
    dem_path: str | None = Field(default=None, max_length=1024)
    timeout_seconds: int | None = Field(default=None, ge=60, le=604800)
    import_timeout_seconds: int | None = Field(default=None, ge=60, le=604800)
    workflow_timeout_seconds: int | None = Field(default=None, ge=60, le=604800)
    params: dict[str, object] = Field(default_factory=dict)


@router.get("/capabilities")
async def get_sbas_insar_capabilities():
    capabilities = sbas_insar_production_service.get_capabilities()
    capabilities["processors"] = [
        {
            "processor_code": capabilities.get("processor_code"),
            "profile_code": "lt1_gamma_sbas",
            "engine_code": capabilities.get("engine_code"),
            "label": "Gamma / IPTA SBAS",
            "enabled": bool((capabilities.get("runtime") or {}).get("enabled", True)),
        },
        {
            **landsar_sbas_service.get_capabilities(),
            "label": "LandSAR SBAS",
        },
    ]
    return capabilities


@router.get("/landsar/capabilities")
async def get_landsar_sbas_capabilities():
    return landsar_sbas_service.get_capabilities()


@router.post("/landsar/workflows/auto", status_code=202)
async def submit_landsar_sbas_auto_workflow(request: LandsarSbasAutoWorkflowRequest):
    try:
        from ..services.job_handlers import JOB_TYPE_SBAS_LANDSAR_WORKFLOW

        selection_request = {
            "run_label": request.run_label,
            "source_roots": request.source_roots,
            "orbit_roots": request.orbit_roots,
            "min_scenes": request.min_scenes,
            "discovery_mode": request.discovery_mode,
            "admin_region": request.admin_region,
            "aoi_bbox": request.aoi_bbox.model_dump() if request.aoi_bbox else None,
            "min_aoi_coverage_ratio": request.min_aoi_coverage_ratio,
            "min_common_overlap_ratio": request.min_common_overlap_ratio,
            "limit": request.limit,
            "dem_path": request.dem_path,
            "timeout_seconds": request.timeout_seconds,
            "import_timeout_seconds": request.import_timeout_seconds,
            "params": dict(request.params or {}),
        }
        payload = {
            "auto_select": True,
            "selection_request": selection_request,
            "timeout_seconds": request.workflow_timeout_seconds or request.timeout_seconds,
        }
        task_id = await task_service.create_task(
            task_type=JOB_TYPE_SBAS_LANDSAR_WORKFLOW,
            task_name="LandSAR SBAS Auto Workflow",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=JOB_TYPE_SBAS_LANDSAR_WORKFLOW,
            payload=payload,
            task_id=task_id,
            max_attempts=1,
        )
        return {
            "message": "LandSAR SBAS auto workflow job queued. Stack selection and input import will run in the background.",
            "run_id": None,
            "selection_pending": True,
            "task_id": task_id,
            "job_id": job_id,
            "job_type": JOB_TYPE_SBAS_LANDSAR_WORKFLOW,
            "status": "QUEUED",
        }
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "already running" in message.lower() or "conflict" in message.lower() or "任务冲突" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/landsar/runs")
async def list_landsar_sbas_runs():
    return await asyncio.to_thread(landsar_sbas_service.list_runs)


@router.get("/landsar/runs/{run_id}")
async def get_landsar_sbas_run(run_id: str):
    try:
        return await asyncio.to_thread(landsar_sbas_service.get_run_detail, run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/landsar/runs/{run_id}/artifacts/{relative_path:path}")
async def get_landsar_sbas_run_artifact(run_id: str, relative_path: str):
    try:
        artifact_path = await asyncio.to_thread(landsar_sbas_service.resolve_artifact, run_id, relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    media_type = mimetypes.guess_type(str(artifact_path))[0] or "application/octet-stream"
    return FileResponse(
        artifact_path,
        media_type=media_type,
        filename=artifact_path.name,
    )


@router.post("/stacks/discover")
async def discover_sbas_insar_stacks(request: SbasStackDiscoverRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.discover_stacks,
            sensor_family=request.sensor_family,
            source_roots=request.source_roots,
            orbit_roots=request.orbit_roots,
            min_scenes=request.min_scenes,
            require_orbits=request.require_orbits,
            include_scenes=request.include_scenes,
            limit=request.limit,
            platform=request.platform,
            relative_orbit=request.relative_orbit,
            orbit_direction=request.orbit_direction,
            admin_region=request.admin_region,
            discovery_mode=request.discovery_mode,
            aoi_bbox=request.aoi_bbox.model_dump() if request.aoi_bbox else None,
            min_aoi_coverage_ratio=request.min_aoi_coverage_ratio,
            min_common_overlap_ratio=request.min_common_overlap_ratio,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stacks/{stack_id}/audit")
async def audit_sbas_insar_stack(stack_id: str, request: SbasStackDiscoverRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.audit_stack,
            stack_id,
            sensor_family=request.sensor_family,
            source_roots=request.source_roots,
            orbit_roots=request.orbit_roots,
            min_scenes=request.min_scenes,
            require_orbits=request.require_orbits,
            discovery_mode=request.discovery_mode,
            admin_region=request.admin_region,
            aoi_bbox=request.aoi_bbox.model_dump() if request.aoi_bbox else None,
            min_aoi_coverage_ratio=request.min_aoi_coverage_ratio,
            min_common_overlap_ratio=request.min_common_overlap_ratio,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stacks/{stack_id}/runs", status_code=202)
async def submit_sbas_insar_run(stack_id: str, request: SbasRunSubmitRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.create_run,
            stack_id,
            sensor_family=request.sensor_family,
            run_label=request.run_label,
            source_roots=request.source_roots,
            orbit_roots=request.orbit_roots,
            min_scenes=request.min_scenes,
            require_orbits=request.require_orbits,
            discovery_mode=request.discovery_mode,
            admin_region=request.admin_region,
            aoi_bbox=request.aoi_bbox.model_dump() if request.aoi_bbox else None,
            min_aoi_coverage_ratio=request.min_aoi_coverage_ratio,
            min_common_overlap_ratio=request.min_common_overlap_ratio,
            monitor_points=[
                point.model_dump(exclude_none=True)
                for point in (request.monitor_points or [])
            ],
            monitor_point_strategy=request.monitor_point_strategy,
            dry_run=request.dry_run,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs")
async def list_sbas_insar_runs():
    return await asyncio.to_thread(sbas_insar_production_service.list_runs)


@router.get("/runs/{run_id}")
async def get_sbas_insar_run(run_id: str):
    try:
        detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
        background_activity, wsl_processes = await asyncio.gather(
            _load_run_background_activity(run_id),
            asyncio.to_thread(_load_wsl_process_summary, run_id),
        )
        runtime_status = dict(detail.get("runtime_status") or {})
        runtime_status["background_activity"] = background_activity
        runtime_status["wsl_processes"] = wsl_processes
        runtime_status["active"] = bool(
            runtime_status.get("active")
            or background_activity.get("active")
            or any(item.get("matches_run") for item in wsl_processes.get("processes") or [])
        )
        detail["runtime_status"] = runtime_status
        return detail
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/runs/{run_id}")
async def delete_sbas_insar_run(
    run_id: str,
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    try:
        async with _new_session() as db:
            result = await sbas_insar_production_service.delete_run_record(run_id, db=db)
            try:
                from ..services.sbas_insar_catalog_service import sbas_insar_catalog_service

                catalog_result = await sbas_insar_catalog_service.rebuild_catalog(db, full_rebuild=True)
            except Exception as catalog_exc:
                catalog_result = {
                    "status": "WARN",
                    "message": f"SBAS catalog rebuild failed after run deletion: {catalog_exc}",
                }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "active task/job" in message or "running" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    return {
        **result,
        "catalog": catalog_result,
    }


@router.post("/runs/{run_id}/workflow", status_code=202)
async def prepare_sbas_insar_workflow(run_id: str, request: SbasWorkflowPrepareRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.prepare_workflow,
            run_id,
            force=request.force,
            rlks=request.rlks,
            azlks=request.azlks,
            reference_window=request.reference_window,
            mb_mode=request.mb_mode,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/workflow/jobs", status_code=202)
async def submit_sbas_insar_workflow_job(run_id: str, request: SbasWorkflowJobRequest):
    try:
        await asyncio.to_thread(
            sbas_insar_production_service.prepare_workflow,
            run_id,
            force=request.force,
            rlks=request.rlks,
            azlks=request.azlks,
            reference_window=request.reference_window,
            mb_mode=request.mb_mode,
        )
        from ..services.job_handlers import JOB_TYPE_SBAS_GAMMA_WORKFLOW

        payload = {
            "run_id": run_id,
            "force": request.force,
            "rlks": request.rlks,
            "azlks": request.azlks,
            "reference_window": request.reference_window,
            "mb_mode": request.mb_mode,
            "from_step": request.from_step,
            "to_step": request.to_step,
            "only_steps": request.only_steps or [],
            "timeout_seconds": request.timeout_seconds,
        }
        task_id = await task_service.create_task(
            task_type=JOB_TYPE_SBAS_GAMMA_WORKFLOW,
            task_name=f"Gamma SBAS Workflow {run_id}",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=JOB_TYPE_SBAS_GAMMA_WORKFLOW,
            payload=payload,
            task_id=task_id,
            max_attempts=1,
        )
        return {
            "message": "Gamma SBAS workflow job queued.",
            "run_id": run_id,
            "task_id": task_id,
            "job_id": job_id,
            "job_type": JOB_TYPE_SBAS_GAMMA_WORKFLOW,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "already running" in message.lower() or "conflict" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/runs/{run_id}/baseline-audit", status_code=202)
async def run_sbas_insar_baseline_audit(run_id: str, request: SbasBaselineAuditRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.run_baseline_audit,
            run_id,
            execute=request.execute,
            rlks=request.rlks,
            azlks=request.azlks,
            max_delta_n=request.max_delta_n,
            timeout_seconds=request.timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"baseline audit timed out after {exc.timeout}s") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/itab-decision")
async def decide_sbas_insar_itab(run_id: str, request: SbasItabDecisionRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.decide_itab,
            run_id,
            decision=request.decision,
            reviewer=request.reviewer,
            note=request.note,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/coregistration", status_code=202)
async def prepare_sbas_insar_coregistration(run_id: str, request: SbasCoregistrationRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.prepare_coregistration,
            run_id,
            execute=request.execute,
            rlks=request.rlks,
            azlks=request.azlks,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/coregistration/jobs", status_code=202)
async def submit_sbas_insar_coregistration_job(run_id: str, request: SbasCoregistrationJobRequest):
    try:
        run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
        status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status in {"ITAB_APPROVED", "COREGISTRATION_FAILED"}:
            await asyncio.to_thread(
                sbas_insar_production_service.prepare_coregistration,
                run_id,
                execute=False,
                rlks=request.rlks,
                azlks=request.azlks,
            )
            run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
            status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status not in {"COREGISTRATION_SCRIPT_READY", "COREGISTRATION_RUNNING"}:
            raise ValueError(f"run status does not allow coregistration job submission: {status}")
        if status == "COREGISTRATION_RUNNING":
            raise ValueError("coregistration is already running for this run")

        from ..services.job_handlers import JOB_TYPE_SBAS_COREGISTRATION

        payload = {
            "run_id": run_id,
            "rlks": request.rlks,
            "azlks": request.azlks,
            "timeout_seconds": request.timeout_seconds,
        }
        task_id = await task_service.create_task(
            task_type=JOB_TYPE_SBAS_COREGISTRATION,
            task_name=f"SBAS-InSAR 共参考配准: {run_id}",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=JOB_TYPE_SBAS_COREGISTRATION,
            payload=payload,
            task_id=task_id,
            max_attempts=1,
        )
        return {
            "message": "SBAS-InSAR coregistration job queued.",
            "run_id": run_id,
            "task_id": task_id,
            "job_id": job_id,
            "job_type": JOB_TYPE_SBAS_COREGISTRATION,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "冲突" in message or "conflict" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/runs/{run_id}/rdc-dem", status_code=202)
async def prepare_sbas_insar_rdc_dem(run_id: str, request: SbasRdcDemRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.prepare_rdc_dem,
            run_id,
            execute=request.execute,
            rlks=request.rlks,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/rdc-dem/jobs", status_code=202)
async def submit_sbas_insar_rdc_dem_job(run_id: str, request: SbasRdcDemJobRequest):
    try:
        run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
        status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status in {"COREGISTRATION_READY", "RDC_DEM_FAILED"}:
            await asyncio.to_thread(
                sbas_insar_production_service.prepare_rdc_dem,
                run_id,
                execute=False,
                rlks=request.rlks,
            )
            run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
            status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status not in {"RDC_DEM_SCRIPT_READY", "RDC_DEM_RUNNING"}:
            raise ValueError(f"run status does not allow RDC DEM job submission: {status}")
        if status == "RDC_DEM_RUNNING":
            raise ValueError("RDC DEM is already running for this run")

        from ..services.job_handlers import JOB_TYPE_SBAS_RDC_DEM

        payload = {
            "run_id": run_id,
            "rlks": request.rlks,
            "timeout_seconds": request.timeout_seconds,
        }
        task_id = await task_service.create_task(
            task_type=JOB_TYPE_SBAS_RDC_DEM,
            task_name=f"SBAS-InSAR RDC DEM {run_id}",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=JOB_TYPE_SBAS_RDC_DEM,
            payload=payload,
            task_id=task_id,
            max_attempts=1,
        )
        return {
            "message": "SBAS-InSAR RDC DEM job queued.",
            "run_id": run_id,
            "task_id": task_id,
            "job_id": job_id,
            "job_type": JOB_TYPE_SBAS_RDC_DEM,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "already running" in message.lower() or "conflict" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/runs/{run_id}/interferograms", status_code=202)
async def prepare_sbas_insar_interferograms(run_id: str, request: SbasInterferogramsRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.prepare_interferograms,
            run_id,
            execute=request.execute,
            rlks=request.rlks,
            azlks=request.azlks,
            unwrap_threshold=request.unwrap_threshold,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/interferograms/jobs", status_code=202)
async def submit_sbas_insar_interferograms_job(run_id: str, request: SbasInterferogramsJobRequest):
    try:
        run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
        status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status in {"RDC_DEM_READY", "INTERFEROGRAMS_FAILED"}:
            await asyncio.to_thread(
                sbas_insar_production_service.prepare_interferograms,
                run_id,
                execute=False,
                rlks=request.rlks,
                azlks=request.azlks,
                unwrap_threshold=request.unwrap_threshold,
            )
            run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
            status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status not in {"INTERFEROGRAMS_SCRIPT_READY", "INTERFEROGRAMS_RUNNING"}:
            raise ValueError(f"run status does not allow interferogram job submission: {status}")
        if status == "INTERFEROGRAMS_RUNNING":
            raise ValueError("interferograms are already running for this run")

        from ..services.job_handlers import JOB_TYPE_SBAS_INTERFEROGRAMS

        payload = {
            "run_id": run_id,
            "rlks": request.rlks,
            "azlks": request.azlks,
            "unwrap_threshold": request.unwrap_threshold,
            "timeout_seconds": request.timeout_seconds,
        }
        task_id = await task_service.create_task(
            task_type=JOB_TYPE_SBAS_INTERFEROGRAMS,
            task_name=f"SBAS-InSAR Interferograms {run_id}",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=JOB_TYPE_SBAS_INTERFEROGRAMS,
            payload=payload,
            task_id=task_id,
            max_attempts=1,
        )
        return {
            "message": "SBAS-InSAR interferogram job queued.",
            "run_id": run_id,
            "task_id": task_id,
            "job_id": job_id,
            "job_type": JOB_TYPE_SBAS_INTERFEROGRAMS,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "already running" in message.lower() or "conflict" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/runs/{run_id}/ipta-timeseries", status_code=202)
async def prepare_sbas_insar_ipta_timeseries(run_id: str, request: SbasIptaTimeseriesRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.prepare_ipta_timeseries,
            run_id,
            execute=request.execute,
            rlks=request.rlks,
            reference_window=request.reference_window,
            mb_mode=request.mb_mode,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/ipta-timeseries/jobs", status_code=202)
async def submit_sbas_insar_ipta_timeseries_job(run_id: str, request: SbasIptaTimeseriesJobRequest):
    try:
        run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
        status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status in {"INTERFEROGRAMS_READY", "IPTA_TIMESERIES_FAILED"}:
            await asyncio.to_thread(
                sbas_insar_production_service.prepare_ipta_timeseries,
                run_id,
                execute=False,
                rlks=request.rlks,
                reference_window=request.reference_window,
                mb_mode=request.mb_mode,
            )
            run_detail = await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
            status = str((run_detail.get("run") or {}).get("status") or "").strip()
        if status not in {"IPTA_TIMESERIES_SCRIPT_READY", "IPTA_TIMESERIES_RUNNING"}:
            raise ValueError(f"run status does not allow IPTA time-series job submission: {status}")
        if status == "IPTA_TIMESERIES_RUNNING":
            raise ValueError("IPTA time-series is already running for this run")

        from ..services.job_handlers import JOB_TYPE_SBAS_IPTA_TIMESERIES

        payload = {
            "run_id": run_id,
            "rlks": request.rlks,
            "reference_window": request.reference_window,
            "mb_mode": request.mb_mode,
            "timeout_seconds": request.timeout_seconds,
        }
        task_id = await task_service.create_task(
            task_type=JOB_TYPE_SBAS_IPTA_TIMESERIES,
            task_name=f"SBAS-InSAR IPTA Timeseries {run_id}",
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=JOB_TYPE_SBAS_IPTA_TIMESERIES,
            payload=payload,
            task_id=task_id,
            max_attempts=1,
        )
        return {
            "message": "SBAS-InSAR IPTA time-series job queued.",
            "run_id": run_id,
            "task_id": task_id,
            "job_id": job_id,
            "job_type": JOB_TYPE_SBAS_IPTA_TIMESERIES,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "already running" in message.lower() or "conflict" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/runs/{run_id}/artifacts/{relative_path:path}")
async def get_sbas_insar_run_artifact(run_id: str, relative_path: str):
    try:
        artifact_path = sbas_insar_production_service.resolve_run_artifact_path(run_id, relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    media_type = mimetypes.guess_type(str(artifact_path))[0] or "application/octet-stream"
    return FileResponse(
        artifact_path,
        media_type=media_type,
        filename=artifact_path.name,
    )


@router.get("/trial-runs")
async def list_sbas_insar_trial_runs():
    return await asyncio.to_thread(sbas_insar_production_service.list_trial_runs)


@router.get("/trial-runs/{trial_id}")
async def get_sbas_insar_trial_run(trial_id: str):
    try:
        return await asyncio.to_thread(sbas_insar_production_service.get_trial_detail, trial_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/trial-runs/{trial_id}/artifacts/{relative_path:path}")
async def get_sbas_insar_artifact(trial_id: str, relative_path: str):
    try:
        artifact_path = sbas_insar_production_service.resolve_artifact_path(trial_id, relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    media_type = mimetypes.guess_type(str(artifact_path))[0] or "application/octet-stream"
    return FileResponse(
        artifact_path,
        media_type=media_type,
        filename=artifact_path.name,
    )
