from __future__ import annotations

import asyncio
import mimetypes
import subprocess

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from ..services.job_queue_service import job_queue_service
from ..services.sbas_insar_production_service import sbas_insar_production_service
from ..services.task_service import task_service


router = APIRouter(prefix="/sbas-insar-production", tags=["sbas-insar-production"])


class SbasStackDiscoverRequest(BaseModel):
    source_roots: list[str] | None = None
    orbit_roots: list[str] | None = None
    min_scenes: int = Field(default=3, ge=2, le=100)
    require_orbits: bool = True
    include_scenes: bool = False
    limit: int = Field(default=30, ge=0, le=500)
    platform: str | None = Field(default=None, max_length=16)
    relative_orbit: str | None = Field(default=None, max_length=32)
    orbit_direction: str | None = Field(default=None, max_length=32)

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

    @field_validator("platform", "relative_orbit", "orbit_direction", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class SbasMonitorPoint(BaseModel):
    point_id: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=120)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class SbasRunSubmitRequest(SbasStackDiscoverRequest):
    run_label: str | None = Field(default=None, max_length=120)
    dry_run: bool = True
    monitor_point_strategy: str = Field(default="auto_low_sigma_high_rate", max_length=64)
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


@router.get("/capabilities")
async def get_sbas_insar_capabilities():
    return sbas_insar_production_service.get_capabilities()


@router.post("/stacks/discover")
async def discover_sbas_insar_stacks(request: SbasStackDiscoverRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.discover_stacks,
            source_roots=request.source_roots,
            orbit_roots=request.orbit_roots,
            min_scenes=request.min_scenes,
            require_orbits=request.require_orbits,
            include_scenes=request.include_scenes,
            limit=request.limit,
            platform=request.platform,
            relative_orbit=request.relative_orbit,
            orbit_direction=request.orbit_direction,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/stacks/{stack_id}/audit")
async def audit_sbas_insar_stack(stack_id: str, request: SbasStackDiscoverRequest):
    try:
        return await asyncio.to_thread(
            sbas_insar_production_service.audit_stack,
            stack_id,
            source_roots=request.source_roots,
            orbit_roots=request.orbit_roots,
            min_scenes=request.min_scenes,
            require_orbits=request.require_orbits,
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
            run_label=request.run_label,
            source_roots=request.source_roots,
            orbit_roots=request.orbit_roots,
            min_scenes=request.min_scenes,
            require_orbits=request.require_orbits,
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
        return await asyncio.to_thread(sbas_insar_production_service.get_run_detail, run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
