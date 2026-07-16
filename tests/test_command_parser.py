"""指令解析器测试"""
from core.command_parser import CommandParser


def test_parse_non_command():
    parser = CommandParser("/")
    result = parser.parse("你好，今天天气怎么样？")
    assert result.is_command is False


def test_parse_simple_command():
    parser = CommandParser("/")
    result = parser.parse("/help")
    assert result.is_command is True
    assert result.command == "help"


def test_parse_command_with_subcommand():
    parser = CommandParser("/")
    result = parser.parse("/card use creative")
    assert result.is_command is True
    assert result.command == "card"
    assert result.subcommand == "use"
    assert result.args == ["creative"]


def test_parse_command_with_args():
    parser = CommandParser("/")
    result = parser.parse("/memory recall Python编程")
    assert result.command == "memory"
    assert result.subcommand == "recall"
    assert result.args == ["Python编程"]


def test_parse_command_case_insensitive():
    parser = CommandParser("/")
    result = parser.parse("/Card List")
    assert result.command == "card"
    assert result.subcommand == "list"
