import uuid
import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import json
import sys

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, text

from ..config import read_int_env
from ..models import SystemTaskORM, TaskLogORM
from .. import database

# --- Configuration ---
# 任务超时时间 (分钟)。如果任务超过此时间未更新，视为僵尸任务。
# ENVI 任务单步可能超过 60 分钟，默认设为 120 分钟，可通过环境变量覆盖。
TASK_TIMEOUT_MINUTES = read_int_env("TASK_TIMEOUT_MINUTES", 120, minimum=10, maximum=1440)
TASK_ACTIVE_DEFAULT_LIMIT = read_int_env(
    "TASK_ACTIVE_DEFAULT_LIMIT",
    100,
    minimum=1,
    maximum=2000,
)
TASK_ACTIVE_MAX_LIMIT = read_int_env(
    "TASK_ACTIVE_MAX_LIMIT",
    500,
    minimum=1,
    maximum=10000,
)
TASK_LOG_DEFAULT_LIMIT = read_int_env(
    "TASK_LOG_DEFAULT_LIMIT",
    100,
    minimum=1,
    maximum=5000,
)
TASK_LOG_MAX_LIMIT = read_int_env(
    "TASK_LOG_MAX_LIMIT",
    1000,
    minimum=1,
    maximum=20000,
)
TASK_QUERY_MAX_OFFSET = read_int_env(
    "TASK_QUERY_MAX_OFFSET",
    500000,
    minimum=0,
    maximum=20000000,
)


def _clamp_pagination(limit: int, offset: int, *, default_limit: int, max_limit: int) -> tuple[int, int]:
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        parsed_limit = default_limit
    try:
        parsed_offset = int(offset)
    except (TypeError, ValueError):
        parsed_offset = 0

    safe_limit = min(max_limit, max(1, parsed_limit))
    safe_offset = min(TASK_QUERY_MAX_OFFSET, max(0, parsed_offset))
    return safe_limit, safe_offset


def _task_type_lock_key(task_type: str) -> int:
    normalized = (task_type or "").strip().lower().encode("utf-8")
    digest = hashlib.sha256(normalized).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _is_postgresql_session(db: AsyncSession) -> bool:
    try:
        bind = db.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        return (dialect_name or "").lower() == "postgresql"
    except Exception as exc:
        print(f"[WARN] _is_postgresql: {exc}")
        return False


def get_db_session():
    """
    获取数据库会话的辅助函数。
    如果数据库尚未初始化，则尝试初始化。
    """
    if database.AsyncSessionLocal is None:
        print(">>> [TaskService] 检测到 AsyncSessionLocal 未初始化，正在尝试重新初始化数据库...")
        try:
            database.init_db()
            print(">>> [TaskService] 数据库初始化成功。")
        except Exception as e:
            print(f">>> [TaskService] 数据库初始化失败: {e}")
            raise

    if database.AsyncSessionLocal is None:
        raise RuntimeError("无法获取数据库会话，请检查数据库连接配置。")

    return database.AsyncSessionLocal()


