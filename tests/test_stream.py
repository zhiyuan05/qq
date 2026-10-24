"""流式对话测试"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.engine import Engine
from platforms.models import UnifiedMessage, Platform
from utils.config import AppConfig


@pytest.fixture
def engine_with_mock_llm():
    """Engine with a mocked LLM that returns chunks. """
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    eng = Engine(cfg)
    # Mock the llm to return chunks
    mock_llm = AsyncMock()
    async def mock_stream(*args, **kwargs):
        yield "Hello"
        yield " "
        yield "World"
    mock_llm.chat_stream = mock_stream
    mock_llm.chat.return_value = "Hello World"
    eng._llm = mock_llm
    return eng


@pytest.mark.asyncio
async def test_handle_chat_stream_yields_chunks(engine_with_mock_llm):
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="你好")
    chunks = []
    async for chunk in engine_with_mock_llm._handle_chat_stream(msg):
        chunks.append(chunk)
    assert len(chunks) >= 1
    for c in chunks:
        assert isinstance(c, str)


@pytest.mark.asyncio
async def test_handle_chat_stream_accumulates(engine_with_mock_llm):
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="你好")
    chunks = []
    async for chunk in engine_with_mock_llm._handle_chat_stream(msg):
        chunks.append(chunk)
    full = "".join(chunks)
    assert "Hello" in full
    assert "World" in full


@pytest.mark.asyncio
async def test_handle_chat_stream_saves_to_session(engine_with_mock_llm):
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u2", content="你好")
    chunks = []
    async for chunk in engine_with_mock_llm._handle_chat_stream(msg):
        chunks.append(chunk)
    full = "".join(chunks)
    # Session should have the full message
    session_key = msg.session_key
    session = engine_with_mock_llm.session_manager.get(session_key)
    assert session is not None
    # Should at least have user message recorded
    assert any(m.role == "user" and m.content == "你好" for m in session.messages)


@pytest.mark.asyncio
async def test_public_stream_runs_pipeline_and_emits_done(engine_with_mock_llm):
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="stream-public", content="你好")
    events = [event async for event in engine_with_mock_llm.process_message_stream(msg)]

    assert any(event["type"] == "chunk" for event in events)
    assert events[-1]["type"] == "done"
    assert "Hello World" in events[-1]["content"]


@pytest.mark.asyncio
async def test_public_stream_rejects_oversized_input():
    cfg = AppConfig(
        security={"max_message_length": 3, "active_hours": {"enabled": False}},
        delay={"enabled": False},
        memory={"long_term": {"enabled": False}, "entity": {"auto_extract": False}},
    )
    engine = Engine(cfg)
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="too-long", content="1234")
    events = [event async for event in engine.process_message_stream(msg)]

    assert events == [{"type": "done", "content": "消息过长（最多 3 个字符）", "replace": True}]
