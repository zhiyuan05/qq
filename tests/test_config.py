"""配置系统测试"""
import os
import yaml
import tempfile
from utils.config import AppConfig, load_config, Config


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


def test_dotnotation_config_load():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({
            "platforms": {"qq": {"enabled": True, "onebot_url": "ws://127.0.0.1:3001"}},
            "security": {"rate_limit": {"per_user_per_minute": 10}},
        }, f)
        f.flush()
        config = Config(f.name)

    assert config.get("platforms.qq.enabled") is True
    assert config.get("platforms.qq.onebot_url") == "ws://127.0.0.1:3001"
    assert config.get("security.rate_limit.per_user_per_minute") == 10
    assert config.get("nonexistent.key", "default") == "default"
    os.unlink(f.name)


def test_dotnotation_config_set():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({}, f)
        f.flush()
        config = Config(f.name)

    config.set("security.rate_limit.per_user_per_minute", 15)
    assert config.get("security.rate_limit.per_user_per_minute") == 15
    os.unlink(f.name)


def test_dotnotation_config_reload():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump({"test": {"value": 1}}, f)
        f.flush()
        config = Config(f.name)

    assert config.get("test.value") == 1

    with open(f.name, 'w') as f2:
        yaml.dump({"test": {"value": 2}}, f2)
    config.reload()

    assert config.get("test.value") == 2
    os.unlink(f.name)
