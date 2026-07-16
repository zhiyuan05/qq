"""消息拆分工具测试"""
import pytest
from utils.message_splitter import split_message


def test_split_short_text():
    text = "你好，今天天气不错。"
    result = split_message(text, max_length=2000)
    assert len(result) == 1
    assert result[0] == text


def test_split_long_text():
    text = "第一句话。" * 50 + "第二句话。" * 50
    result = split_message(text, max_length=100)
    assert len(result) > 1


def test_split_by_newline():
    text = "第一段\n\n第二段\n\n第三段"
    result = split_message(text, max_length=10)
    assert len(result) >= 2


def test_split_empty():
    result = split_message("")
    assert result == [""] or result == []


def test_split_exact_boundary():
    text = "a" * 100
    result = split_message(text, max_length=100)
    assert len(result) == 1


def test_split_mixed_content():
    """混合内容正确拆分"""
    text = "短句。" + "a" * 200 + "。结尾。"
    result = split_message(text, max_length=100)
    assert len(result) > 1
