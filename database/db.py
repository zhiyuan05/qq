"""Async database engine registry and dialect-aware schema bootstrap."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base
from utils.logger import setup_logger

logger = setup_logger("db")
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class UnsupportedDatabaseError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DatabaseLocation:
    url: URL
    key: str
    backend: str
    display_name: str
    sqlite_path: str | None = None


_engines: dict[tuple[str, str], AsyncEngine] = {}
_async_session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}


def resolve_database(database: str = "data/chatbot.db") -> DatabaseLocation:
    """Normalize a legacy SQLite path or an async SQLAlchemy URL."""

    value = str(database).strip() or "data/chatbot.db"
    if "://" not in value:
        path = str(Path(value).resolve())
        url = URL.create("sqlite+aiosqlite", database=Path(path).as_posix())
        return DatabaseLocation(
            url=url,
            key=url.render_as_string(hide_password=False),
            backend="sqlite",
            display_name=path,
            sqlite_path=path,
        )

    try:
        url = make_url(value)
    except Exception as exc:
        raise UnsupportedDatabaseError("persistence database URL is invalid") from exc

    driver = url.drivername.lower()
    if driver in {"sqlite", "sqlite+pysqlite"}:
        url = url.set(drivername="sqlite+aiosqlite")
        backend = "sqlite"
    elif driver in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+asyncpg")
        backend = "postgresql"
    elif driver == "postgresql+asyncpg":
        backend = "postgresql"
    else:
        raise UnsupportedDatabaseError("only SQLite and PostgreSQL async database URLs are supported")

    sqlite_path = None
    if backend == "sqlite" and url.database not in {None, "", ":memory:"}:
        sqlite_path = str(Path(url.database).resolve())
        url = url.set(database=Path(sqlite_path).as_posix())
    return DatabaseLocation(
        url=url,
        key=url.render_as_string(hide_password=False),
        backend=backend,
        display_name=url.render_as_string(hide_password=True),
        sqlite_path=sqlite_path,
    )


def _database_key(database: str) -> str:
    return resolve_database(database).key


def get_async_engine(
    database: str = "data/chatbot.db",
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: float = 30.0,
    pool_namespace: str = "default",
) -> AsyncEngine:
    location = resolve_database(database)
    namespace = pool_namespace.strip()
    if not namespace:
        raise ValueError("pool_namespace must not be empty")
    engine_key = (location.key, namespace)
    if engine_key in _engines:
        return _engines[engine_key]

    options: dict[str, Any] = {"pool_pre_ping": True}
    if location.backend == "sqlite":
        if location.sqlite_path:
            Path(location.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        options["connect_args"] = {"timeout": 30}
    else:
        options.update(pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout)

    engine = create_async_engine(location.url, **options)
    if location.backend == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

    _engines[engine_key] = engine
    return engine


def get_session_factory(
    database: str = "data/chatbot.db",
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: float = 30.0,
) -> async_sessionmaker[AsyncSession]:
    location = resolve_database(database)
    if location.key not in _async_session_factories:
        engine = get_async_engine(
            database,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )
        _async_session_factories[location.key] = async_sessionmaker(engine, expire_on_commit=False)
    return _async_session_factories[location.key]


def _migration_checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQLite migration without breaking strings or trigger bodies."""

    statements: list[str] = []
    buffer = ""
    for character in sql:
        buffer += character
        if character == ";" and sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        executable_tail = re.sub(r"--[^\n]*|/\*.*?\*/", "", buffer, flags=re.DOTALL).strip()
        if not executable_tail:
            return statements
        if not sqlite3.complete_statement(buffer):
            raise RuntimeError("数据库迁移包含不完整的 SQL 语句")
        statements.append(buffer.strip())
    return statements


