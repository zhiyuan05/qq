"""PostgreSQL integration coverage, enabled by the dedicated CI service job."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from core.session import Session
from database.db import dispose_db, init_db
from database.session_coordinator import PostgreSQLSessionCoordinator
from database.session_store import PostgreSQLSessionStore, SessionConflictError
from memory.entity_memory import EntityMemory

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not configured")


@pytest.mark.asyncio
async def test_postgres_store_cas_entity_upsert_and_distributed_lock():
    database_url = str(POSTGRES_URL)
    session_id = f"integration:{uuid.uuid4().hex}"
    entity_user = f"integration-{uuid.uuid4().hex}"
    await init_db(database_url, pool_size=4, max_overflow=2)
    store = PostgreSQLSessionStore(database_url, pool_size=4, max_overflow=2)
    entities = EntityMemory(database_url, pool_size=4, max_overflow=2)
    try:
        original = Session(session_id, "postgres-user", "console")
        original.add_message("user", "initial")
        assert await store.save_session(original) == 1

        winner = await store.load_session(session_id)
        stale = await store.load_session(session_id)
        assert winner is not None and stale is not None
        winner.add_message("assistant", "winner")
        await store.save_session(winner)
        stale.add_message("assistant", "stale")
        with pytest.raises(SessionConflictError):
            await store.save_session(stale)

        await entities.store(entity_user, "preference", "language", "Python")
        await entities.store(entity_user, "preference", "language", "Rust")
        profile = await entities.query(entity_user)
        assert len(profile) == 1
        assert profile[0].value == "Rust"

        coordinator_a = PostgreSQLSessionCoordinator(database_url, timeout=2)
        coordinator_b = PostgreSQLSessionCoordinator(database_url, timeout=2)
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []

        async def first():
            async with coordinator_a.lock(session_id):
                order.append("first")
                first_entered.set()
                await release_first.wait()

        async def second():
            await first_entered.wait()
            async with coordinator_b.lock(session_id):
                order.append("second")

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await first_entered.wait()
        await asyncio.sleep(0.1)
        assert order == ["first"]
        release_first.set()
        await asyncio.gather(first_task, second_task)
        assert order == ["first", "second"]
    finally:
        await store.delete_session(session_id)
        await entities.clear(entity_user)
        await dispose_db(database_url)


@pytest.mark.asyncio
async def test_postgres_lock_does_not_starve_single_connection_data_pool():
    database_url = str(POSTGRES_URL)
    session_id = f"pool-contract:{uuid.uuid4().hex}"
    await dispose_db(database_url)
    await init_db(database_url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    store = PostgreSQLSessionStore(database_url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    coordinator = PostgreSQLSessionCoordinator(
        database_url,
        timeout=1,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
    )
    try:
        await store.save_session(Session(session_id, "pool-user", "console"))
        async with coordinator.lock(session_id):
            loaded = await asyncio.wait_for(store.load_session(session_id), timeout=1)
            assert loaded is not None
            loaded.summary = "updated while the coordinator owns its separate connection"
            await asyncio.wait_for(store.save_session(loaded), timeout=1)
    finally:
        await store.delete_session(session_id)
        await dispose_db(database_url)


@pytest.mark.asyncio
async def test_postgres_archive_persona_and_usage_schema():
    database_url = str(POSTGRES_URL)
    session_id = f"integration:{uuid.uuid4().hex}"
    await init_db(database_url, pool_size=2, max_overflow=1)
    try:
        from database.archive import ConversationArchiveStore
        from database.db import get_session_factory
        from llm.usage import UsageBuckets, UsageLedger
        from memory.persona import PersonaStore, TraitCandidate
        from sqlalchemy import text
        from utils.config import PersonaConfig, UsageConfig

        async with get_session_factory(database_url)() as database_session:
            tables = {
                row[0]
                for row in (
                    await database_session.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public'"
                        )
                    )
                ).all()
            }
            conversation_columns = {
                row[0]
                for row in (
                    await database_session.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'conversations'"
                        )
                    )
                ).all()
            }
        assert {
            "conversation_archive",
            "persona_profiles",
            "persona_traits",
            "persona_versions",
            "persona_cursors",
            "llm_usage_events",
        }.issubset(tables)
        assert "attachments_json" in conversation_columns

        archive = ConversationArchiveStore(database_url, pool_size=2, max_overflow=1)
        await archive.append_turns(
            session_id,
            "postgres-user",
            "request-1",
            [("user", "question", []), ("assistant", "answer", [])],
        )
        await archive.append_turns(
            session_id,
            "postgres-user",
            "request-1",
            [("user", "question", []), ("assistant", "answer", [])],
        )
        rows = await archive.fetch_after(session_id, 0)
        assert [row["content"] for row in rows] == ["question", "answer"]

        persona = PersonaStore(database_url, config=PersonaConfig(), pool_size=2, max_overflow=1)
        await persona.apply_candidates(
            [
                TraitCandidate(
                    scope="user",
                    category="speech_style",
                    trait_key="语气",
                    value="简洁",
                    confidence=0.8,
                )
            ],
            user_key=f"qq:{uuid.uuid4().hex}",
            session_id=session_id,
        )

        ledger = UsageLedger(database_url, UsageConfig(), pool_size=2, max_overflow=1)
        await ledger.record(
            request_id="request-1",
            session_id=session_id,
            user_id="postgres-user",
            model="deepseek-chat",
            buckets=UsageBuckets(cache_miss=10, output=5),
        )
        assert (await ledger.aggregate(session_id=session_id))["turns"] == 1
    finally:
        await dispose_db(database_url)
