"""配置系统测试"""
from pathlib import Path

import pytest
import yaml

from pydantic import ValidationError

from utils.config import (
    AppConfig,
    LLMConfig,
    PersistenceConfig,
    PlatformConfig,
    TelegramPlatformConfig,
    load_config,
    validate_config,
)

ROOT = Path(__file__).resolve().parents[1]


def _mapping_paths(value, prefix=""):
    paths = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths.update(_mapping_paths(nested, path))
    return paths


def test_default_config():
    cfg = AppConfig()
    assert cfg.bot.name == "AI助手"
    assert cfg.bot.command_prefix == "/"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.llm.base_url == "https://api.deepseek.com"
    assert cfg.llm.max_tokens == 2048
    assert cfg.memory.short_term.max_messages == 50
    assert cfg.rate_limit.max_per_minute == 20
    assert cfg.learning.trigger_interval == 50
    assert cfg.persistence.enabled is False
    assert cfg.persistence.restore_limit == 200


def test_config_with_custom_values():
    cfg = AppConfig(
        bot={"name": "测试机器人", "command_prefix": "!"},
        llm={"model": "deepseek-reasoner", "temperature": 0.5},
    )
    assert cfg.bot.name == "测试机器人"
    assert cfg.bot.command_prefix == "!"
    assert cfg.llm.model == "deepseek-reasoner"
    assert cfg.llm.temperature == 0.5


def test_unknown_configuration_keys_fail_fast():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AppConfig(persistnce={"enabled": True})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AppConfig(persistence={"pool_sze": 10})


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


def test_llm_provider_specific_defaults_and_local_validation():
    ollama = LLMConfig(provider="ollama")
    assert ollama.model == "qwen2.5:7b"
    assert ollama.base_url == "http://localhost:11434"
    assert ollama.fallback_model == ""
    validate_config(AppConfig(llm={"provider": "ollama"}))

    compatible = LLMConfig(provider="openai")
    assert compatible.provider == "openai_compatible"
    assert compatible.base_url == "https://api.openai.com/v1"
    assert compatible.fallback_model == ""


def test_remote_web_listener_fails_closed_without_authentication():
    with pytest.raises(ValueError, match="CHATBOT_WEB_AUTH_TOKEN"):
        validate_config(AppConfig(llm={"provider": "ollama"}, web={"host": "0.0.0.0"}))

    validate_config(
        AppConfig(
            llm={"provider": "ollama"},
            web={"host": "0.0.0.0", "auth_token": "long-production-token"},
        )
    )


def test_generic_llm_environment_takes_precedence_over_legacy_names(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("llm:\n  provider: deepseek\n", encoding="utf-8")
    monkeypatch.setenv("CHATBOT_LLM_API_KEY", "generic-key")
    monkeypatch.setenv("CHATBOT_LLM_BASE_URL", "https://generic.example/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://legacy.example/v1")

    config = load_config(str(config_file), str(tmp_path / "missing.env"))

    assert config.llm.api_key == "generic-key"
    assert config.llm.base_url == "https://generic.example/v1"


def test_primary_and_fallback_yaml_use_the_same_schema():
    primary = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    fallback = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))

    assert _mapping_paths(primary) == _mapping_paths(fallback)
    AppConfig(**primary)
    AppConfig(**fallback)


def test_platform_transport_configuration_is_validated():
    with pytest.raises(ValidationError, match="ws://"):
        PlatformConfig(onebot_url="http://localhost:3001")
    with pytest.raises(ValidationError, match="reconnect_min_seconds"):
        PlatformConfig(reconnect_min_seconds=5, reconnect_max_seconds=1)
    with pytest.raises(ValidationError, match="group_mode"):
        TelegramPlatformConfig(group_mode="sometimes")
    with pytest.raises(ValidationError, match="retry_min_seconds"):
        TelegramPlatformConfig(retry_min_seconds=5, retry_max_seconds=1)
    with pytest.raises(ValidationError, match="timeout"):
        TelegramPlatformConfig(poll_timeout=30, timeout=30)


def test_platform_secrets_are_loaded_from_environment(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("llm:\n  provider: ollama\nplatforms:\n  telegram:\n    token: yaml-token\n", encoding="utf-8")
    monkeypatch.setenv("CHATBOT_TELEGRAM_TOKEN", "environment-token")
    monkeypatch.setenv("CHATBOT_ONEBOT_ACCESS_TOKEN", "onebot-secret")

    config = load_config(str(config_file), str(tmp_path / "missing.env"))

    assert config.platforms.telegram.token == "environment-token"
    assert config.platforms.qq.access_token == "onebot-secret"


def test_persistence_database_url_and_multi_instance_validation():
    sqlite = PersistenceConfig(db_path="custom.db")
    postgres = PersistenceConfig(
        enabled=True,
        database_url="postgresql://chatbot:secret@database/chatbot",
        multi_instance=True,
    )

    assert sqlite.database_target == "custom.db"
    assert postgres.database_target.startswith("postgresql://")
    with pytest.raises(ValidationError, match="multi_instance"):
        PersistenceConfig(database_url="sqlite+aiosqlite:///chatbot.db", multi_instance=True)
    with pytest.raises(ValidationError, match="enabled"):
        PersistenceConfig(database_url="postgresql://database/chatbot", multi_instance=True)
    with pytest.raises(ValidationError, match="仅支持"):
        PersistenceConfig(database_url="mysql://database/chatbot")


def test_database_url_environment_overrides_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "llm:\n  provider: ollama\npersistence:\n  enabled: true\n  db_path: yaml.db\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHATBOT_DATABASE_URL", "postgresql://chatbot:secret@database/chatbot")
    monkeypatch.setenv("CHATBOT_MULTI_INSTANCE", "true")

    config = load_config(str(config_file), str(tmp_path / "missing.env"))

    assert config.persistence.database_target == "postgresql://chatbot:secret@database/chatbot"
    assert config.persistence.multi_instance is True
