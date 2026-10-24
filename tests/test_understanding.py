"""模糊理解提示测试。"""
from core.understanding import ambiguity_hint


def test_context_dependent_messages_get_hint():
    assert ambiguity_hint("然后呢", has_history=True)
    assert ambiguity_hint("那个怎么办", has_history=True)
    assert ambiguity_hint("继续", has_history=True)
    assert ambiguity_hint("她刚才说的那个……", has_history=True)
    assert ambiguity_hint("嗯", has_history=True)


def test_clear_messages_do_not_get_hint():
    assert ambiguity_hint("你好", has_history=False) == ""
    assert ambiguity_hint("晚安", has_history=False) == ""
    assert ambiguity_hint("帮我写一个读取 CSV 并统计每列缺失值的 Python 脚本", has_history=True) == ""
    assert ambiguity_hint("", has_history=True) == ""
    assert ambiguity_hint("/card list", has_history=True) == ""


def test_short_message_without_history_is_not_forced():
    assert ambiguity_hint("好的", has_history=False) == ""
    assert ambiguity_hint("嗯", has_history=False) == ""
