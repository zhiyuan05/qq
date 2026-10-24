import pytest

from core.engine import Engine
from platforms.models import Platform, ReplyPlan, UnifiedMessage
from utils.config import AppConfig


class _FakeLLM:
    async def chat(self, messages, **kwargs):
        return "第一段。第二段。"

    async def chat_stream(self, messages, **kwargs):
        yield "第一段。"

    async def aclose(self):
        return None


def _config() -> AppConfig:
    return AppConfig(
        memory={"long_term": {"enabled": False}, "entity": {"auto_extract": False}},
        rate_limit={"enabled": False},
        stickers={"enabled": False},
        persona={"enabled": False},
        humanize={"enabled": True, "quote_probability": 0.0, "group_reply_probability": 1.0},
    )


def _message(content="hi", **overrides) -> UnifiedMessage:
    values = {"platform": Platform.QQ, "user_id": "1", "content": content, "message_id": "m1"}
    values.update(overrides)
    return UnifiedMessage(**values)


@pytest.mark.asyncio
async def test_legacy_entry_point_still_returns_a_string():
    engine = Engine(_config(), llm_client=_FakeLLM())

    response = await engine.process_message(_message())

    assert isinstance(response, str)
    assert "第一段" in response


@pytest.mark.asyncio
async def test_plan_entry_point_returns_reply_plan_for_private_chat():
    engine = Engine(_config(), llm_client=_FakeLLM())

    plan = await engine.process_message_plan(_message())

    assert isinstance(plan, ReplyPlan)
    assert plan.humanized is True
    assert plan.bubbles


@pytest.mark.asyncio
async def test_group_plan_is_marked_non_humanized():
    engine = Engine(_config(), llm_client=_FakeLLM())

    plan = await engine.process_message_plan(_message(group_id="100"))

    assert isinstance(plan, ReplyPlan)
    assert plan.humanized is False


@pytest.mark.asyncio
async def test_commands_still_return_plain_text_in_plan_mode():
    engine = Engine(_config(), llm_client=_FakeLLM())

    response = await engine.process_message_plan(_message("/help"))

    assert isinstance(response, str)


@pytest.mark.asyncio
async def test_non_qq_platforms_keep_the_legacy_string_path():
    engine = Engine(_config(), llm_client=_FakeLLM())
    message = _message()
    message.platform = Platform.CONSOLE

    response = await engine.process_message_plan(message)

    assert isinstance(response, str)
