"""配置加载与管理，支持 dot-notation 访问和热重载"""
import os
from pathlib import Path
from typing import Any, List, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from utils.logger import setup_logger

logger = setup_logger("config")


class BotConfig(BaseModel):
    name: str = "AI助手"
    default_character: str = "default"
    default_style: str = "casual"
    command_prefix: str = "/"


class LLMConfig(BaseModel):
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    fallback_model: str = "deepseek-reasoner"
    max_tokens: int = 2048
    temperature: float = 0.7
    timeout: int = 30
    max_retries: int = 3
    stream: bool = True


class ShortTermMemoryConfig(BaseModel):
    max_messages: int = 50
    summarize_after: int = 30


class LongTermMemoryConfig(BaseModel):
    enabled: bool = True
    vector_db: str = "chroma"
    top_k: int = 5


class EntityMemoryConfig(BaseModel):
    enabled: bool = True
    auto_extract: bool = True


class MemoryConfig(BaseModel):
    short_term: ShortTermMemoryConfig = ShortTermMemoryConfig()
    long_term: LongTermMemoryConfig = LongTermMemoryConfig()
    entity: EntityMemoryConfig = EntityMemoryConfig()


class PlatformConfig(BaseModel):
    enabled: bool = True
    auto_accept_friend: bool = False
    allowed_users: List[str] = Field(default_factory=list)
    allowed_groups: List[str] = Field(default_factory=list)
    onebot_url: str = "ws://127.0.0.1:3001"


class TelegramPlatformConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    owner_id: str = ""
    allowed_users: List[str] = Field(default_factory=list)
    group_mode: str = "mention"


class PlatformsConfig(BaseModel):
    wechat: PlatformConfig = PlatformConfig()
    qq: PlatformConfig = PlatformConfig()
    telegram: TelegramPlatformConfig = TelegramPlatformConfig()


class RateLimitConfig(BaseModel):
    enabled: bool = True
    max_per_minute: int = 20
    max_per_hour: int = 200


class SecurityConfig(BaseModel):
    admin_users: List[str] = Field(default_factory=list)
    content_filter: bool = False
    max_message_length: int = 2000


class LearningConfig(BaseModel):
    enabled: bool = True
    trigger_interval: int = 50
    trigger_schedule: str = "03:00"
    max_history_for_learning: int = 200
    auto_apply: bool = True
    backup_before_learn: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "data/logs/chatbot.log"


class WebConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080


class AppConfig(BaseModel):
    bot: BotConfig = BotConfig()
    llm: LLMConfig = LLMConfig()
    memory: MemoryConfig = MemoryConfig()
    platforms: PlatformsConfig = PlatformsConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    security: SecurityConfig = SecurityConfig()
    learning: LearningConfig = LearningConfig()
    logging: LoggingConfig = LoggingConfig()
    web: WebConfig = WebConfig()


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """加载配置文件和环境变量"""
    load_dotenv(env_path)

    path = Path(config_path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "")

    llm_raw = raw.get("llm", {})
    if api_key:
        llm_raw["api_key"] = api_key
    if base_url:
        llm_raw["base_url"] = base_url
    raw["llm"] = llm_raw

    config = AppConfig(**raw)
    validate_config(config)
    return config


def validate_config(config: AppConfig):
    """验证关键配置项"""
    errors = []
    if not config.llm.api_key or config.llm.api_key in ("", "your-api-key-here", "sk-xxxxx"):
        errors.append("未配置 DEEPSEEK_API_KEY，请在 .env 文件中填入")
    if not config.llm.base_url:
        errors.append("未配置 DEEPSEEK_BASE_URL")
    if config.memory.short_term.max_messages < 5:
        errors.append("short_term.max_messages 不能小于 5")
    if config.rate_limit.max_per_minute < 1:
        errors.append("rate_limit.max_per_minute 不能小于 1")
    if errors:
        raise ValueError("配置验证失败:\n" + "\n".join(f"  - {e}" for e in errors))


class Config:
    """Dot-notation config wrapper with hot-reload support."""

    def __init__(self, config_path: str = "config/config.yaml"):
        self._path = Path(config_path)
        self._data: dict = {}
        self.reload()

    def reload(self):
        if self._path.exists():
            with open(self._path, 'r', encoding='utf-8') as f:
                self._data = yaml.safe_load(f) or {}
            logger.info(f"配置已加载: {self._path}")
        else:
            logger.warning(f"配置文件不存在: {self._path}，使用空配置")
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._data
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
            if value is None:
                return default
        return value

    def set(self, key: str, value: Any):
        keys = key.split(".")
        data = self._data
        for k in keys[:-1]:
            if k not in data or not isinstance(data[k], dict):
                data[k] = {}
            data = data[k]
        data[keys[-1]] = value

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, 'w', encoding='utf-8') as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False)

    def to_dict(self) -> dict:
        return dict(self._data)


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def init_config(path: str = "config/config.yaml") -> Config:
    global _config
    _config = Config(path)
    return _config
