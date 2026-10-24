from datetime import datetime

import pytest
from sqlalchemy import event

from core.session import Session
from database.db import dispose_db, get_async_engine, get_session_factory, init_db
from database.session_store import SessionConflictError, SQLiteSessionStore


@pytest.mark.asyncio
async def test_outer_transaction_rolls_back_staged_snapshot_and_revision(tmp_path):
    db_path = str(tmp_path / "atomic-projection.db")
    await init_db(db_path)
    store = SQLiteSessionStore(db_path)
    session = Session("console:atomic", "atomic", "console")
    session.add_message("user", "original")
    try:
        await store.save_session(session)
        session.add_message("assistant", "uncommitted")
        with pytest.raises(RuntimeError, match="outbox failure"):
            async with get_session_factory(db_path)() as connection:
                async with connection.begin():
                    revision = await store.save_session_in_transaction(connection, session)
                    assert revision == 2
                    assert session.revision == 1
                    raise RuntimeError("outbox failure")
        restored = await store.load_session(session.session_id)
        assert restored.revision == 1
        assert [message.content for message in restored.messages] == ["original"]
        assert session.revision == 1

        async with get_session_factory(db_path)() as connection:
            async with connection.begin():
                revision = await store.save_session_in_transaction(connection, session)
        session.revision = revision
        assert (await store.load_session(session.session_id)).revision == 2
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_sqlite_session_store_round_trip_and_replace(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    await init_db(db_path)
    store = SQLiteSessionStore(db_path)
    try:
        session = Session(
            session_id="console:user-1",
            user_id="user-1",
            platform="console",
            character_card="default",
            style="casual",
            summary="known\x00summary",
            created_at=datetime(2026, 1, 1, 8, 0, 0),
            last_active=datetime(2026, 1, 1, 8, 1, 0),
        )
        session.add_message("user", "hello\x00world", token_count=2)
        session.add_message("assistant", "hi", token_count=1)
        await store.save_session(session)

        restored = await store.load_sessions()
        assert len(restored) == 1
        assert restored[0].session_id == session.session_id
        assert restored[0].summary == "knownsummary"
        assert [item.content for item in restored[0].messages] == ["helloworld", "hi"]
        assert [item.token_count for item in restored[0].messages] == [2, 1]

        session.style = "professional"
        session.messages = session.messages[-1:]
        await store.save_session(session)

        replaced = await store.load_sessions()
        assert replaced[0].style == "professional"
        assert [item.content for item in replaced[0].messages] == ["hi"]
        assert await store.health_check() is True
        assert await store.stats() == {"backend": "sqlite", "sessions": 1, "messages": 1}

        await store.delete_session(session.session_id)
        assert await store.load_sessions() == []
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_database_connections_are_isolated_by_path(tmp_path):
    first_path = str(tmp_path / "first.db")
    second_path = str(tmp_path / "second.db")
    await init_db(first_path)
    await init_db(second_path)
    first_store = SQLiteSessionStore(first_path)
    second_store = SQLiteSessionStore(second_path)
    try:
        await first_store.save_session(Session("console:first", "first", "console"))
        assert (await first_store.stats())["sessions"] == 1
        assert (await second_store.stats())["sessions"] == 0
    finally:
        await dispose_db(first_path)
        await dispose_db(second_path)


@pytest.mark.asyncio
async def test_stale_session_snapshot_cannot_overwrite_newer_revision(tmp_path):
    db_path = str(tmp_path / "cas.db")
    await init_db(db_path)
    store = SQLiteSessionStore(db_path)
    try:
        original = Session("console:cas", "cas", "console")
        original.add_message("user", "initial")
        assert await store.save_session(original) == 1
        assert original.revision == 1

        first = await store.load_session(original.session_id)
        stale = await store.load_session(original.session_id)
        assert first is not None and stale is not None

        first.add_message("assistant", "winner")
        assert await store.save_session(first) == 2
        stale.add_message("assistant", "stale overwrite")
        with pytest.raises(SessionConflictError) as captured:
            await store.save_session(stale)

        assert captured.value.expected == 1
        assert captured.value.actual == 2
        committed = await store.load_session(original.session_id)
        assert committed is not None
        assert committed.revision == 2
        assert [message.content for message in committed.messages] == ["initial", "winner"]
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_session_load_uses_one_consistent_select(tmp_path):
    db_path = str(tmp_path / "consistent-read.db")
    await init_db(db_path)
    store = SQLiteSessionStore(db_path)
    session = Session("console:consistent", "consistent", "console")
    session.add_message("user", "question")
    session.add_message("assistant", "answer")
    await store.save_session(session)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = get_async_engine(db_path)
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        loaded = await store.load_session(session.session_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        await dispose_db(db_path)

    assert loaded is not None
    assert [message.content for message in loaded.messages] == ["question", "answer"]
    assert len(statements) == 1
