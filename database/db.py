"""数据库连接管理"""
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from database.models import Base
from utils.logger import setup_logger

logger = setup_logger("db")
_engine = None
_async_session_factory = None


def get_async_engine(db_path: str = "data/chatbot.db"):
    global _engine
    if _engine is None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    return _engine


def get_session_factory(db_path: str = "data/chatbot.db"):
    global _async_session_factory
    if _async_session_factory is None:
        engine = get_async_engine(db_path)
        _async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _async_session_factory


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(db_path: str = "data/chatbot.db"):
    """运行数据库迁移"""
    engine = get_async_engine(db_path)
    async with engine.begin() as conn:
        # 创建迁移跟踪表
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(200) NOT NULL UNIQUE,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # 获取已应用的迁移
        result = await conn.execute(text("SELECT name FROM _migrations"))
        applied = {row[0] for row in result.fetchall()}

        # 按顺序应用未运行的迁移
        if MIGRATIONS_DIR.exists():
            migrations = sorted(f for f in os.listdir(MIGRATIONS_DIR) if f.endswith(".sql"))
            for m in migrations:
                if m not in applied:
                    sql = (MIGRATIONS_DIR / m).read_text("utf-8")
                    for statement in sql.split(";"):
                        stmt = statement.strip()
                        if stmt:
                            await conn.execute(text(stmt))
                    await conn.execute(text("INSERT INTO _migrations (name) VALUES (:name)"), {"name": m})
                    logger.info(f"数据库迁移已应用: {m}")

        # 创建所有表（兼容旧版）
        await conn.run_sync(Base.metadata.create_all)


async def init_db(db_path: str = "data/chatbot.db"):
    """初始化数据库（创建表 + 运行迁移）"""
    await run_migrations(db_path)


async def get_db() -> AsyncSession:
    """获取数据库会话"""
    factory = get_session_factory()
    async with factory() as session:
        yield session
