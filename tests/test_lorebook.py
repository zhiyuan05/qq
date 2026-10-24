"""世界书关键词选择测试。"""
from characters.lorebook import format_lorebook, select_lorebook_entries


def _entry(content, keys, **overrides):
    return {"keys": keys, "content": content, **overrides}


def test_cjk_keyword_matches_substring():
    entries = [_entry("猫娘怕打雷。", ["打雷"])]

    assert select_lorebook_entries(entries, "外面打雷了，好可怕") == ["猫娘怕打雷。"]
    assert select_lorebook_entries(entries, "今天天气不错") == []


def test_ascii_keyword_requires_word_boundary():
    entries = [_entry("用户喜欢猫。", ["cat"])]

    assert select_lorebook_entries(entries, "I have a cat") == ["用户喜欢猫。"]
    assert select_lorebook_entries(entries, "用cat试试") == ["用户喜欢猫。"]
    assert select_lorebook_entries(entries, "category theory") == []


def test_constant_and_disabled_entries():
    entries = [
        _entry("常驻设定。", ["不会出现的词"], constant=True),
        _entry("无关键词的常驻设定。", [], constant=True),
        _entry("关闭的设定。", ["你好"], enabled=False),
    ]

    assert select_lorebook_entries(entries, "随便聊聊") == ["常驻设定。", "无关键词的常驻设定。"]
    assert select_lorebook_entries(entries, "你好") == ["常驻设定。", "无关键词的常驻设定。"]


def test_selective_entry_requires_secondary_key():
    entries = [
        _entry("考试周设定。", ["考试"], selective=True, secondary_keys=["笔记"]),
    ]

    assert select_lorebook_entries(entries, "考试好难") == []
    assert select_lorebook_entries(entries, "考试前借我笔记") == ["考试周设定。"]


def test_priority_limit_and_budget():
    entries = [
        _entry("低优先级。", ["话题"], priority=10),
        _entry("高优先级。", ["话题"], priority=200),
        _entry("中优先级。", ["话题"], priority=100),
    ]

    assert select_lorebook_entries(entries, "聊聊话题") == ["高优先级。", "中优先级。", "低优先级。"]
    assert select_lorebook_entries(entries, "聊聊话题", limit=2) == ["高优先级。", "中优先级。"]
    assert select_lorebook_entries(entries, "聊聊话题", max_chars=len("高优先级。")) == ["高优先级。"]


def test_format_lorebook_returns_empty_for_no_entries():
    assert format_lorebook([]) == ""
    assert "相关设定" in format_lorebook(["一条设定"])
