from __future__ import annotations

import logging
import mimetypes
import os
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..config import read_int_env, settings, split_env_paths
from ..models import AuthUserORM, DinsarResult, DinsarResultPage
from ..services.dinsar_read_service import DinsarCatalogReadRecord, dinsar_read_service
from ..services.dinsar_export import export_dinsar_results
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from .dependencies import _add_operation_audit_log, _require_admin, _validate_export_path

router = APIRouter()
logger = logging.getLogger(__name__)
MAX_SCAN_DIRECTORY_COUNT = read_int_env(
    "MAX_SCAN_DIRECTORY_COUNT",
    64,
    minimum=1,
    maximum=500,
)
MAX_SCAN_PATH_LENGTH = read_int_env(
    "MAX_SCAN_PATH_LENGTH",
    2048,
    minimum=64,
    maximum=32767,
)
LIST_QUERY_MAX_LIMIT = read_int_env(
    "LIST_QUERY_MAX_LIMIT",
    2000,
    minimum=1,
    maximum=100000,
)
LIST_QUERY_MAX_OFFSET = read_int_env(
    "LIST_QUERY_MAX_OFFSET",
    200000,
    minimum=0,
    maximum=20000000,
)
LIST_QUERY_MAX_WINDOW = read_int_env(
    "LIST_QUERY_MAX_WINDOW",
    202000,
    minimum=1,
    maximum=50000000,
)
LIST_QUERY_TIMEOUT_MS = read_int_env(
    "LIST_QUERY_TIMEOUT_MS",
    20000,
    minimum=1000,
    maximum=300000,
)


class DinsarScanRequest(BaseModel):
    results_directories: List[str]

    @field_validator("results_directories", mode="before")
    @classmethod
    def _normalize_results_directories(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("results_directories must be a list.")

        normalized: List[str] = []
        for raw in value:
            path = str(raw or "").strip()
            if not path:
                continue
            if len(path) > MAX_SCAN_PATH_LENGTH:
                raise ValueError(
                    f"results_directories contains a path longer than {MAX_SCAN_PATH_LENGTH} characters."
                )
            if path not in normalized:
                normalized.append(path)

        if len(normalized) > MAX_SCAN_DIRECTORY_COUNT:
            raise ValueError(
                f"results_directories exceeds max directory count ({MAX_SCAN_DIRECTORY_COUNT})."
            )
        return normalized


def _get_default_dinsar_scan_dirs() -> List[str]:
    normalized: List[str] = []
    for item in split_env_paths(settings.MONITOR_DINSAR_DIRS):
        path = str(item or "").strip()
        if path and path not in normalized:
            normalized.append(path)
    return normalized


def _is_postgresql_session(db: AsyncSession) -> bool:
    try:
        bind = db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        return (dialect_name or "").lower() == "postgresql"
    except Exception:
        return False


async def _apply_list_query_statement_timeout(db: AsyncSession) -> None:
    timeout_ms = int(LIST_QUERY_TIMEOUT_MS)
    if timeout_ms <= 0:
        return
    if not _is_postgresql_session(db):
        return
    try:
        # PostgreSQL SET/SET LOCAL does not support bind parameters in this form.
        timeout_ms = int(timeout_ms)
        await db.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
    except Exception as exc:
        logger.warning("Failed to apply list query statement_timeout=%sms: %s", timeout_ms, exc)


def _normalize_list_pagination(limit: int, offset: int) -> tuple[int, int]:
    safe_limit = min(LIST_QUERY_MAX_LIMIT, max(1, int(limit or 1)))
    safe_offset = min(LIST_QUERY_MAX_OFFSET, max(0, int(offset or 0)))
    if safe_limit + safe_offset > LIST_QUERY_MAX_WINDOW:
        safe_offset = max(0, LIST_QUERY_MAX_WINDOW - safe_limit)
    return safe_limit, safe_offset


def _build_dinsar_result_payload(record: DinsarCatalogReadRecord) -> DinsarResult:
    compat_row = record.compat_row
    product = record.product
    preview_path = dinsar_read_service.resolve_preview_path(product, compat_row)
    min_lon = product.min_lon if product.min_lon is not None else getattr(compat_row, "min_lon", None)
    min_lat = product.min_lat if product.min_lat is not None else getattr(compat_row, "min_lat", None)
    max_lon = product.max_lon if product.max_lon is not None else getattr(compat_row, "max_lon", None)
    max_lat = product.max_lat if product.max_lat is not None else getattr(compat_row, "max_lat", None)
    file_path = (
        str(product.primary_asset_path or "").strip()
        or str(product.source_primary_path or "").strip()
        or (str(compat_row.file_path).strip() if compat_row is not None and compat_row.file_path else "")
        or str(product.manifest_path or "").strip()
    )
    return DinsarResult(
        id=int(compat_row.id if compat_row is not None else product.id),
        product_id=product.product_id,
        compat_result_id=int(compat_row.id) if compat_row is not None else None,
        name=record.display_name,
        task_name=product.task_name,
        task_alias=product.task_alias,
        pair_key=product.pair_key,
        pair_uid=product.pair_uid,
        run_key=product.run_key,
        network_run_id=product.network_run_id,
        network_edge_id=product.network_edge_id,
        policy_version=product.policy_version,
        selection_strategy=product.selection_strategy,
        engine_code=product.engine_code,
        file_path=file_path,
        min_lon=float(min_lon or 0.0),
        min_lat=float(min_lat or 0.0),
        max_lon=float(max_lon or 0.0),
        max_lat=float(max_lat or 0.0),
        coverage_polygon=product.coverage_polygon,
        is_cached=bool(preview_path and os.path.exists(preview_path)),
        ai_score=product.ai_score,
        user_label=product.user_label,
        ai_report=getattr(compat_row, "ai_report", None),
    )


async def _get_cached_image(result_id: int, db: AsyncSession):
    """
    获取Dinsar结果的可视化图像。
    使用 FileResponse 以支持浏览器缓存 (ETag, Last-Modified) 和断点续传。
    """
    record = await dinsar_read_service.get_compat_record(db, compat_result_id=result_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"ID为 {result_id} 的结果不存在。")

    target_path = dinsar_read_service.resolve_preview_path(record.product, record.compat_row)

    if target_path and os.path.exists(target_path):
        media_type = mimetypes.guess_type(target_path)[0] or "application/octet-stream"
        return FileResponse(
            target_path,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=31536000"},
        )

    compat_row = record.compat_row
    if compat_row is not None and compat_row.is_cached:
        compat_row.is_cached = False
        db.add(compat_row)
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="数据不一致：缓存文件丢失。状态已重置，请重新扫描以修复。"
        )

    raise HTTPException(
        status_code=202,
        detail="缓存等待生成。请运行扫描程序。"
    )


