"""User texting-style hint tests。"""
from core.session import ChatMessage
from core.style_profile import format_style_hint


def test_short_messages_produce_a_short_style_hint():
    history = [
        ChatMessage("user", "今天好累"),
        ChatMessage("assistant", "先歇会儿"),
        ChatMessage("user", "哈哈没事"),
        ChatMessage("user", "哈哈好"),
    ]

    hint = format_style_hint(history)

    assert "偏短" in hint
    assert "哈哈" in hint
    assert "不要刻意模仿" in hint


def test_too_little_history_has_no_hint():
    assert format_style_hint([ChatMessage("user", "你好")]) == ""


def test_commands_and_assistant_messages_are_ignored():
    history = [
        ChatMessage("user", "/card list"),
        ChatMessage("assistant", "这是很长很长的一段助手回复，不应该被当作用户习惯。"),
        ChatMessage("user", "嗯"),
        ChatMessage("user", "好"),
    ]

    assert format_style_hint(history) == ""
