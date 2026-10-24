from __future__ import annotations

import logging
import sqlite3
import tomllib
from pathlib import Path

import pytest

import database.migrate as migration_cli
from database.migrate import MigrationExitCode, build_parser, load_migration_options, main


def test_cli_applies_sqlite_migrations_and_exits_successfully(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATBOT_DATABASE_URL", raising=False)
    database = tmp_path / "chatbot.db"

    arguments = ["--database", str(database), "--log-level", "ERROR"]
    exit_code = main(arguments)
    repeated_exit_code = main(arguments)

    assert exit_code == MigrationExitCode.SUCCESS
    assert repeated_exit_code == MigrationExitCode.SUCCESS
    with sqlite3.connect(database) as connection:
        applied = connection.execute("SELECT name, checksum FROM _migrations ORDER BY name").fetchall()
        session_columns = {row[1] for row in connection.execute("PRAGMA table_info(chat_sessions)").fetchall()}
    assert [name for name, _checksum in applied] == [
        "001_init.sql",
        "002_chat_sessions.sql",
        "003_session_revision_and_entity_identity.sql",
        "004_durable_execution.sql",
        "005_persistence_window_attachments.sql",
        "006_persona.sql",
        "007_llm_usage.sql",
    ]
    assert all(len(checksum) == 64 for _name, checksum in applied)
    assert "revision" in session_columns


def test_cli_resolves_cli_environment_and_persistence_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "migration.yaml"
    config.write_text(
        """
persistence:
  db_path: from-config.db
  pool_size: 7
  max_overflow: 4
  pool_timeout: 12.5
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("CHATBOT_DATABASE_URL", "postgresql://env-user:env-secret@db/env-db")
    options = load_migration_options(build_parser().parse_args(["--config", str(config)]))
    assert options.database == "postgresql://env-user:env-secret@db/env-db"
    assert options.pool_size == 7
    assert options.max_overflow == 4
    assert options.pool_timeout == 12.5
    assert options.migration_timeout == 300.0

    cli_database = str(tmp_path / "from-cli.db")
    options = load_migration_options(
        build_parser().parse_args(
            [
                "--config",
                str(config),
                "--database",
                cli_database,
                "--pool-size",
                "9",
            ]
        )
    )
    assert options.database == cli_database
    assert options.pool_size == 9


def test_cli_loads_explicit_env_file_without_runtime_config_validation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATBOT_DATABASE_URL", raising=False)
    config = tmp_path / "migration.yaml"
    config.write_text("persistence:\n  db_path: fallback.db\n", encoding="utf-8")
    env_file = tmp_path / "migration.env"
    env_file.write_text(
        "CHATBOT_DATABASE_URL=postgresql://migration-user:private@database/chatbot\n",
        encoding="utf-8",
    )

    options = load_migration_options(build_parser().parse_args(["--config", str(config), "--env-file", str(env_file)]))

    assert options.database == "postgresql://migration-user:private@database/chatbot"
    assert options.location.backend == "postgresql"


@pytest.mark.parametrize(
    ("arguments", "expected_fragment"),
    [
        (["--config", "missing.yaml"], "config file does not exist"),
        (["--env-file", "missing.env"], "env file does not exist"),
        (["--database", "mysql://user:secret@db/chatbot"], "only SQLite and PostgreSQL"),
        (["--database", "data/chatbot.db", "--pool-size", "0"], "pool_size"),
    ],
)
def test_cli_returns_configuration_exit_code_without_echoing_database_secret(
    arguments,
    expected_fragment,
    tmp_path,
    monkeypatch,
    caplog,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATBOT_DATABASE_URL", raising=False)
    caplog.set_level(logging.ERROR, logger="database.migrate")

    exit_code = main(arguments)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert exit_code == MigrationExitCode.CONFIGURATION_ERROR
    assert expected_fragment in messages
    assert "secret" not in messages


def test_cli_failure_redacts_raw_encoded_and_query_secrets(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATBOT_DATABASE_URL", raising=False)
    encoded_secret = "p%40ss%2Fword"
    raw_secret = "p@ss/word"
    query_secret = "query-secret"
    arbitrary_query_secret = "arbitrary-query-key"
    database = (
        f"postgresql+asyncpg://chatbot:{encoded_secret}@db.example/chatbot"
        f"?password={query_secret}&token={query_secret}&api_key={arbitrary_query_secret}"
    )
    calls: list[str] = []

    async def fail_migration(received_database, **_pool_options):
        calls.append(received_database)
        raise RuntimeError(f"connection failed: {database}; password={raw_secret}; token={query_secret}")

    async def dispose(received_database):
        calls.append(received_database)

    monkeypatch.setattr(migration_cli, "run_migrations", fail_migration)
    monkeypatch.setattr(migration_cli, "dispose_db", dispose)
    caplog.set_level(logging.INFO, logger="database.migrate")

    exit_code = main(["--database", database])

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert exit_code == MigrationExitCode.MIGRATION_FAILED
    assert calls == [database, database]
    assert encoded_secret not in messages
    assert raw_secret not in messages
    assert query_secret not in messages
    assert arbitrary_query_secret not in messages
    assert "postgresql+asyncpg://chatbot:***@db.example/chatbot" in messages
    assert "RuntimeError" in messages


def test_cli_times_out_a_stuck_migration_and_still_disposes(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHATBOT_DATABASE_URL", raising=False)
    disposed: list[str] = []

    async def stuck_migration(_database, **_pool_options):
        await migration_cli.asyncio.Event().wait()

    async def dispose(database):
        disposed.append(database)

    monkeypatch.setattr(migration_cli, "run_migrations", stuck_migration)
    monkeypatch.setattr(migration_cli, "dispose_db", dispose)
    caplog.set_level(logging.ERROR, logger="database.migrate")

    exit_code = main(["--database", str(tmp_path / "timeout.db"), "--migration-timeout", "0.01"])

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert exit_code == MigrationExitCode.MIGRATION_FAILED
    assert disposed == [str(tmp_path / "timeout.db")]
    assert "TimeoutError" in messages


@pytest.mark.parametrize(
    "arguments", [["--not-an-option"], ["--migration-timeout", "0"], ["--migration-timeout", "nan"]]
)
def test_cli_uses_standard_usage_exit_code_for_bad_arguments(arguments):
    with pytest.raises(SystemExit) as exc_info:
        main(arguments)
    assert exc_info.value.code == MigrationExitCode.USAGE_ERROR


def test_packaged_console_script_targets_migration_entrypoint():
    with open(Path(__file__).parents[1] / "pyproject.toml", "rb") as source:
        project = tomllib.load(source)["project"]
    assert project["scripts"]["chatbot-migrate"] == "database.migrate:entrypoint"
