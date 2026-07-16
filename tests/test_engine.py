"""核心引擎测试"""
import pytest
from unittest.mock import AsyncMock, patch
from core.engine import Engine
from utils.config import AppConfig
from platforms.models import UnifiedMessage, Platform


def _make_msg(content, user_id="u1"):
    return UnifiedMessage(platform=Platform.WECHAT, user_id=user_id, content=content)


@pytest.fixture
def engine():
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    cfg.memory.entity.auto_extract = False
    return Engine(cfg)


def test_engine_init(engine):
    assert engine.character_manager is not None
    assert engine.style_engine is not None
    assert engine.session_manager is not None


@pytest.mark.asyncio
async def test_engine_help_command(engine):
    result = await engine.process_message(_make_msg("/help"))
    assert "可用指令" in result


@pytest.mark.asyncio
async def test_engine_new_command(engine):
    result = await engine.process_message(_make_msg("/new"))
    assert "新会话" in result


@pytest.mark.asyncio
async def test_engine_card_list(engine):
    result = await engine.process_message(_make_msg("/card list"))
    assert "角色卡列表" in result


@pytest.mark.asyncio
async def test_engine_style_list(engine):
    result = await engine.process_message(_make_msg("/style list"))
    assert "风格列表" in result


@pytest.mark.asyncio
async def test_engine_context(engine):
    result = await engine.process_message(_make_msg("/context"))
    assert "当前会话" in result


@pytest.mark.asyncio
async def test_engine_chat_calls_llm(engine):
    with patch.object(engine, "_get_llm") as mock_get_llm:
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = "测试回复"
        mock_get_llm.return_value = mock_llm

        result = await engine.process_message(_make_msg("你好"))
        assert "测试回复" in result
        mock_llm.chat.assert_called_once()


@pytest.mark.asyncio
async def test_engine_group_info_no_group(engine):
    """非群聊环境使用 /group 应提示"""
    result = await engine.process_message(_make_msg("/group info"))
    assert "仅在群聊中可用" in result


@pytest.mark.asyncio
async def test_engine_mention_detection(engine):
    """测试 @mention 检测"""
    msg = UnifiedMessage(platform=Platform.QQ, user_id="u1", content="@AI助手 你好",
                         group_id="g1")
    is_mentioned, clean = engine._check_mention(msg)
    assert is_mentioned is True
    assert "你好" in clean


@pytest.mark.asyncio
async def test_engine_mention_not_detected(engine):
    """普通群消息不触发 mention"""
    msg = UnifiedMessage(platform=Platform.QQ, user_id="u1", content="大家好",
                         group_id="g1")
    is_mentioned, clean = engine._check_mention(msg)
    assert is_mentioned is False


def test_engine_has_group_manager(engine):
    assert engine.group_manager is not None
