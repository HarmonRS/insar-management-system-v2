"""
Alembic 迁移环境配置。

使用说明：
  # 生成新迁移（自动检测模型变更）
  cd backend
  alembic revision --autogenerate -m "描述变更内容"

  # 执行迁移
  alembic upgrade head

  # 回滚一步
  alembic downgrade -1

  # 标记当前数据库为最新（首次引入 Alembic 时使用）
  alembic stamp head

注意：首次使用时，由于数据库已由 create_all 创建，请先运行 `alembic stamp head`
      将当前状态标记为基线，之后再用 alembic 管理后续变更。
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# 将 backend 目录加入 sys.path，使 app 包可被导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

# 导入所有 ORM 模型，确保它们注册到 Base.metadata
from app.database import Base  # noqa: E402
from app import models  # noqa: E402, F401

config = context.config

# 从环境变量动态注入数据库 URL（将 asyncpg 驱动替换为 psycopg 同步驱动）
_async_url = os.environ.get("DATABASE_URL", "")
_sync_url = _async_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
if not _sync_url:
    raise RuntimeError("DATABASE_URL 环境变量未设置，无法运行 Alembic 迁移。")
config.set_main_option("sqlalchemy.url", _sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：仅生成 SQL 脚本，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库并执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
