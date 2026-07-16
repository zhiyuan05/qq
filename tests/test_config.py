"""配置系统测试"""
import os
import yaml
from utils.config import AppConfig, load_config


def test_default_config():
    cfg = AppConfig()
    assert cfg.bot.name == "AI助手"
    assert cfg.bot.command_prefix == "/"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.llm.max_tokens == 2048
    assert cfg.memory.short_term.max_messages == 50
    assert cfg.rate_limit.max_per_minute == 20
    assert cfg.learning.trigger_interval == 50


def test_config_with_custom_values():
    cfg = AppConfig(
        bot={"name": "测试机器人", "command_prefix": "!"},
        llm={"model": "deepseek-reasoner", "temperature": 0.5},
    )
    assert cfg.bot.name == "测试机器人"
    assert cfg.bot.command_prefix == "!"
    assert cfg.llm.model == "deepseek-reasoner"
    assert cfg.llm.temperature == 0.5


def test_load_config_from_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "bot:\n  name: YAML机器人\nllm:\n  model: deepseek-chat\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
    cfg = load_config(str(config_file))
    assert cfg.bot.name == "YAML机器人"
    assert cfg.llm.model == "deepseek-chat"


