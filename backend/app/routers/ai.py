from __future__ import annotations

import io
import base64
import logging
import os
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional

from ..ai_service import (
    analyze_map_with_vlm,
    get_model_info,
    is_model_trained,
    predict_quality,
    train_quality_model,
)
from ..database import get_db
from ..config import read_int_env, settings
from ..models import (
    AuthUserORM,
    AiDiagnosisORM,
    AiDiagnosisCreate,
    AiDiagnosisResponse,
    AiDiagnosisListResponse,
)
from ..services.dinsar_read_service import dinsar_read_service
from ..services.job_queue_service import job_queue_service
from ..services.task_service import task_service
from ..services.ai_prompts import get_prompt_template, list_prompt_templates
from .dependencies import _require_admin

router = APIRouter()
logger = logging.getLogger(__name__)
AI_ANALYZE_MAP_MAX_IMAGES = read_int_env(
    "AI_ANALYZE_MAP_MAX_IMAGES",
    4,
    minimum=1,
    maximum=50,
)
AI_ANALYZE_MAP_MAX_IMAGE_BASE64_CHARS = read_int_env(
    "AI_ANALYZE_MAP_MAX_IMAGE_BASE64_CHARS",
    12 * 1024 * 1024,
    minimum=1024,
    maximum=100 * 1024 * 1024,
)
AI_ANALYZE_MAP_PROMPT_MAX_CHARS = read_int_env(
    "AI_ANALYZE_MAP_PROMPT_MAX_CHARS",
    8000,
    minimum=32,
    maximum=200000,
)


