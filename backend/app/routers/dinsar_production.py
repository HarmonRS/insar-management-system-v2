"""D-InSAR multi-engine production routes."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .dependencies import _get_current_user, _require_admin
from ..config import read_int_env, settings
from ..models import AuthUserORM
from ..services import envi_service as _envi_svc
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service

router = APIRouter(prefix="/dinsar-production", tags=["dinsar-production"])

DINSAR_PRODUCTION_JOB_MAX_ATTEMPTS = read_int_env(
    "DINSAR_PRODUCTION_JOB_MAX_ATTEMPTS",
    6,
    minimum=1,
    maximum=20,
)
ISCE2_PRODUCTION_JOB_MAX_ATTEMPTS = read_int_env(
    "ISCE2_PRODUCTION_JOB_MAX_ATTEMPTS",
    1,
    minimum=1,
    maximum=10,
)


class RunJobRequest(BaseModel):
    engine_code: str = Field(..., description="Engine code: sarscape / isce2 / landsar")
    profile: str = Field(..., description="Engine profile, for example custom6 / lt1_stripmap")
    root_dir: str = Field(..., description="Windows root directory")
    num_to_process: int = Field(default=0, ge=0, description="How many tasks to process; 0 means all")
    timeout_seconds: Optional[int] = Field(default=None, ge=60)
    extra: Dict[str, Any] = Field(default_factory=dict, description="Engine-specific parameters")


class WslCheckRequest(BaseModel):
    distro: Optional[str] = Field(default=None, description="Optional WSL distro override")
    smoke_test: bool = Field(default=False)


def _get_registry():
    from ..dinsar_engines import registry

    return registry


@router.get("/engines")
async def list_engines():
    registry = _get_registry()
    result = []
    for engine in registry.list_engines():
        availability = await asyncio.to_thread(engine.check_available)
        result.append(
            {
                **engine.to_dict(),
                "status": availability.status,
                "available": availability.available,
                "message": availability.message,
                "checks": availability.checks,
            }
        )
    return {"engines": result}


@router.get("/engines/{engine_code}")
async def get_engine_detail(engine_code: str):
    registry = _get_registry()
    engine = registry.get_engine(engine_code)
    if not engine:
        raise HTTPException(status_code=404, detail=f"Engine '{engine_code}' not found.")

    availability = await asyncio.to_thread(engine.check_available)
    return {
        **engine.to_dict(),
        "status": availability.status,
        "available": availability.available,
        "message": availability.message,
        "checks": availability.checks,
    }


@router.post("/wsl-check")
async def run_wsl_check(
    req: WslCheckRequest,
    current_user: AuthUserORM = Depends(_require_admin),
):
    from ..services.wsl_service import check_wsl_environment

    distro = req.distro or settings.ISCE2_WSL_DISTRO
    report = await asyncio.to_thread(
        check_wsl_environment,
        distro=distro,
        python_cmd=settings.ISCE2_PYTHON,
        stripmap_app_path=settings.ISCE2_STRIPMAP_APP,
        pipeline_script_path=settings.ISCE2_PIPELINE_SCRIPT,
        dem_path_win=settings.ISCE2_DEM_PATH,
        orbit_dir_win=settings.ORBIT_POOL_ISCE2,
        output_dir_win=settings.ISCE2_OUTPUT_ROOT,
        smoke_test=req.smoke_test,
    )
    return report.to_dict()


@router.post("/run")
async def submit_run(
    req: RunJobRequest,
    current_user: AuthUserORM = Depends(_get_current_user),
):
    registry = _get_registry()
    engine = registry.get_engine(req.engine_code)
    if not engine:
        raise HTTPException(status_code=400, detail=f"Engine '{req.engine_code}' not found.")

    availability = await asyncio.to_thread(engine.check_available)
    if not availability.available:
        raise HTTPException(
            status_code=400,
            detail=f"Engine '{req.engine_code}' is unavailable: {availability.message}",
        )

    valid_profiles = {profile.code for profile in engine.get_profiles()}
    if req.profile not in valid_profiles:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Engine '{req.engine_code}' does not support profile '{req.profile}'. "
                f"Available profiles: {sorted(valid_profiles)}"
            ),
        )

    validation_summary = None
    if req.engine_code == "isce2" and hasattr(engine, "validate_root_dir"):
        try:
            validation_summary = await asyncio.to_thread(
                engine.validate_root_dir,
                req.root_dir,
                req.num_to_process,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = {
        "engine_code": req.engine_code,
        "profile": req.profile,
        "root_dir": req.root_dir,
        "num_to_process": req.num_to_process,
        "timeout_seconds": req.timeout_seconds,
        "extra": dict(req.extra or {}),
    }

    from ..services.job_handlers import JOB_TYPE_IDL_RUN_DINSAR, JOB_TYPE_ISCE2_RUN

    if req.engine_code == "sarscape":
        payload["mode"] = "custom" if req.profile == "custom6" else "metatask"
        job_type = JOB_TYPE_IDL_RUN_DINSAR
        max_attempts = DINSAR_PRODUCTION_JOB_MAX_ATTEMPTS
    elif req.engine_code == "isce2":
        if hasattr(engine, "normalize_extra"):
            try:
                payload["extra"] = engine.normalize_extra(payload["extra"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        job_type = JOB_TYPE_ISCE2_RUN
        max_attempts = ISCE2_PRODUCTION_JOB_MAX_ATTEMPTS
        if validation_summary is not None:
            payload["extra"].update(
                {
                    "__validated_task_count": validation_summary.get("task_count", 0),
                    "__validated_mode": validation_summary.get("mode", ""),
                }
            )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Engine '{req.engine_code}' does not support queued production.",
        )

    task_name = f"D-InSAR production: {req.engine_code}/{req.profile}"
    try:
        task_id = await task_service.create_task(
            task_type=job_type,
            task_name=task_name,
            params=payload,
        )
        job_id = await job_queue_service.create_job(
            job_type=job_type,
            payload=payload,
            task_id=task_id,
            max_attempts=max_attempts,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "任务冲突" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc

    return {
        "task_id": task_id,
        "job_id": job_id,
        "job_type": job_type,
        "engine_code": req.engine_code,
        "profile": req.profile,
        "selected_task_count": validation_summary.get("task_count", 1) if validation_summary else 1,
        "message": "Task queued.",
    }


@router.get("/runs")
async def list_runs(limit: int = 20):
    runs = await asyncio.to_thread(_envi_svc.list_recent_runs, limit)
    return {"runs": runs, "total": len(runs)}