async def _run_sqlite_migrations(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS _migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(200) NOT NULL UNIQUE,
                    checksum VARCHAR(64),
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        columns = await connection.execute(text("PRAGMA table_info(_migrations)"))
        if "checksum" not in {row[1] for row in columns.fetchall()}:
            await connection.execute(text("ALTER TABLE _migrations ADD COLUMN checksum VARCHAR(64)"))

        result = await connection.execute(text("SELECT name, checksum FROM _migrations"))
        applied = {row[0]: row[1] for row in result.fetchall()}
        if MIGRATIONS_DIR.exists():
            migrations = sorted(path for path in os.listdir(MIGRATIONS_DIR) if path.endswith(".sql"))
            for migration in migrations:
                sql = (MIGRATIONS_DIR / migration).read_text("utf-8")
                checksum = _migration_checksum(sql)
                if migration in applied:
                    previous_checksum = applied[migration]
                    if previous_checksum and previous_checksum != checksum:
                        raise RuntimeError(f"已应用的数据库迁移被修改: {migration}")
                    if not previous_checksum:
                        await connection.execute(
                            text("UPDATE _migrations SET checksum = :checksum WHERE name = :name"),
                            {"name": migration, "checksum": checksum},
                        )
                    continue

                for statement in _split_sql_statements(sql):
                    # SQLite has no portable ADD COLUMN IF NOT EXISTS. Reconcile
                    # transitional schemas where metadata bootstrap already
                    # created the column (or the table itself) and still record
                    # the immutable migration checksum below.
                    alter = re.search(
                        r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
                        statement,
                        flags=re.IGNORECASE,
                    )
                    if alter:
                        table_name, column_name = alter.group(1), alter.group(2)
                        table_columns = await connection.execute(text(f"PRAGMA table_info({table_name})"))
                        column_names = {row[1] for row in table_columns.fetchall()}
                        if not column_names or column_name in column_names:
                            continue
                    await connection.execute(text(statement))
                await connection.execute(
                    text("INSERT INTO _migrations (name, checksum) VALUES (:name, :checksum)"),
                    {"name": migration, "checksum": checksum},
                )
                logger.info("数据库迁移已应用: %s", migration)
        await connection.run_sync(Base.metadata.create_all)


async def _run_postgresql_migrations(engine: AsyncEngine) -> None:
    """Bootstrap the first supported PostgreSQL schema and upgrade its CAS column."""

    async with engine.begin() as connection:
        # Serialize DDL when several replicas boot against an empty database.
        await connection.execute(text("SELECT pg_advisory_xact_lock(724316520241903437)"))
        await connection.run_sync(Base.metadata.create_all)
        # PostgreSQL support starts at schema v3. These idempotent statements also
        # repair databases created by pre-release builds.
        await connection.execute(
            text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1")
        )
        await connection.execute(
            text("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS summary_until_id INTEGER NOT NULL DEFAULT 0")
        )
        await connection.execute(
            text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS attachments_json TEXT NOT NULL DEFAULT '[]'")
        )
        await connection.execute(
            text(
                "DELETE FROM entity_memories newer USING entity_memories older "
                "WHERE newer.id < older.id "
                "AND newer.user_id = older.user_id "
                "AND newer.entity_type = older.entity_type "
                "AND newer.key = older.key"
            )
        )
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_user_type_key "
                "ON entity_memories(user_id, entity_type, key)"
            )
        )


async def run_migrations(
    database: str = "data/chatbot.db",
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: float = 30.0,
) -> None:
    location = resolve_database(database)
    engine = get_async_engine(
        database,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
    )
    if location.backend == "sqlite":
        await _run_sqlite_migrations(engine)
    else:
        await _run_postgresql_migrations(engine)


async def init_db(database: str = "data/chatbot.db", **pool_options: Any) -> None:
    await run_migrations(database, **pool_options)


async def check_db(database: str = "data/chatbot.db") -> bool:
    try:
        engine = get_async_engine(database)
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("数据库健康检查失败 (%s)", type(exc).__name__)
        return False


async def dispose_db(database: str | None = None) -> None:
    database_key = _database_key(database) if database else None
    keys = [key for key in _engines if database_key is None or key[0] == database_key]
    for key in keys:
        engine = _engines.pop(key, None)
        if engine is not None:
            await engine.dispose()
    if database_key is None:
        _async_session_factories.clear()
    else:
        _async_session_factories.pop(database_key, None)


async def get_db() -> AsyncSession:
    factory = get_session_factory()
    async with factory() as session:
        yield session
