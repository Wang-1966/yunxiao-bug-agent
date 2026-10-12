"""数据库连接和会话管理"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
from loguru import logger

from ..config import get_settings

settings = get_settings()

# 创建异步引擎
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# 声明基类
Base = declarative_base()


async def init_db():
    """初始化数据库"""
    import aiosqlite
    import os

    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # 读取并执行 SQL 脚本
    sql_file = os.path.join(os.path.dirname(__file__), "init.sql")
    with open(sql_file, "r", encoding="utf-8") as f:
        sql_script = f.read()

    async with aiosqlite.connect(db_path) as db:
        await db.executescript(sql_script)
        cursor = await db.execute("PRAGMA table_info(bugs)")
        columns = {row[1] for row in await cursor.fetchall()}
        for column in ("source_status", "source_project", "source_project_id", "test_command"):
            if column not in columns:
                await db.execute(f"ALTER TABLE bugs ADD COLUMN {column} TEXT")
        from ..core.projects import project_id_from_url
        cursor = await db.execute("SELECT bug_id, source_url FROM bugs WHERE source='yunxiao' AND source_project_id IS NULL")
        for bug_id, url in await cursor.fetchall():
            project_id = project_id_from_url(url)
            if project_id:
                await db.execute("UPDATE bugs SET source_project_id=? WHERE bug_id=?", (project_id, bug_id))
        await db.commit()

    logger.info("Database initialized successfully")


@asynccontextmanager
async def get_db():
    """获取数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
