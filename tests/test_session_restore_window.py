import pytest

from core.session import Session
from database.archive import ConversationArchiveStore
from database.db import dispose_db, init_db
from database.session_store import SQLiteSessionStore
from platforms.models import Attachment, MessageType


@pytest.mark.asyncio
async def test_restore_reads_only_the_newest_window_per_session(tmp_path):
    db_path = str(tmp_path / "window.db")
    await init_db(db_path)
    store = SQLiteSessionStore(db_path)
    session = Session("console:window", "window", "console")
    for index in range(100):
        session.add_message("user", f"message-{index}")
    try:
        await store.save_session(session)

        loaded = await store.load_session(session.session_id, messages_per_session=20)
        assert loaded is not None
        assert len(loaded.messages) == 20
        assert loaded.messages[0].content == "message-80"
        assert loaded.messages[-1].content == "message-99"

        sessions = await store.load_sessions(limit=10, messages_per_session=5)
        assert len(sessions) == 1
        assert len(sessions[0].messages) == 5
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_attachment_metadata_round_trips(tmp_path):
    db_path = str(tmp_path / "attachments.db")
    await init_db(db_path)
    store = SQLiteSessionStore(db_path)
    session = Session("console:media", "media", "console")
    attachment = Attachment(
        kind=MessageType.IMAGE,
        path="data/media/2026-10/abc.jpg",
        mime="image/jpeg",
        size=1234,
        sha256="a" * 64,
    )
    session.add_message("user", "[图片]", attachments=[attachment])
    try:
        await store.save_session(session)
        loaded = await store.load_session(session.session_id)
        assert loaded is not None
        assert len(loaded.messages[0].attachments) == 1
        restored = loaded.messages[0].attachments[0]
        assert restored.kind is MessageType.IMAGE
        assert restored.sha256 == "a" * 64
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_archive_append_is_idempotent_and_cursor_ordered(tmp_path):
    db_path = str(tmp_path / "archive.db")
    await init_db(db_path)
    archive = ConversationArchiveStore(db_path)
    try:
        await archive.append_turns(
            "console:archive",
            "archive",
            "msg-1",
            [("user", "q1", []), ("assistant", "a1", [])],
        )
        # Replaying the same request must not duplicate a turn.
        await archive.append_turns(
            "console:archive",
            "archive",
            "msg-1",
            [("user", "q1", []), ("assistant", "a1", [])],
        )
        await archive.append_turns("console:archive", "archive", "msg-2", [("user", "q2", [])])

        rows = await archive.fetch_after("console:archive", 0)
        assert [row["content"] for row in rows] == ["q1", "a1", "q2"]
        latest = await archive.latest_id("console:archive")
        assert latest == rows[-1]["id"]
        assert await archive.fetch_after("console:archive", latest) == []
    finally:
        await dispose_db(db_path)