class TaskService:
    """
    持久化任务服务。
    负责管理任务的生命周期、日志记录和状态持久化。
    """

    def __init__(self):
        pass

    async def create_task(
        self,
        task_type: str,
        task_name: str,
        params: Optional[Dict[str, Any]] = None,
        db: Optional[AsyncSession] = None
    ) -> str:
        """
        创建新任务。
        实现逻辑：检查是否存在同类型且运行中的任务。如果存在，则抛出异常。
        """
        gen_db = db is None

        if gen_db:
            db = get_db_session()

        try:
            # 1. 并发冲突检查：是否已有同类型的任务在运行？
            if _is_postgresql_session(db):
                lock_key = _task_type_lock_key(task_type)
                await db.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )

            status_check = await db.execute(
                select(SystemTaskORM)
                .where(
                    and_(
                        SystemTaskORM.task_type == task_type,
                        SystemTaskORM.status.in_(["PENDING", "RUNNING"])
                    )
                )
            )
            existing_task = status_check.scalar_one_or_none()

            if existing_task:
                raise ValueError(f"任务冲突: 已存在一个正在运行的 {task_type} 任务 (ID: {existing_task.task_id})。请等待该任务完成。")

            # 2. 创建新任务记录
            task_id = str(uuid.uuid4())
            new_task = SystemTaskORM(
                task_id=task_id,
                task_type=task_type,
                task_name=task_name,
                status="PENDING",
                params=params
            )
            db.add(new_task)

            # 3. 记录创建日志
            log_entry = TaskLogORM(
                task_id=task_id,
                log_level="INFO",
                message=f"任务已创建: {task_name} ({task_type})"
            )
            db.add(log_entry)

            await db.commit()
            return task_id

        except Exception as e:
            await db.rollback()
            raise e
        finally:
            if gen_db:
                await db.close()

    async def start_task(self, task_id: str, message: str = "任务开始执行", db: Optional[AsyncSession] = None):
        """将任务状态更新为 RUNNING"""
        gen_db = db is None
        if gen_db:
            db = get_db_session()

        try:
            result = await db.execute(
                select(SystemTaskORM).where(SystemTaskORM.task_id == task_id)
            )
            task = result.scalar_one_or_none()

            if task:
                task.status = "RUNNING"
                task.message = message
                task.started_at = datetime.now()
                await self._add_log(task_id, "INFO", "任务已开始", db=db)
                await db.commit()
            else:
                raise ValueError(f"任务未找到: {task_id}")
        finally:
            if gen_db:
                await db.close()

    async def update_task(
        self,
        task_id: str,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        db: Optional[AsyncSession] = None
    ):
        """
        更新任务状态。
        如果状态变为 COMPLETED 或 FAILED，标记结束时间。
        """
        gen_db = db is None
        if gen_db:
            db = get_db_session()

        try:
            result = await db.execute(
                select(SystemTaskORM).where(SystemTaskORM.task_id == task_id)
            )
            task = result.scalar_one_or_none()

            if task:
                if status:
                    task.status = status
                if progress is not None:
                    task.progress = progress
                if message:
                    task.message = message

                # 如果任务结束，更新结束时间
                if status in ["COMPLETED", "FAILED", "CANCELLED"]:
                    task.ended_at = datetime.now()
                    await self._add_log(task_id, "INFO", f"任务已结束: {status}", db=db)
                else:
                    # 显式更新心跳时间，防止 SQLAlchemy 因属性未变而跳过 UPDATE
                    task.updated_at = datetime.now()

                await db.commit()
            else:
                print(f"警告: 尝试更新一个不存在的任务 {task_id}")
        finally:
            if gen_db:
                await db.close()

    async def get_active_tasks(
        self,
        limit: int = TASK_ACTIVE_DEFAULT_LIMIT,
        offset: int = 0,
        db: Optional[AsyncSession] = None,
    ) -> List[SystemTaskORM]:
        """
        获取所有非结束状态的任务。
        注意：这里应该加上超时检测逻辑，如果 updated_at 太旧，应该标记为 FAILED。
        """
        gen_db = db is None
        if gen_db:
            db = get_db_session()

        try:
            safe_limit, safe_offset = _clamp_pagination(
                limit,
                offset,
                default_limit=TASK_ACTIVE_DEFAULT_LIMIT,
                max_limit=TASK_ACTIVE_MAX_LIMIT,
            )

            # 1. 查找僵尸任务并标记为失败
            timeout_threshold = datetime.now() - timedelta(minutes=TASK_TIMEOUT_MINUTES)
            result = await db.execute(
                select(SystemTaskORM).where(
                    and_(
                        SystemTaskORM.status == "RUNNING",
                        SystemTaskORM.updated_at < timeout_threshold
                    )
                )
            )
            zombie_tasks = result.scalars().all()

            for task in zombie_tasks:
                task.status = "FAILED"
                task.message = "系统检测超时: 任务被认为已失效 (心跳超时)"
                log = TaskLogORM(task_id=task.task_id, log_level="WARNING", message="任务因超时被自动标记为失败")
                db.add(log)

            if zombie_tasks:
                await db.commit()

            # 2. 获取活跃任务
            active_result = await db.execute(
                select(SystemTaskORM)
                .where(SystemTaskORM.status.in_(["PENDING", "RUNNING"]))
                .order_by(SystemTaskORM.created_at.desc())
                .offset(safe_offset)
                .limit(safe_limit)
            )
            return active_result.scalars().all()

        finally:
            if gen_db:
                await db.close()

    async def get_task(self, task_id: str, db: Optional[AsyncSession] = None) -> Optional[SystemTaskORM]:
        gen_db = db is None
        if gen_db:
            db = get_db_session()
        try:
            result = await db.execute(
                select(SystemTaskORM).where(SystemTaskORM.task_id == task_id)
            )
            return result.scalar_one_or_none()
        finally:
            if gen_db:
                await db.close()

    async def get_logs(
        self,
        task_id: str,
        limit: int = TASK_LOG_DEFAULT_LIMIT,
        offset: int = 0,
        db: Optional[AsyncSession] = None,
    ) -> List[TaskLogORM]:
        """获取任务日志"""
        gen_db = db is None
        if gen_db:
            db = get_db_session()

        try:
            safe_limit, safe_offset = _clamp_pagination(
                limit,
                offset,
                default_limit=TASK_LOG_DEFAULT_LIMIT,
                max_limit=TASK_LOG_MAX_LIMIT,
            )
            result = await db.execute(
                select(TaskLogORM)
                .where(TaskLogORM.task_id == task_id)
                .order_by(TaskLogORM.timestamp.desc())
                .offset(safe_offset)
                .limit(safe_limit)
            )
            # 返回正序列表以便阅读
            logs = result.scalars().all()
            return list(reversed(logs))
        finally:
            if gen_db:
                await db.close()

    async def _add_log(self, task_id: str, level: str, message: str, db: AsyncSession):
        """内部方法：添加日志"""
        log_entry = TaskLogORM(task_id=task_id, log_level=level, message=message)
        db.add(log_entry)
        # 不在这里 commit，由上层控制事务

    async def add_log(self, task_id: str, level: str, message: str):
        """公开方法：添加日志 (带独立事务)"""
        db = get_db_session()
        try:
            await self._add_log(task_id, level, message, db=db)
            await db.commit()
        finally:
            await db.close()


# 创建全局单例实例
task_service = TaskService()
