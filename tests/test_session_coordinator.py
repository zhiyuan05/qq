"""Local and PostgreSQL session-ordering contract tests."""
from __future__ import annotations

import asyncio

import pytest

from database.session_coordinator import (
    LocalSessionCoordinator,
    PostgreSQLSessionCoordinator,
    SessionLockTimeoutError,
)


@pytest.mark.asyncio
async def test_local_coordinator_serializes_same_key_but_not_different_keys():
    coordinator = LocalSessionCoordinator()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first():
        async with coordinator.lock("same"):
            first_entered.set()
            await release_first.wait()

    async def second():
        await first_entered.wait()
        async with coordinator.lock("same"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())
    await first_entered.wait()
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    async with coordinator.lock("different"):
        assert coordinator.stats()["active_keys"] == 2

    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert second_entered.is_set()
    assert coordinator.stats() == {
        "backend": "local",
        "distributed": False,
        "active_keys": 0,
        "acquisitions": 3,
        "contentions": 1,
        "timeouts": 0,
    }


class FakeConnection:
    def __init__(self, *, immediate: bool, block: asyncio.Event | None = None):
        self.immediate = immediate
        self.block = block
        self.statements: list[str] = []
    async def scalar(self, statement, _parameters):
        sql = str(statement)
        self.statements.append(sql)
        if "pg_try_advisory_xact_lock" in sql:
            return self.immediate
        if self.block is not None:
            await self.block.wait()
        return None


class FakeConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return None


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return FakeConnectionContext(self.connection)


@pytest.mark.asyncio
async def test_postgres_coordinator_uses_transaction_advisory_lock():
    connection = FakeConnection(immediate=True)
    coordinator = PostgreSQLSessionCoordinator(
        "postgresql://user:password@localhost/chatbot",
        engine=FakeEngine(connection),
    )

    async with coordinator.lock("telegram:42"):
        assert coordinator.stats()["active_keys"] == 1

    assert any("pg_try_advisory_xact_lock" in sql for sql in connection.statements)
    assert coordinator.stats()["active_keys"] == 0
    assert PostgreSQLSessionCoordinator.lock_key("telegram:42") == PostgreSQLSessionCoordinator.lock_key("telegram:42")


@pytest.mark.asyncio
async def test_postgres_coordinator_times_out_on_contended_lock():
    connection = FakeConnection(immediate=False, block=asyncio.Event())
    coordinator = PostgreSQLSessionCoordinator(
        "postgresql://user:password@localhost/chatbot",
        timeout=0.01,
        engine=FakeEngine(connection),
    )

    with pytest.raises(SessionLockTimeoutError):
        async with coordinator.lock("telegram:blocked"):
            raise AssertionError("lock must not be acquired")

    assert coordinator.stats()["contentions"] == 1
    assert coordinator.stats()["timeouts"] == 1
