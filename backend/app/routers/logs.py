"""
日志管理 API

提供日志文件的列表、查看、删除功能。
"""
import itertools
import logging
import os
from typing import List, Optional
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import settings
from ..models import AuthUserORM
from .dependencies import _get_current_user


router = APIRouter(prefix="/logs", tags=["logs"])

# 日志根目录
LOG_ROOT = Path(settings.PROJECT_ROOT) / "logs"


class LogFileInfo(BaseModel):
    """日志文件信息"""
    name: str
    path: str
    size: int
    modified_at: str
    type: str  # app, task, error


class LogContentResponse(BaseModel):
    """日志内容响应"""
    file_name: str
    total_lines: int
    content: str
    offset: int
    limit: int


def _get_log_type(file_path: Path) -> str:
    """根据路径判断日志类型"""
    path_str = str(file_path)
    if "logs/app" in path_str or "logs\\app" in path_str:
        return "app"
    elif "logs/error" in path_str or "logs\\error" in path_str:
        return "error"
    elif "logs/tasks" in path_str or "logs\\tasks" in path_str:
        return "task"
    return "other"


def _is_safe_path(file_path: str) -> bool:
    """检查路径是否安全（防止路径遍历攻击）"""
    try:
        requested_path = (LOG_ROOT / file_path).resolve()
        return requested_path.is_relative_to(LOG_ROOT)
    except (ValueError, RuntimeError):
        return False


@router.get("/list", response_model=List[LogFileInfo])
async def list_logs(
    log_type: Optional[str] = Query(None, description="日志类型: app, task, error"),
    current_user: AuthUserORM = Depends(_get_current_user)
):
    """
    列出所有日志文件

    - 支持按类型过滤
    - 返回文件名、大小、修改时间
    """
    if not LOG_ROOT.exists():
        return []

    log_files = []

    # 遍历日志目录
    for log_file in LOG_ROOT.rglob("*.log"):
        if log_file.is_file() and log_file.name != ".gitkeep":
            file_type = _get_log_type(log_file)

            # 类型过滤
            if log_type and file_type != log_type:
                continue

            # 获取相对路径
            relative_path = log_file.relative_to(LOG_ROOT)

            log_files.append(LogFileInfo(
                name=log_file.name,
                path=str(relative_path).replace("\\", "/"),
                size=log_file.stat().st_size,
                modified_at=datetime.fromtimestamp(log_file.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                type=file_type
            ))

    # 按修改时间倒序排序
    log_files.sort(key=lambda x: x.modified_at, reverse=True)

    return log_files


@router.get("/content/{log_path:path}", response_model=LogContentResponse)
async def get_log_content(
    log_path: str,
    offset: int = Query(0, ge=0, description="起始行号"),
    limit: int = Query(1000, ge=1, le=10000, description="读取行数"),
    current_user: AuthUserORM = Depends(_get_current_user)
):
    """
    读取日志内容

    - 支持分页读取（大文件）
    - offset: 起始行号（从 0 开始）
    - limit: 读取行数（最多 10000 行）
    """
    # 安全检查
    if not _is_safe_path(log_path):
        raise HTTPException(status_code=400, detail="非法的文件路径")

    log_file = LOG_ROOT / log_path

    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    if not log_file.is_file():
        raise HTTPException(status_code=400, detail="不是有效的文件")

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            # Count total lines without loading all into memory
            total_lines = sum(1 for _ in f)

        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            # Skip to offset using islice, then read 'limit' lines
            content_lines = list(itertools.islice(f, offset, offset + limit))

        return LogContentResponse(
            file_name=log_file.name,
            total_lines=total_lines,
            content="".join(content_lines),
            offset=offset,
            limit=limit
        )
    except Exception as e:
        logger.exception("读取日志失败: %s", log_file.name)
        raise HTTPException(status_code=500, detail="读取日志失败，请查看后端日志")


@router.delete("/{log_path:path}")
async def delete_log(
    log_path: str,
    current_user: AuthUserORM = Depends(_get_current_user)
):
    """
    删除日志文件

    - 仅管理员可以删除
    - 需要确认操作
    """
    # 权限检查
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以删除日志")

    # 安全检查
    if not _is_safe_path(log_path):
        raise HTTPException(status_code=400, detail="非法的文件路径")

    log_file = LOG_ROOT / log_path

    if not log_file.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    if not log_file.is_file():
        raise HTTPException(status_code=400, detail="不是有效的文件")

    try:
        log_file.unlink()
        return {"message": f"日志文件 {log_file.name} 已删除"}
    except Exception as e:
        logger.exception("删除日志失败: %s", log_file.name)
        raise HTTPException(status_code=500, detail="删除日志失败，请查看后端日志")