class MapAnalysisRequest(BaseModel):
    images: List[str] = Field(default_factory=list)  # Base64 strings
    prompt: Optional[str] = None

    @field_validator("images", mode="before")
    @classmethod
    def _validate_images(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("images must be a list.")
        if len(value) == 0:
            raise ValueError("images must not be empty.")
        if len(value) > AI_ANALYZE_MAP_MAX_IMAGES:
            raise ValueError(
                f"images exceeds max count ({AI_ANALYZE_MAP_MAX_IMAGES})."
            )

        normalized: List[str] = []
        for raw in value:
            img = str(raw or "").strip()
            if not img:
                continue
            if len(img) > AI_ANALYZE_MAP_MAX_IMAGE_BASE64_CHARS:
                raise ValueError(
                    "one image exceeds max base64 length "
                    f"({AI_ANALYZE_MAP_MAX_IMAGE_BASE64_CHARS} chars)."
                )
            normalized.append(img)

        if not normalized:
            raise ValueError("images must contain at least one non-empty base64 string.")
        return normalized

    @field_validator("prompt", mode="before")
    @classmethod
    def _validate_prompt(cls, value):
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if len(normalized) > AI_ANALYZE_MAP_PROMPT_MAX_CHARS:
            raise ValueError(
                f"prompt exceeds max length ({AI_ANALYZE_MAP_PROMPT_MAX_CHARS} chars)."
            )
        return normalized


@router.post("/ai/train", status_code=202)
async def train_ai_model(background_tasks: BackgroundTasks, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    使用已标记的数据训练AI模型。
    """
    try:
        task_id = await task_service.create_task("AI_TRAIN", "AI 模型训练")
        await job_queue_service.create_job("AI_TRAIN", payload={}, task_id=task_id)
        return {"message": "AI模型训练任务已进入队列", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("AI 模型训练任务创建失败")
        raise HTTPException(status_code=500, detail="AI 模型训练任务创建失败，请查看后端日志")


@router.post("/ai/predict-all", status_code=202)
async def predict_all_quality(background_tasks: BackgroundTasks, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    使用训练好的模型预测所有结果的质量。
    """
    try:
        task_id = await task_service.create_task("AI_PREDICT", "AI 质量预测")
        await job_queue_service.create_job("AI_PREDICT", payload={}, task_id=task_id)
        return {"message": "AI质量预测任务已进入队列", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception("AI 质量预测任务创建失败")
        raise HTTPException(status_code=500, detail="AI 质量预测任务创建失败，请查看后端日志")


@router.get("/ai/status")
async def get_ai_status(db: AsyncSession = Depends(get_db)):
    """
    获取AI模型状态和标记统计，并检测 Ollama 连通性。
    """
    counts = await dinsar_read_service.get_ai_status_counts(db)

    ollama_online = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=1.0) as client:
            ollama_base = settings.OLLAMA_BASE_URL
            response = await client.get(f"{ollama_base.rstrip('/')}/api/tags")
            ollama_online = response.status_code == 200
    except Exception:
        ollama_online = False

    return {
        "is_model_trained": is_model_trained(),
        "model_info": get_model_info(),
        "labeled_count": counts["labeled_count"],
        "good_count": counts["good_count"],
        "bad_count": counts["bad_count"],
        "ollama_online": ollama_online
    }


@router.post("/ai/warmup")
async def warmup_ai_endpoint(background_tasks: BackgroundTasks, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    发起 AI 模型预热任务，提前将 VLM 加载至显存。
    """
    try:
        task_id = await task_service.create_task("AI_WARMUP", "AI 模型预热")
        await job_queue_service.create_job("AI_WARMUP", payload={}, task_id=task_id)
        return {"task_id": task_id}
    except Exception as e:
        logger.exception("AI 模型预热任务创建失败")
        raise HTTPException(status_code=500, detail="AI 模型预热任务创建失败，请查看后端日志")


@router.post("/ai/analyze-map")
async def analyze_map_endpoint(request: MapAnalysisRequest, admin_user: AuthUserORM = Depends(_require_admin)):
    """
    接收地图截图并调用本地 VLM 进行分析。
    """
    try:
        analysis = await analyze_map_with_vlm(request.images, request.prompt)
        return {"analysis": analysis}
    except Exception as e:
        logger.exception("AI 地图分析失败")
        raise HTTPException(status_code=500, detail="AI 分析失败，请查看后端日志")


@router.post("/ai/analyze-result/{result_id}", status_code=202)
async def analyze_dinsar_result_endpoint(
    result_id: int,
    background_tasks: BackgroundTasks,
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    异步自动分析指定的 D-InSAR 结果。
    """
    try:
        task_id = await task_service.create_task("AI_ANALYZE", f"AI 诊断 (结果 ID: {result_id})", params={"result_id": result_id})
        payload = {"result_id": result_id}
        await job_queue_service.create_job("AI_ANALYZE", payload=payload, task_id=task_id)
        return {"message": "AI 智能诊断任务已进入队列", "task_id": task_id}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ============ 新版 AI 诊断 RESTful API ============


@router.get("/ai/prompt-templates")
async def get_prompt_templates():
    """
    获取所有可用的 Prompt 模板列表。
    """
    return list_prompt_templates()


@router.post("/ai/diagnosis", status_code=202)
async def create_diagnosis(
    request: AiDiagnosisCreate,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    创建 AI 诊断任务（异步执行）。

    简化流程：
    1. 验证 result_id 存在
    2. 创建 SystemTask 和 SystemJob
    3. 在 job handler 中创建诊断记录
    """
    try:
        logger.info(f"收到 AI 诊断请求: result_id={request.result_id}, model={request.model_name}")

        # 验证 result_id 存在
        logger.info("正在验证 result_id...")
        record = await dinsar_read_service.get_compat_record(
            db,
            compat_result_id=request.result_id,
        )
        if record is None:
            logger.warning(f"D-InSAR 结果 ID {request.result_id} 不存在")
            raise HTTPException(status_code=404, detail=f"D-InSAR 结果 ID {request.result_id} 不存在")

        result_name = record.display_name
        logger.info(f"找到 D-InSAR 结果: {result_name}")

        # 获取 Prompt 文本
        logger.info("正在获取 Prompt 文本...")
        if request.custom_prompt:
            prompt_text = request.custom_prompt
        else:
            try:
                prompt_text = get_prompt_template(request.prompt_template)
            except ValueError as e:
                logger.error(f"Prompt 模板错误: {e}")
                raise HTTPException(status_code=400, detail=str(e))

        logger.info(f"Prompt 文本长度: {len(prompt_text)}")

        # 创建后台任务
        logger.info("正在创建 SystemTask...")
        task_id = await task_service.create_task(
            "AI_DIAGNOSIS",
            f"AI 诊断 - {result_name}",
            params={
                "result_id": request.result_id,
                "product_id": record.product.product_id,
                "model_name": request.model_name,
                "prompt_template": request.prompt_template,
            }
        )
        logger.info(f"SystemTask 已创建: {task_id}")

        logger.info("正在创建 SystemJob...")
        payload = {
            "result_id": request.result_id,
            "product_id": record.product.product_id,
            "model_name": request.model_name,
            "prompt_template": request.prompt_template,
            "prompt_text": prompt_text,
        }
        await job_queue_service.create_job("AI_DIAGNOSIS", payload=payload, task_id=task_id)
        logger.info("SystemJob 已创建")

        logger.info(f"AI 诊断任务已创建: result_id={request.result_id}, task_id={task_id}")

        return {
            "message": "AI 诊断任务已创建",
            "task_id": task_id,
            "result_id": request.result_id,
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"创建任务失败 (ValueError): {str(e)}", exc_info=True)
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"创建任务失败 (Exception): {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="AI 诊断任务创建失败，请查看后端日志")


@router.get("/ai/diagnosis", response_model=AiDiagnosisListResponse)
async def list_diagnoses(
    result_id: Optional[int] = None,
    task_id: Optional[str] = None,
    risk_level: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """
    查询 AI 诊断记录列表（支持分页和过滤）。

    - result_id: 按 D-InSAR 结果 ID 过滤
    - task_id: 按任务 ID 过滤
    - risk_level: 按风险等级过滤（low/medium/high/critical）
    - page: 页码（从 1 开始）
    - page_size: 每页数量（1-100）
    """
    if page < 1:
        raise HTTPException(status_code=400, detail="page 必须 >= 1")
    if page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="page_size 必须在 1-100 之间")

    # 构建查询
    query = select(AiDiagnosisORM)

    if result_id is not None:
        query = query.where(AiDiagnosisORM.result_id == result_id)
    if task_id is not None:
        query = query.where(AiDiagnosisORM.task_id == task_id)
    if risk_level is not None:
        query = query.where(AiDiagnosisORM.risk_level == risk_level)

    # 总数查询
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # 分页查询（按创建时间倒序）
    query = query.order_by(desc(AiDiagnosisORM.created_at))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    return AiDiagnosisListResponse(
        items=[AiDiagnosisResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/ai/diagnosis/{diagnosis_id}", response_model=AiDiagnosisResponse)
async def get_diagnosis(
    diagnosis_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    获取单个 AI 诊断记录详情。
    """
    result = await db.execute(
        select(AiDiagnosisORM).where(AiDiagnosisORM.id == diagnosis_id)
    )
    diagnosis = result.scalar_one_or_none()

    if not diagnosis:
        raise HTTPException(status_code=404, detail=f"诊断记录 ID {diagnosis_id} 不存在")

    return diagnosis


@router.delete("/ai/diagnosis/{diagnosis_id}", status_code=204)
async def delete_diagnosis(
    diagnosis_id: int,
    db: AsyncSession = Depends(get_db),
    admin_user: AuthUserORM = Depends(_require_admin),
):
    """
    删除 AI 诊断记录。
    """
    result = await db.execute(
        select(AiDiagnosisORM).where(AiDiagnosisORM.id == diagnosis_id)
    )
    diagnosis = result.scalar_one_or_none()

    if not diagnosis:
        raise HTTPException(status_code=404, detail=f"诊断记录 ID {diagnosis_id} 不存在")

    await db.delete(diagnosis)
    await db.commit()

    logger.info(f"AI 诊断记录已删除: diagnosis_id={diagnosis_id}")
