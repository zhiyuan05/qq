"""集成测试"""
import pytest
from unittest.mock import AsyncMock, patch
from core.engine import Engine
from utils.config import AppConfig
from platforms.models import UnifiedMessage, Platform


def _msg(content, user_id="u1"):
    return UnifiedMessage(platform=Platform.WECHAT, user_id=user_id, content=content)


@pytest.fixture
def engine():
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    return Engine(cfg)


@pytest.mark.asyncio
async def test_full_conversation_flow(engine):
    with patch.object(engine, "_get_llm") as mock:
        llm = AsyncMock()
        llm.chat.return_value = "你好！有什么可以帮你的？"
        mock.return_value = llm

        resp = await engine.process_message(_msg("你好"))
        assert resp is not None
        assert llm.chat.called

    resp = await engine.process_message(_msg("/style use professional"))
    assert "已切换风格" in resp

    resp = await engine.process_message(_msg("/context"))
    assert "当前会话" in resp

    resp = await engine.process_message(_msg("/history"))
    assert resp is not None

    resp = await engine.process_message(_msg("/undo"))
    assert "撤销" in resp

    resp = await engine.process_message(_msg("/new"))
    assert "新会话" in resp


@pytest.mark.asyncio
async def test_character_switch(engine):
    resp = await engine.process_message(_msg("/card use default"))
    assert "已切换到角色卡" in resp


@pytest.mark.asyncio
async def test_memory_commands(engine):
    resp = await engine.process_message(_msg("/memory profile"))
    assert resp is not None

    resp = await engine.process_message(_msg("/memory clear"))
    assert "已清空" in resp
