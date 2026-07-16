"""数据库连接管理"""
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from database.models import Base

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


async def init_db(db_path: str = "data/chatbot.db"):
    """创建所有表"""
    engine = get_async_engine(db_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    """获取数据库会话"""
    factory = get_session_factory()
    async with factory() as session:
        yield session
