"""Prompt构建器测试"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.prompt_builder import PromptBuilder, time_context
from characters.manager import CharacterCard
from styles.engine import Style
from memory.entity_memory import EntityMemory


def test_time_context_uses_beijing_time():
    moment = datetime(2026, 10, 24, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    text = time_context(moment)
    assert "2026年10月24日" in text
    assert "星期六" in text
    assert "15:30" in text


@pytest.mark.asyncio
async def test_build_basic_prompt():
    builder = PromptBuilder()
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}。规则:\n{{rules}}",
        "rules": ["说中文"],
    })
    style = Style({"name": "casual", "prompt_modifier": "轻松随意"})
    messages = await builder.build(card, style, "你好", [], user_id="u1")

    assert messages[0]["role"] == "system"
    assert "助手" in messages[0]["content"]
    assert "轻松随意" in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "你好"


@pytest.mark.asyncio
async def test_build_injects_understanding_hint():
    builder = PromptBuilder()
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    messages = await builder.build(
        card,
        style,
        "然后呢",
        [],
        understanding_hint="理解提示：结合上下文推断。",
    )

    assert "理解提示：结合上下文推断。" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_injects_lore_text():
    builder = PromptBuilder()
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    messages = await builder.build(
        card,
        style,
        "外面打雷了",
        [],
        lore_text="相关设定：\n- 助手怕打雷。",
    )

    assert "助手怕打雷。" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_with_memory():
    builder = PromptBuilder()
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    memory_results = [
        {"text": "用户喜欢Python", "metadata": {}},
        {"text": "用户是程序员", "metadata": {}},
    ]
    messages = await builder.build(card, style, "推荐个框架", [], memory_results, "u1")
    assert "用户喜欢Python" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_with_entity_memory():
    em = EntityMemory()
    await em.store("u1", "preference", "语言", "Python")
    builder = PromptBuilder(entity_memory=em)
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    messages = await builder.build(card, style, "我在学 Python", [], user_id="u1")
    assert "Python" in messages[0]["content"]

    unrelated = await builder.build(card, style, "你好", [], user_id="u1")
    assert "Python" not in unrelated[0]["content"]


@pytest.mark.asyncio
async def test_build_with_examples():
    builder = PromptBuilder()
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}",
        "examples": [{"user": "hi", "assistant": "hello!"}],
    })
    style = Style({"name": "casual", "prompt_modifier": ""})
    messages = await builder.build_with_examples(card, style, "你好", [], user_id="u1")
    assert any(m["content"] == "hi" for m in messages)
    assert any(m["content"] == "hello!" for m in messages)
