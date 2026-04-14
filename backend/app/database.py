import os
from typing import AsyncGenerator
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# --- 全局变量，将在应用启动时被初始化 ---
engine = None
AsyncSessionLocal = None
_CURRENT_DB_URL = None
Base = declarative_base()

def init_db(db_url: str = None):
    """
    初始化 PostgreSQL 数据库连接。
    """
    global engine, AsyncSessionLocal, _CURRENT_DB_URL

    # 1. 确定数据库 URL
    database_url = db_url or os.environ.get('DATABASE_URL')
    
    if not database_url:
        raise RuntimeError("未设置 DATABASE_URL 环境变量。请配置 PostgreSQL 连接字符串 (postgresql+asyncpg://...)。")

    if engine is not None and AsyncSessionLocal is not None:
        if db_url is None or _CURRENT_DB_URL == database_url:
            return
        raise RuntimeError("数据库连接已初始化，且 DATABASE_URL 不一致。请重启服务以应用新的配置。")

    if "postgresql" not in database_url:
        raise ValueError("仅支持 PostgreSQL 数据库。")

    print(f"数据库正在初始化，连接至: {urlparse(database_url).hostname}")

    # 2. 创建异步引擎
    # 配置高性能连接池
    engine = create_async_engine(
        database_url,
        echo=False,
        future=True,
        pool_size=20,
        max_overflow=10,
        pool_recycle=3600,
        pool_pre_ping=True
    )
    
    AsyncSessionLocal = async_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _CURRENT_DB_URL = database_url

# 创建一个 FastAPI 依赖项，用于在每个请求中获取数据库会话
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a database session for a single request.
    """
    if AsyncSessionLocal is None:
        raise RuntimeError("数据库未初始化。请在应用启动时调用 init_db()。")

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
