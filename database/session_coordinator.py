"""Per-session ordering across tasks and, optionally, application instances."""
from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from database.db import get_async_engine, resolve_database


class SessionLockTimeoutError(TimeoutError):
    pass


class SessionCoordinator(Protocol):
    distributed: bool

    def lock(self, session_key: str): ...

    def stats(self) -> dict[str, Any]: ...


@dataclass(slots=True)
class _LocalLockEntry:
    lock: asyncio.Lock
    users: int = 0


class LocalSessionCoordinator:
    """Keyed task locks that remove idle entries instead of growing forever."""

    distributed = False

    def __init__(self):
        self._entries: dict[str, _LocalLockEntry] = {}
        self._guard = asyncio.Lock()
        self._acquisitions = 0
        self._contentions = 0

    @asynccontextmanager
    async def lock(self, session_key: str) -> AsyncIterator[None]:
        async with self._guard:
            entry = self._entries.get(session_key)
            if entry is None:
                entry = _LocalLockEntry(asyncio.Lock())
                self._entries[session_key] = entry
            entry.users += 1
            if entry.lock.locked():
                self._contentions += 1

        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            self._acquisitions += 1
            yield
        finally:
            if acquired:
                entry.lock.release()
            async with self._guard:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(session_key, None)

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "local",
            "distributed": False,
            "active_keys": len(self._entries),
            "acquisitions": self._acquisitions,
            "contentions": self._contentions,
            "timeouts": 0,
        }


class PostgreSQLSessionCoordinator:
    """Transaction-scoped PostgreSQL advisory locks keyed by session identity."""

    distributed = True

    def __init__(
        self,
        database_url: str,
        *,
        timeout: float = 30.0,
        engine: AsyncEngine | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
    ):
        if resolve_database(database_url).backend != "postgresql":
            raise ValueError("PostgreSQLSessionCoordinator requires a PostgreSQL URL")
        self._engine = engine or get_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_namespace="session-coordinator",
        )
        self._timeout = timeout
        self._acquisitions = 0
        self._contentions = 0
        self._timeouts = 0
        self._active = 0

    @staticmethod
    def lock_key(session_key: str) -> int:
        digest = hashlib.blake2b(
            session_key.encode("utf-8"),
            digest_size=8,
            person=b"chatbot",
        ).digest()
        return int.from_bytes(digest, byteorder="big", signed=True)

    @asynccontextmanager
    async def lock(self, session_key: str) -> AsyncIterator[None]:
        lock_key = self.lock_key(session_key)
        async with self._engine.begin() as connection:
            immediate = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )
            )
            if not immediate:
                self._contentions += 1
                try:
                    await asyncio.wait_for(
                        connection.scalar(
                            text("SELECT pg_advisory_xact_lock(:lock_key)"),
                            {"lock_key": lock_key},
                        ),
                        timeout=self._timeout,
                    )
                except TimeoutError as exc:
                    self._timeouts += 1
                    raise SessionLockTimeoutError(
                        f"timed out acquiring distributed lock for session {session_key!r}"
                    ) from exc

            self._acquisitions += 1
            self._active += 1
            try:
                yield
            finally:
                self._active = max(0, self._active - 1)

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "postgresql_advisory_xact_lock",
            "distributed": True,
            "active_keys": self._active,
            "acquisitions": self._acquisitions,
            "contentions": self._contentions,
            "timeouts": self._timeouts,
        }


def create_session_coordinator(config: Any) -> SessionCoordinator:
    if not config.multi_instance:
        return LocalSessionCoordinator()
    return PostgreSQLSessionCoordinator(
        config.database_target,
        timeout=config.lock_timeout,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout,
    )
