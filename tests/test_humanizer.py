import random

import pytest

from core.humanizer import Humanizer
from media.stickers import Sticker
from platforms.models import Attachment, MessageType, Platform, UnifiedMessage
from utils.config import HumanizeConfig, VoiceConfig


def _message(content="hi", **overrides) -> UnifiedMessage:
    values = {
        "platform": Platform.QQ,
        "user_id": "1",
        "content": content,
        "message_id": "m1",
    }
    values.update(overrides)
    return UnifiedMessage(**values)


class _Stickers:
    def __init__(self, sticker: Sticker | None):
        self.sticker = sticker
        self.calls = []

    async def pick(self, tag, session_key):
        self.calls.append((tag, session_key))
        return self.sticker


@pytest.mark.asyncio
async def test_short_reply_stays_in_one_bubble_and_strips_markers():
    humanizer = Humanizer(HumanizeConfig(quote_probability=1.0), VoiceConfig())
    plan = await humanizer.plan("你好[sticker:开心]呀", _message(), rng=random.Random(1))

    assert plan.bubbles == ["你好呀"]
    assert plan.sticker_path == ""
    assert plan.reply_to == "m1"


@pytest.mark.asyncio
async def test_long_reply_splits_into_at_most_three_bubbles_with_delays():
    humanizer = Humanizer(HumanizeConfig(), VoiceConfig())
    text = "第一句话。" * 20
    plan = await humanizer.plan(text, _message(), rng=random.Random(2))

    assert 1 < len(plan.bubbles) <= 3
    assert len(plan.delays) == len(plan.bubbles) - 1
    assert all(delay >= 0 for delay in plan.delays)


@pytest.mark.asyncio
async def test_long_reply_can_stay_in_one_bubble():
    humanizer = Humanizer(
        HumanizeConfig(split_threshold=10, single_bubble_probability=1.0),
        VoiceConfig(),
    )
    text = "第一句话。第二句话。第三句话。"

    plan = await humanizer.plan(text, _message(), rng=random.Random(7))

    assert plan.bubbles == [text]


@pytest.mark.asyncio
async def test_overlong_reply_falls_back_to_legacy_delivery():
    humanizer = Humanizer(HumanizeConfig(max_bubble_chars=20, split_threshold=10), VoiceConfig())
    plan = await humanizer.plan("很长" * 100, _message(), rng=random.Random(3))

    assert plan.humanized is False
    assert plan.bubbles == ["很长" * 100]


@pytest.mark.asyncio
async def test_markdown_polish_strips_chat_markup_but_keeps_code():
    humanizer = Humanizer(HumanizeConfig())
    raw = (
        "## 标题\n- 第一点\n- 第二点\n**重点**\n\n"
        "```python\nprint('**keep**')\n```"
    )
    plan = await humanizer.plan(raw, _message())

    text = "\n".join(plan.bubbles)
    assert "##" not in text
    assert "**重点**" not in text
    assert "```python" in text
    assert "**keep**" in text


@pytest.mark.asyncio
async def test_markdown_polish_can_be_disabled():
    humanizer = Humanizer(HumanizeConfig(polish=False))
    plan = await humanizer.plan("**重点**", _message())
    assert "**重点**" in "\n".join(plan.bubbles)


@pytest.mark.asyncio
async def test_markdown_polish_keeps_dates_and_ignores_code_markers():
    humanizer = Humanizer(HumanizeConfig())
    plan = await humanizer.plan(
        "2026. 10. 24 是截止日期\n```\n[sticker:开心]\n```",
        _message(),
    )

    text = "\n".join(plan.bubbles)
    assert "2026. 10. 24" in text
    assert plan.sticker_tag == ""
    assert "[sticker:开心]" in text


@pytest.mark.asyncio
async def test_user_format_request_keeps_markdown():
    humanizer = Humanizer(HumanizeConfig())
    plan = await humanizer.plan("- 第一点\n- 第二点", _message(content="请用列表回答"))

    assert "- 第一点" in "\n".join(plan.bubbles)


@pytest.mark.asyncio
async def test_group_messages_are_not_humanized():
    humanizer = Humanizer(HumanizeConfig(), VoiceConfig())
    plan = await humanizer.plan("你好", _message(group_id="100"), rng=random.Random(4))

    assert plan.humanized is False
    assert plan.reply_to == ""


@pytest.mark.asyncio
async def test_media_message_forces_quote_and_can_select_sticker():
    sticker = Sticker("h" * 64, "/tmp/a.png", ("开心",), "", "library", "active", 0)
    humanizer = Humanizer(HumanizeConfig(quote_probability=0.0), VoiceConfig())
    message = _message(
        attachments=[Attachment(kind=MessageType.IMAGE, path="/tmp/x.jpg")],
        message_type=MessageType.IMAGE,
    )

    plan = await humanizer.plan("[sticker:开心]好图", message, stickers=_Stickers(sticker), rng=random.Random(5))

    assert plan.reply_to == "m1"
    assert plan.sticker_path == "/tmp/a.png"
    assert plan.sticker_hash == "h" * 64


@pytest.mark.asyncio
async def test_voice_replaces_first_bubble():
    humanizer = Humanizer(HumanizeConfig(), VoiceConfig(enabled=True, voice_probability=1.0, voice_max_chars=80))
    plan = await humanizer.plan("你好呀", _message(), rng=random.Random(6))

    assert plan.voice_text == "你好呀"
    assert plan.bubbles == []
    assert plan.fallback_text == "你好呀"
