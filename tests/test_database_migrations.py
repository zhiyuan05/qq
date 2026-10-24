import sqlite3

import pytest
from sqlalchemy import text

import database.db as database_db
from database.db import UnsupportedDatabaseError, dispose_db, get_async_engine, resolve_database, run_migrations


@pytest.mark.asyncio
async def test_migration_parser_preserves_semicolons_and_records_checksum(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    migration = migrations / "001_sample.sql"
    migration.write_text(
        "CREATE TABLE sample (value TEXT);\nINSERT INTO sample(value) VALUES ('a;b');\n-- trailing comment\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(database_db, "MIGRATIONS_DIR", migrations)
    db_path = str(tmp_path / "migration.db")

    try:
        await run_migrations(db_path)
        engine = get_async_engine(db_path)
        async with engine.connect() as connection:
            value = await connection.scalar(text("SELECT value FROM sample"))
            checksum = await connection.scalar(
                text("SELECT checksum FROM _migrations WHERE name = '001_sample.sql'")
            )
            foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
        assert value == "a;b"
        assert len(checksum) == 64
        assert foreign_keys == 1
        assert journal_mode == "wal"
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_modified_applied_migration_is_rejected(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    migration = migrations / "001_sample.sql"
    migration.write_text("CREATE TABLE sample (value TEXT);\n", encoding="utf-8")
    monkeypatch.setattr(database_db, "MIGRATIONS_DIR", migrations)
    db_path = str(tmp_path / "migration.db")

    try:
        await run_migrations(db_path)
        migration.write_text("CREATE TABLE sample (value INTEGER);\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="已应用的数据库迁移被修改"):
            await run_migrations(db_path)
    finally:
        await dispose_db(db_path)


def test_database_location_normalizes_legacy_paths_and_redacts_postgres_password(tmp_path):
    sqlite = resolve_database(str(tmp_path / "database.db"))
    postgres = resolve_database("postgres://chatbot:super-secret@db.example/chatbot")

    assert sqlite.backend == "sqlite"
    assert sqlite.url.drivername == "sqlite+aiosqlite"
    assert sqlite.sqlite_path == str((tmp_path / "database.db").resolve())
    assert postgres.backend == "postgresql"
    assert postgres.url.drivername == "postgresql+asyncpg"
    assert "super-secret" not in postgres.display_name
    assert "***" in postgres.display_name


def test_database_location_rejects_sync_or_unknown_backends():
    with pytest.raises(UnsupportedDatabaseError, match="only SQLite and PostgreSQL"):
        resolve_database("mysql://localhost/chatbot")


@pytest.mark.asyncio
async def test_named_engine_pools_are_isolated_and_disposed_together(tmp_path):
    db_path = str(tmp_path / "pool.db")
    default = get_async_engine(db_path)
    coordinator = get_async_engine(db_path, pool_namespace="session-coordinator")

    assert default is get_async_engine(db_path)
    assert coordinator is get_async_engine(db_path, pool_namespace="session-coordinator")
    assert coordinator is not default
    with pytest.raises(ValueError, match="pool_namespace"):
        get_async_engine(db_path, pool_namespace=" ")

    await dispose_db(db_path)
    assert get_async_engine(db_path) is not default
    await dispose_db(db_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("preexisting_revision", [False, True])
async def test_v3_migration_upgrades_legacy_schema_and_deduplicates_entities(tmp_path, preexisting_revision):
    db_path = str(tmp_path / "legacy.db")
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            f"""
            CREATE TABLE _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(200) NOT NULL UNIQUE,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO _migrations(name) VALUES ('001_init.sql'), ('002_chat_sessions.sql');

            CREATE TABLE chat_sessions (
                session_id VARCHAR(200) PRIMARY KEY,
                user_id VARCHAR(200) NOT NULL,
                platform VARCHAR(20) NOT NULL,
                character_card VARCHAR(100) DEFAULT 'default',
                style VARCHAR(50) DEFAULT 'casual',
                summary TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                {', revision INTEGER NOT NULL DEFAULT 1' if preexisting_revision else ''}
            );
            CREATE TABLE entity_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entity_type VARCHAR(50) NOT NULL,
                key VARCHAR(200) NOT NULL,
                value TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source VARCHAR(20) DEFAULT 'auto',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO entity_memories(user_id, entity_type, key, value)
            VALUES
                ('user-1', 'preference', 'language', 'Python'),
                ('user-1', 'preference', 'language', 'Rust');
            """
        )
        connection.commit()
    finally:
        connection.close()

    try:
        await run_migrations(db_path)
        engine = get_async_engine(db_path)
        async with engine.connect() as async_connection:
            session_columns = {
                row[1]: row for row in (await async_connection.execute(text("PRAGMA table_info(chat_sessions)")))
            }
            indexes = (await async_connection.execute(text("PRAGMA index_list(entity_memories)"))).fetchall()
            entities = (
                await async_connection.execute(
                    text(
                        "SELECT user_id, entity_type, key, value "
                        "FROM entity_memories ORDER BY id"
                    )
                )
            ).fetchall()
            applied_v3 = await async_connection.scalar(
                text("SELECT checksum FROM _migrations WHERE name = '003_session_revision_and_entity_identity.sql'")
            )

        assert session_columns["revision"][3] == 1
        assert session_columns["revision"][4] == "1"
        assert any(row[1] == "uq_entity_user_type_key" and row[2] == 1 for row in indexes)
        assert entities == [("user-1", "preference", "language", "Rust")]
        assert len(applied_v3) == 64
    finally:
        await dispose_db(db_path)
