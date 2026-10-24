"""One-shot database migration command for deployment jobs.

The command deliberately loads only the persistence section of the application
configuration.  A schema migration must not depend on LLM credentials, platform
connectivity, or the web process being able to start.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import NoReturn, Sequence
from urllib.parse import quote, quote_plus

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from database.db import DatabaseLocation, UnsupportedDatabaseError, dispose_db, resolve_database, run_migrations
from utils.config import PersistenceConfig
from utils.logger import setup_logger

logger = setup_logger("database.migrate")


class MigrationExitCode(IntEnum):
    """Stable process exit codes used by deployment automation."""

    SUCCESS = 0
    USAGE_ERROR = 2
    CONFIGURATION_ERROR = 3
    MIGRATION_FAILED = 4
    INTERRUPTED = 130


class MigrationConfigurationError(ValueError):
    """Raised when migration-only configuration cannot be loaded safely."""


@dataclass(frozen=True, slots=True)
class MigrationOptions:
    database: str = field(repr=False)
    location: DatabaseLocation = field(repr=False)
    pool_size: int
    max_overflow: int
    pool_timeout: float
    migration_timeout: float


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chatbot-migrate",
        description="Apply pending chatbot database migrations once and exit.",
    )
    parser.add_argument(
        "--database",
        metavar="URL_OR_PATH",
        help="SQLite path or PostgreSQL URL; overrides CHATBOT_DATABASE_URL and persistence config.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="YAML config to read. Defaults to config.yaml, then config/config.yaml when present.",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="dotenv file to load without overriding the process environment. Defaults to .env when present.",
    )
    parser.add_argument("--pool-size", type=int, help="PostgreSQL connection pool size.")
    parser.add_argument("--max-overflow", type=int, help="PostgreSQL pool overflow limit.")
    parser.add_argument("--pool-timeout", type=float, help="PostgreSQL pool checkout timeout in seconds.")
    parser.add_argument(
        "--migration-timeout",
        type=_positive_seconds,
        default=300.0,
        metavar="SECONDS",
        help="Maximum duration of the migration transaction (default: 300).",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Migration log level (default: INFO).",
    )
    return parser


def _load_environment(explicit_path: str | None) -> None:
    path = Path(explicit_path) if explicit_path else Path(".env")
    if explicit_path and not path.is_file():
        raise MigrationConfigurationError(f"env file does not exist: {path}")
    if path.is_file():
        load_dotenv(dotenv_path=path, override=False, encoding="utf-8")


def _select_config_path(explicit_path: str | None) -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_file():
            raise MigrationConfigurationError(f"config file does not exist: {path}")
        return path

    for candidate in (Path("config.yaml"), Path("config/config.yaml")):
        if candidate.is_file():
            return candidate
    return None


def _read_persistence_config(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise MigrationConfigurationError(f"cannot read config file {path} ({type(exc).__name__})") from exc
    if not isinstance(raw, dict):
        raise MigrationConfigurationError("config root must be a mapping")
    persistence = raw.get("persistence", {}) or {}
    if not isinstance(persistence, dict):
        raise MigrationConfigurationError("persistence config must be a mapping")
    return dict(persistence)


def _validation_fields(exc: ValidationError) -> str:
    fields = {
        ".".join(str(part) for part in item.get("loc", ())) or "persistence"
        for item in exc.errors(include_url=False, include_context=False, include_input=False)
    }
    return ", ".join(sorted(fields)) or "persistence"


def load_migration_options(args: argparse.Namespace) -> MigrationOptions:
    """Resolve CLI, environment, and YAML settings without loading runtime config."""

    _load_environment(args.env_file)
    persistence_values = _read_persistence_config(_select_config_path(args.config))

    environment_database = os.getenv("CHATBOT_DATABASE_URL", "").strip()
    if environment_database:
        persistence_values["database_url"] = environment_database
    if args.database is not None:
        if not args.database.strip():
            raise MigrationConfigurationError("--database must not be empty")
        persistence_values["database_url"] = args.database.strip()
    if args.pool_size is not None:
        persistence_values["pool_size"] = args.pool_size
    if args.max_overflow is not None:
        persistence_values["max_overflow"] = args.max_overflow
    if args.pool_timeout is not None:
        persistence_values["pool_timeout"] = args.pool_timeout

    # Validate a textual target before Pydantic's model-level backend check so
    # operators get a precise, value-free error for an unsupported driver.
    candidate_url = persistence_values.get("database_url", "")
    candidate_path = persistence_values.get("db_path", "data/chatbot.db")
    candidate_database = candidate_url or candidate_path
    if isinstance(candidate_database, str):
        try:
            resolve_database(candidate_database)
        except UnsupportedDatabaseError as exc:
            raise MigrationConfigurationError(str(exc)) from exc

    try:
        persistence = PersistenceConfig(**persistence_values)
    except ValidationError as exc:
        raise MigrationConfigurationError(f"invalid persistence config fields: {_validation_fields(exc)}") from exc

    database = persistence.database_target
    try:
        location = resolve_database(database)
    except UnsupportedDatabaseError as exc:
        raise MigrationConfigurationError(str(exc)) from exc

    return MigrationOptions(
        database=database,
        location=location,
        pool_size=persistence.pool_size,
        max_overflow=persistence.max_overflow,
        pool_timeout=persistence.pool_timeout,
        migration_timeout=args.migration_timeout,
    )


def _safe_database_label(location: DatabaseLocation) -> str:
    if location.backend == "sqlite":
        return location.display_name
    # Query parameters can contain provider-specific credentials. They are not
    # needed to identify the database in an operational log.
    return location.url.set(query={}).render_as_string(hide_password=True)


_URL_PASSWORD_RE = re.compile(r"(?i)\b((?:postgres(?:ql)?|sqlite)(?:\+[a-z0-9_]+)?://[^:/@\s]+:)[^@\s/]+@")
_SECRET_PARAMETER_RE = re.compile(r"(?i)(\b(?:password|passwd|pwd|token|secret)\s*[=:]\s*)([^&\s,;]+)")


def _redact_error_detail(exc: BaseException, location: DatabaseLocation) -> str:
    """Return a bounded diagnostic without exposing URL credentials."""

    detail = str(exc).replace("\r", " ").replace("\n", " ")
    detail = _URL_PASSWORD_RE.sub(r"\1***@", detail)
    detail = _SECRET_PARAMETER_RE.sub(r"\1***", detail)

    sensitive_values: set[str] = set()
    if location.url.password:
        sensitive_values.add(location.url.password)
    for query_value in location.url.query.values():
        if isinstance(query_value, tuple):
            sensitive_values.update(query_value)
        else:
            sensitive_values.add(query_value)

    encoded_variants: set[str] = set()
    for value in sensitive_values:
        encoded_variants.update({value, quote(value, safe=""), quote_plus(value, safe="")})
    for value in sorted(encoded_variants, key=len, reverse=True):
        if value:
            detail = detail.replace(value, "***")

    # Keep deployment logs useful but prevent an unbounded server error from
    # copying arbitrary remote content into the log stream.
    return detail[:500] or "no diagnostic detail"


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name)
    for name in ("database.migrate", "db"):
        configured = logging.getLogger(name)
        configured.setLevel(level)
        for handler in configured.handlers:
            handler.setLevel(level)


async def execute_migration(options: MigrationOptions) -> MigrationExitCode:
    target = _safe_database_label(options.location)
    started_at = time.monotonic()
    result = MigrationExitCode.SUCCESS
    logger.info("数据库迁移开始 backend=%s target=%s", options.location.backend, target)
    try:
        async with asyncio.timeout(options.migration_timeout):
            await run_migrations(
                options.database,
                pool_size=options.pool_size,
                max_overflow=options.max_overflow,
                pool_timeout=options.pool_timeout,
            )
    except Exception as exc:
        result = MigrationExitCode.MIGRATION_FAILED
        logger.error(
            "数据库迁移失败 backend=%s target=%s error=%s detail=%s",
            options.location.backend,
            target,
            type(exc).__name__,
            _redact_error_detail(exc, options.location),
        )
    finally:
        try:
            await dispose_db(options.database)
        except Exception as exc:
            result = MigrationExitCode.MIGRATION_FAILED
            logger.error(
                "数据库连接清理失败 backend=%s target=%s error=%s",
                options.location.backend,
                target,
                type(exc).__name__,
            )

    if result == MigrationExitCode.SUCCESS:
        logger.info(
            "数据库迁移完成 backend=%s target=%s elapsed_seconds=%.3f",
            options.location.backend,
            target,
            time.monotonic() - started_at,
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)
    try:
        options = load_migration_options(args)
    except MigrationConfigurationError as exc:
        logger.error("数据库迁移配置错误: %s", exc)
        return int(MigrationExitCode.CONFIGURATION_ERROR)
    except Exception as exc:
        # dotenv and filesystem implementations can fail in platform-specific
        # ways. Keep the process contract stable and never echo their payload.
        logger.error("数据库迁移配置加载失败 error=%s", type(exc).__name__)
        return int(MigrationExitCode.CONFIGURATION_ERROR)

    try:
        return int(asyncio.run(execute_migration(options)))
    except KeyboardInterrupt:
        logger.warning("数据库迁移已中断")
        return int(MigrationExitCode.INTERRUPTED)
    except Exception as exc:
        # This covers event-loop/bootstrap failures before execute_migration can
        # apply its normal redaction path. Never include the exception message.
        logger.error("数据库迁移进程异常终止 error=%s", type(exc).__name__)
        return int(MigrationExitCode.MIGRATION_FAILED)


def entrypoint() -> NoReturn:
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
