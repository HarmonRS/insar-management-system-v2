"""D-InSAR multi-engine production routes."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .dependencies import _get_current_user, _require_admin
from .. import database
from ..config import read_int_env, settings
from ..models import AuthUserORM
from ..services.dinsar_production_service import dinsar_production_service
from ..services.job_queue_service import job_queue_service
from ..services.pyint_input_assets_service import build_pyint_input_preview, summarize_preview_blockers
from ..services.task_service import task_service

router = APIRouter(prefix="/dinsar-production", tags=["dinsar-production"])

_RUN_ID_RE = re.compile(r"^[\w\-]{4,128}$")
_LOG_MAX_BYTES = 200 * 1024

DINSAR_PRODUCTION_JOB_MAX_ATTEMPTS = read_int_env(
    "DINSAR_PRODUCTION_JOB_MAX_ATTEMPTS",
    1,
    minimum=1,
    maximum=20,
)
ISCE2_PRODUCTION_JOB_MAX_ATTEMPTS = read_int_env(
    "ISCE2_PRODUCTION_JOB_MAX_ATTEMPTS",
    1,
    minimum=1,
    maximum=10,
)
PYINT_PRODUCTION_JOB_MAX_ATTEMPTS = read_int_env(
    "PYINT_PRODUCTION_JOB_MAX_ATTEMPTS",
    1,
    minimum=1,
    maximum=10,
)


class RunJobRequest(BaseModel):
    engine_code: str = Field(..., description="Engine code: sarscape / isce2 / pyint / landsar")
    profile: str = Field(..., description="Engine profile, for example custom6 / lt1_stripmap / lt1_gamma_dinsar")
    root_dir: str = Field(..., description="Windows root directory")
    num_to_process: int = Field(default=0, ge=0, description="How many tasks to process; 0 means all")
    rerun_mode: Literal["unfinished_only", "rerun_all"] = Field(
        default="unfinished_only",
        description="unfinished_only = only run unfinished tasks; rerun_all = rerun everything",
    )
    timeout_seconds: Optional[int] = Field(default=None, ge=60)
    extra: Dict[str, Any] = Field(default_factory=dict, description="Engine-specific parameters")


class WslCheckRequest(BaseModel):
    distro: Optional[str] = Field(default=None, description="Optional WSL distro override")
    smoke_test: bool = Field(default=False)


class PreviewInputAssetsRequest(BaseModel):
    root_dir: str = Field(..., description="Windows root directory or a single Task_* directory")
    num_to_process: int = Field(default=0, ge=0, description="How many tasks to preview; 0 means all")


def _get_registry():
    from ..dinsar_engines import registry

    return registry


def _new_session():
    if database.AsyncSessionLocal is None:
        database.init_db()
    if database.AsyncSessionLocal is None:
        raise RuntimeError("Database session factory is not initialized.")
    return database.AsyncSessionLocal()


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


@router.post("/engines/pyint/preview-input-assets")
async def preview_pyint_input_assets(
    req: PreviewInputAssetsRequest,
    current_user: AuthUserORM = Depends(_get_current_user),
):
    try:
        preview = await asyncio.to_thread(
            build_pyint_input_preview,
            req.root_dir,
            req.num_to_process,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return preview


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
    if hasattr(engine, "validate_root_dir"):
        try:
            validation_summary = await asyncio.to_thread(
                engine.validate_root_dir,
                req.root_dir,
                req.num_to_process,
                req.rerun_mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if int(validation_summary.get("task_count", 0) or 0) <= 0:
            if (
                req.rerun_mode == "unfinished_only"
                and int(validation_summary.get("skipped_completed_count", 0) or 0) > 0
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"All discovered Task_* directories already have completed "
                        f"{req.engine_code}/{req.profile} results under: {req.root_dir}"
                    ),
                )
            raise HTTPException(status_code=400, detail="No valid task directories selected.")

    effective_timeout_seconds = req.timeout_seconds
    if effective_timeout_seconds is None:
        engine_default_timeout = getattr(engine, "default_timeout_seconds", None)
        if engine_default_timeout:
            effective_timeout_seconds = int(engine_default_timeout)

    pyint_preview = None
    skip_pyint_submit_preview = (
        req.engine_code == "pyint"
        and req.rerun_mode == "unfinished_only"
        and validation_summary is not None
        and int(validation_summary.get("skipped_completed_count", 0) or 0) > 0
    )
    if req.engine_code == "pyint" and not skip_pyint_submit_preview:
        try:
            pyint_preview = await asyncio.to_thread(
                build_pyint_input_preview,
                req.root_dir,
                req.num_to_process,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not pyint_preview.get("allow_submit"):
            detail = summarize_preview_blockers(pyint_preview)
            raise HTTPException(
                status_code=400,
                detail=f"PyINT 输入资产预检未通过: {detail or '请先修复阻塞项。'}",
            )
        if not pyint_preview.get("allow_submit"):
            detail = summarize_preview_blockers(pyint_preview)
            raise HTTPException(
                status_code=400,
                detail=f"PyINT 输入资产预检未通过: {detail or '请先修复阻塞项。'}",
            )

    payload = {
        "engine_code": req.engine_code,
        "profile": req.profile,
        "root_dir": req.root_dir,
        "num_to_process": req.num_to_process,
        "rerun_mode": req.rerun_mode,
        "timeout_seconds": effective_timeout_seconds,
        "extra": dict(req.extra or {}),
    }

    from ..services.job_handlers import JOB_TYPE_IDL_RUN_DINSAR, JOB_TYPE_ISCE2_RUN, JOB_TYPE_PYINT_RUN
    create_managed_run = False
    normalized_extra = dict(payload["extra"])

    if req.engine_code == "sarscape":
        payload["mode"] = "custom" if req.profile == "custom6" else "metatask"
        job_type = JOB_TYPE_IDL_RUN_DINSAR
        max_attempts = DINSAR_PRODUCTION_JOB_MAX_ATTEMPTS
        create_managed_run = True
    elif req.engine_code in {"isce2", "pyint"}:
        if hasattr(engine, "normalize_extra"):
            try:
                payload["extra"] = engine.normalize_extra(payload["extra"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        normalized_extra = dict(payload["extra"])
        if req.engine_code == "isce2":
            job_type = JOB_TYPE_ISCE2_RUN
            max_attempts = ISCE2_PRODUCTION_JOB_MAX_ATTEMPTS
            create_managed_run = True
        else:
            job_type = JOB_TYPE_PYINT_RUN
            max_attempts = PYINT_PRODUCTION_JOB_MAX_ATTEMPTS
            create_managed_run = True
        if validation_summary is not None:
            validated_task_count = validation_summary.get("task_count", 0)
            payload["extra"].update(
                {
                    "__validated_task_count": validated_task_count,
                    "__validated_mode": validation_summary.get("mode", ""),
                    "__rerun_mode": req.rerun_mode,
                    "__discovered_task_count": int(validation_summary.get("discovered_task_count", validated_task_count) or 0),
                    "__skipped_completed_count": int(validation_summary.get("skipped_completed_count", 0) or 0),
                }
            )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Engine '{req.engine_code}' does not support queued production.",
        )

    try:
        if create_managed_run:
            async with _new_session() as db:
                result = await dinsar_production_service.create_run(
                    engine_code=req.engine_code,
                    profile_code=req.profile,
                    root_dir=req.root_dir,
                    num_to_process=req.num_to_process,
                    rerun_mode=req.rerun_mode,
                    timeout_seconds=req.timeout_seconds,
                    extra=normalized_extra,
                    created_by=getattr(current_user, "username", None),
                    db=db,
                )
            return {
                "task_id": result["task_id"],
                "job_id": result.get("workflow_run_id"),
                "run_id": result["run_id"],
                "workflow_run_id": result.get("workflow_run_id"),
                "job_type": job_type,
                "engine_code": req.engine_code,
                "profile": req.profile,
                "selected_task_count": result.get("selected_task_count", 0),
                "discovered_task_count": result.get("discovered_task_count", result.get("selected_task_count", 0)),
                "skipped_completed_count": result.get("skipped_completed_count", 0),
                "rerun_mode": result.get("rerun_mode", req.rerun_mode),
                "message": "Task queued.",
            }

        task_name = f"D-InSAR production: {req.engine_code}/{req.profile}"
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
        "discovered_task_count": (
            validation_summary.get("discovered_task_count", validation_summary.get("task_count", 1))
            if validation_summary
            else 1
        ),
        "skipped_completed_count": validation_summary.get("skipped_completed_count", 0) if validation_summary else 0,
        "rerun_mode": req.rerun_mode,
        "message": "Task queued.",
    }


@router.get("/runs")
async def list_runs(limit: int = 20, offset: int = 0):
    async with _new_session() as db:
        result = await dinsar_production_service.list_runs(db, limit=limit, offset=offset)
    return result


@router.get("/runs/{run_id}/log")
async def get_run_log(
    run_id: str,
    current_user: AuthUserORM = Depends(_get_current_user),
):
    _ = current_user
    normalized_run_id = str(run_id or "").strip()
    if not _RUN_ID_RE.match(normalized_run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format.")
    try:
        return await asyncio.to_thread(
            dinsar_production_service.read_run_log,
            normalized_run_id,
            max_bytes=_LOG_MAX_BYTES,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run log file not found.") from exc


@router.delete("/runs/{run_id}/log")
async def delete_run_log(
    run_id: str,
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    normalized_run_id = str(run_id or "").strip()
    if not _RUN_ID_RE.match(normalized_run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format.")
    deleted = await asyncio.to_thread(
        dinsar_production_service.delete_run_log,
        normalized_run_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Run log file not found.")
    return {
        "run_id": normalized_run_id,
        "deleted": True,
    }


@router.delete("/runs/{run_id}")
async def delete_run_record(
    run_id: str,
    current_user: AuthUserORM = Depends(_require_admin),
):
    _ = current_user
    normalized_run_id = str(run_id or "").strip()
    if not _RUN_ID_RE.match(normalized_run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format.")

    async with _new_session() as db:
        try:
            result = await dinsar_production_service.delete_run_record(
                normalized_run_id,
                db=db,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Production run not found.")
    return {
        **result,
        "deleted": True,
        "products_deleted": False,
    }
