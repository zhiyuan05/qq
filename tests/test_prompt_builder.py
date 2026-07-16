"""Prompt构建器测试"""
from core.prompt_builder import PromptBuilder
from characters.manager import CharacterCard
from styles.engine import Style
from memory.entity_memory import EntityMemory


def test_build_basic_prompt():
    builder = PromptBuilder()
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}。规则:\n{{rules}}",
        "rules": ["说中文"],
    })
    style = Style({"name": "casual", "prompt_modifier": "轻松随意"})
    messages = builder.build(card, style, "你好", [], user_id="u1")

    assert messages[0]["role"] == "system"
    assert "助手" in messages[0]["content"]
    assert "轻松随意" in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "你好"


def test_build_with_memory():
    builder = PromptBuilder()
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    memory_results = [
        {"text": "用户喜欢Python", "metadata": {}},
        {"text": "用户是程序员", "metadata": {}},
    ]
    messages = builder.build(card, style, "推荐个框架", [], memory_results, "u1")
    assert "用户喜欢Python" in messages[0]["content"]


def test_build_with_entity_memory():
    em = EntityMemory()
    em.store("u1", "preference", "语言", "Python")
    builder = PromptBuilder(entity_memory=em)
    card = CharacterCard({"name": "助手", "system_prompt": "你是{{name}}"})
    style = Style({"name": "casual", "prompt_modifier": ""})

    messages = builder.build(card, style, "你好", [], user_id="u1")
    assert "Python" in messages[0]["content"]


def test_build_with_examples():
    builder = PromptBuilder()
    card = CharacterCard({
        "name": "助手",
        "system_prompt": "你是{{name}}",
        "examples": [{"user": "hi", "assistant": "hello!"}],
    })
    style = Style({"name": "casual", "prompt_modifier": ""})
    messages = builder.build_with_examples(card, style, "你好", [], user_id="u1")
    assert any(m["content"] == "hi" for m in messages)
    assert any(m["content"] == "hello!" for m in messages)