@router.post("/scan-dinsar-results", status_code=202)
async def scan_dinsar_results_endpoint(
    background_tasks: BackgroundTasks,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
    request: Optional[DinsarScanRequest] = None,
):
    """
    触发Dinsar结果扫描的API端点，支持多个目录。
    """
    try:
        scan_dirs = list(request.results_directories) if request else []
        if not scan_dirs:
            scan_dirs = _get_default_dinsar_scan_dirs()
        if not scan_dirs:
            raise HTTPException(status_code=400, detail="未提供结果目录，且 MONITOR_DINSAR_DIRS 未配置。")
        task_id = await task_service.create_task("SCAN_DINSAR", "D-InSAR 统一扫描", params={"dirs": scan_dirs})
        payload = {"dirs": scan_dirs}
        await job_queue_service.create_job("SCAN_DINSAR", payload=payload, task_id=task_id)
        await _add_operation_audit_log(
            db,
            request=http_request,
            action="task_queued",
            resource="scan-dinsar-results",
            detail={"task_id": task_id, "directory_count": len(scan_dirs)},
        )
        await db.commit()
        return {"message": "D-InSAR 统一扫描任务已进入队列", "task_id": task_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("D-InSAR 结果扫描失败")
        raise HTTPException(status_code=500, detail="D-InSAR 结果扫描失败，请查看后端日志")


@router.get("/dinsar-results", response_model=DinsarResultPage)
async def get_all_dinsar_results_endpoint(
    limit: int = 500,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """
    从数据库获取所有Dinsar结果的元数据。
    """
    limit, offset = _normalize_list_pagination(limit, offset)
    await _apply_list_query_statement_timeout(db)
    total = await dinsar_read_service.count_compat_records(db)
    records = await dinsar_read_service.list_compat_records(
        db,
        limit=limit,
        offset=offset,
    )
    return DinsarResultPage(
        items=[_build_dinsar_result_payload(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(records)) < total,
    )


@router.get("/dinsar-results/{result_id}/thumb")
async def get_dinsar_thumb_endpoint(result_id: int, db: AsyncSession = Depends(get_db)):
    """获取指定ID的Dinsar结果的可视化图像。"""
    return await _get_cached_image(result_id, db)


@router.get("/dinsar-results/{result_id}/full")
async def get_dinsar_full_endpoint(result_id: int, db: AsyncSession = Depends(get_db)):
    """获取指定ID的Dinsar结果的可视化图像 (现在与缩略图一致)。"""
    return await _get_cached_image(result_id, db)


@router.post("/dinsar-results/{result_id}/label")
async def label_dinsar_result(
    result_id: int,
    http_request: Request,
    label: Optional[int] = Form(None),  # 0: Bad, 1: Good, None: Clear
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    设置或清除Dinsar结果的用户标签。
    """
    record = await dinsar_read_service.get_compat_record(db, compat_result_id=result_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Result not found")

    record.product.user_label = label
    if record.compat_row is not None:
        record.compat_row.user_label = label
    await _add_operation_audit_log(
        db,
        request=http_request,
        action="dinsar_label_updated",
        resource=f"dinsar-results/{result_id}/label",
        detail={"label": label},
    )
    await db.commit()

    return {
        "message": "Label updated",
        "id": result_id,
        "product_id": record.product.product_id,
        "new_label": label,
    }


class DinsarExportRequest(BaseModel):
    result_ids: List[int]
    target_dir: str

    @field_validator("target_dir", mode="before")
    @classmethod
    def _validate_target_dir(cls, value):
        v = str(value or "").strip()
        if not v:
            raise ValueError("target_dir is required")
        if len(v) > MAX_SCAN_PATH_LENGTH:
            raise ValueError(f"target_dir exceeds max length ({MAX_SCAN_PATH_LENGTH})")
        return v

    @field_validator("result_ids", mode="before")
    @classmethod
    def _validate_result_ids(cls, value):
        if not value or not isinstance(value, list) or len(value) == 0:
            raise ValueError("result_ids must be a non-empty list")
        if len(value) > 500:
            raise ValueError("result_ids exceeds max count (500)")
        return value


@router.post("/dinsar-results/export")
async def export_dinsar_results_endpoint(
    request: DinsarExportRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    将选中的 D-InSAR 结果文件复制到用户指定的目录。
    支持本地路径和 UNC 路径。
    """
    _validate_export_path(request.target_dir, "target_dir")
    records = await dinsar_read_service.list_compat_records_by_ids(
        db,
        compat_result_ids=request.result_ids,
    )

    if not records:
        raise HTTPException(status_code=404, detail="未找到任何匹配的结果记录")

    export_items = []
    for record in records:
        compat_row = record.compat_row
        file_path = (
            str(record.product.primary_asset_path or "").strip()
            or str(record.product.source_primary_path or "").strip()
            or (str(compat_row.file_path).strip() if compat_row is not None and compat_row.file_path else "")
        )
        if file_path:
            export_items.append(
                {
                    "source": file_path,
                    "task_alias": str(record.product.task_alias or "").strip(),
                    "task_name": str(record.product.task_name or "").strip(),
                    "display_name": str(record.display_name or "").strip(),
                    "product_id": str(record.product.product_id or "").strip(),
                }
            )

    if not export_items:
        raise HTTPException(status_code=400, detail="选中的结果没有关联的文件路径")

    try:
        import asyncio
        export_result = await asyncio.to_thread(
            export_dinsar_results, export_items, request.target_dir
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("导出 D-InSAR 结果失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"导出失败: {e}")

    await _add_operation_audit_log(
        db,
        request=http_request,
        action="dinsar_results_exported",
        resource="dinsar-results/export",
        detail={
            "result_ids": request.result_ids,
            "target_dir": request.target_dir,
            "copied": export_result["copied"],
            "failed": export_result["failed"],
        },
    )
    await db.commit()

    return export_result
