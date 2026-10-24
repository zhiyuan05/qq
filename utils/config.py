"""配置加载与管理，支持 dot-notation 访问和热重载"""
from __future__ import annotations

import os
import re
from pathlib import Path
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from utils.logger import setup_logger

logger = setup_logger("config")


def _normalize_llm_provider(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {"openai": "openai_compatible", "openai_compat": "openai_compatible"}
    return aliases.get(normalized, normalized)


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BotConfig(StrictConfigModel):

    name: str = "AI助手"
    default_character: str = "default"
    default_style: str = "casual"
    command_prefix: str = "/"


class LLMConfig(StrictConfigModel):
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = ""
    model: str = "deepseek-chat"
    fallback_model: str = "deepseek-reasoner"
    vision_model: str = "deepseek-flash"
    vision_fallback_model: str = "deepseek-chat"
    max_tokens: int = Field(default=2048, ge=1, le=131072)
    temperature: float = Field(default=0.7, ge=0, le=2)
    timeout: float = Field(default=30, gt=0, le=600)
    max_retries: int = Field(default=3, ge=0, le=10)
    provider_failure_threshold: int = Field(default=2, ge=1, le=20)
    provider_cooldown_seconds: float = Field(default=30, ge=0, le=3600)
    daily_token_limit: int = Field(default=500000, ge=1)
    stream: bool = True

    @model_validator(mode="before")
    @classmethod
    def apply_provider_defaults(cls, data):
        if not isinstance(data, dict):
            return data
        values = dict(data)
        provider = _normalize_llm_provider(str(values.get("provider", "deepseek")))
        if not values.get("base_url"):
            values["base_url"] = {
                "deepseek": "https://api.deepseek.com",
                "openai_compatible": "https://api.openai.com/v1",
                "ollama": "http://localhost:11434",
            }.get(provider, "")
        if provider == "ollama":
            values.setdefault("model", "qwen2.5:7b")
        if provider != "deepseek":
            values.setdefault("fallback_model", "")
        return values

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = _normalize_llm_provider(value)
        if normalized not in {"deepseek", "openai_compatible", "ollama"}:
            raise ValueError("llm.provider 必须是 deepseek、openai_compatible 或 ollama")
        return normalized


class ShortTermMemoryConfig(StrictConfigModel):
    max_messages: int = Field(default=50, ge=5, le=1000)
    summarize_after: int = Field(default=30, ge=2, le=1000)

    @model_validator(mode="after")
    def validate_summary_threshold(self):
        if self.summarize_after > self.max_messages:
            raise ValueError("summarize_after 不能大于 max_messages")
        return self


class LongTermMemoryConfig(StrictConfigModel):
    enabled: bool = True
    vector_db: str = "chroma"
    path: str = "data/chroma"
    top_k: int = Field(default=5, ge=1, le=50)
    cleanup_interval_seconds: int = Field(default=3600, ge=60)
    low_access_days: int = Field(default=30, ge=1)
    archive_after_days: int = Field(default=60, ge=1)


class EntityMemoryConfig(StrictConfigModel):
    enabled: bool = True
    auto_extract: bool = True
    extract_interval: int = Field(default=3, ge=1, le=100)
    max_profile_chars: int = Field(default=1000, ge=100, le=20000)
    inject_top_k: int = Field(default=6, ge=1, le=50)


class MemoryConfig(StrictConfigModel):
    short_term: ShortTermMemoryConfig = Field(default_factory=ShortTermMemoryConfig)
    long_term: LongTermMemoryConfig = Field(default_factory=LongTermMemoryConfig)
    entity: EntityMemoryConfig = Field(default_factory=EntityMemoryConfig)


class PersistenceConfig(StrictConfigModel):
    enabled: bool = False
    database_url: str = ""
    db_path: str = "data/chatbot.db"
    restore_limit: int = Field(default=200, ge=1, le=10000)
    messages_per_session: int = Field(default=20, ge=1, le=1000)
    archive_retention_days: int = Field(default=180, ge=1, le=3650)
    archive_max_rows: int = Field(default=200000, ge=1000, le=10_000_000)
    multi_instance: bool = False
    lock_timeout: float = Field(default=30, gt=0, le=600)
    pool_size: int = Field(default=5, ge=1, le=100)
    max_overflow: int = Field(default=10, ge=0, le=200)
    pool_timeout: float = Field(default=30, gt=0, le=600)

    @property
    def database_target(self) -> str:
        return self.database_url.strip() or self.db_path

    @model_validator(mode="after")
    def validate_backend(self):
        target = self.database_target.lower()
        if "://" in target and not target.startswith(
            ("sqlite://", "sqlite+aiosqlite://", "postgres://", "postgresql://", "postgresql+asyncpg://")
        ):
            raise ValueError("persistence.database_url 仅支持 SQLite 或 PostgreSQL")
        if self.multi_instance and not target.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
            raise ValueError("persistence.multi_instance 需要 PostgreSQL database_url")
        if self.multi_instance and not self.enabled:
            raise ValueError("persistence.multi_instance 需要启用 persistence.enabled")
        return self


class PlatformConfig(StrictConfigModel):
    enabled: bool = True
    auto_accept_friend: bool = False
    allowed_users: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)
    onebot_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    connect_timeout: float = Field(default=10, gt=0, le=120)
    api_timeout: float = Field(default=15, gt=0, le=120)
    reconnect_min_seconds: float = Field(default=1, gt=0, le=60)
    reconnect_max_seconds: float = Field(default=30, ge=0, le=600)
    event_queue_size: int = Field(default=1000, ge=1, le=100000)
    event_concurrency: int = Field(default=2, ge=1, le=16)
    dedup_cache_size: int = Field(default=2048, ge=1, le=100000)
    max_msg_length: int = Field(default=2000, ge=1, le=100000)
    shutdown_drain_timeout: float = Field(default=15, gt=0, le=300)
    shutdown_cancel_timeout: float = Field(default=2, gt=0, le=30)
    shutdown_close_timeout: float = Field(default=5, gt=0, le=60)

    @field_validator("onebot_url")
    @classmethod
    def validate_onebot_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("platforms.qq.onebot_url 必须使用 ws:// 或 wss://")
        return value

    @model_validator(mode="after")
    def validate_reconnect_range(self):
        if self.reconnect_min_seconds > self.reconnect_max_seconds:
            raise ValueError("platforms.qq.reconnect_min_seconds 不能大于 reconnect_max_seconds")
        return self


class TelegramPlatformConfig(StrictConfigModel):
    enabled: bool = False
    token: str = ""
    owner_id: str = ""
    allowed_users: list[str] = Field(default_factory=list)
    allowed_groups: list[str] = Field(default_factory=list)
    group_mode: str = "mention"
    poll_timeout: int = Field(default=30, ge=1, le=50)
    timeout: float = Field(default=40, gt=0, le=120)
    retry_min_seconds: float = Field(default=1, gt=0, le=60)
    retry_max_seconds: float = Field(default=30, ge=0, le=600)
    dedup_cache_size: int = Field(default=2048, ge=1, le=100000)
    max_msg_length: int = Field(default=4096, ge=1, le=4096)
    shutdown_drain_timeout: float = Field(default=15, gt=0, le=300)
    shutdown_cancel_timeout: float = Field(default=2, gt=0, le=30)
    shutdown_close_timeout: float = Field(default=5, gt=0, le=60)

    @field_validator("group_mode")
    @classmethod
    def validate_group_mode(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"mention", "always"}:
            raise ValueError("platforms.telegram.group_mode 必须是 mention 或 always")
        return value

    @model_validator(mode="after")
    def validate_retry_range(self):
        if self.retry_min_seconds > self.retry_max_seconds:
            raise ValueError("platforms.telegram.retry_min_seconds 不能大于 retry_max_seconds")
        if self.timeout <= self.poll_timeout:
            raise ValueError("platforms.telegram.timeout 必须大于 poll_timeout")
        return self


class WeChatOfficialPlatformConfig(StrictConfigModel):
    """Official Account callback and customer-message transport settings."""

    enabled: bool = False
    token: str = Field(default="", max_length=64, repr=False)
    app_id: str = Field(default="", max_length=64, repr=False)
    app_secret: str = Field(default="", max_length=256, repr=False)
    encoding_aes_key: str = Field(default="", max_length=64, repr=False)
    message_mode: str = "plaintext"
    allowed_users: list[str] = Field(default_factory=list)
    event_queue_size: int = Field(default=512, ge=1, le=10000)
    timeout: float = Field(default=10, gt=0, le=120)
    timestamp_tolerance_seconds: int = Field(default=300, ge=30, le=3600)
    max_body_bytes: int = Field(default=65536, ge=1024, le=1048576)
    max_msg_bytes: int = Field(default=2000, ge=256, le=2048)
    delivery_retries: int = Field(default=2, ge=0, le=5)
    retry_min_seconds: float = Field(default=0.5, ge=0, le=30)
    retry_max_seconds: float = Field(default=5, ge=0, le=120)
    dedup_cache_size: int = Field(default=4096, ge=1, le=100000)
    shutdown_drain_timeout: float = Field(default=15, gt=0, le=120)
    shutdown_cancel_timeout: float = Field(default=2, gt=0, le=30)
    shutdown_close_timeout: float = Field(default=5, gt=0, le=60)

    @field_validator("token", "app_id", "app_secret", "encoding_aes_key")
    @classmethod
    def strip_credentials(cls, value: str) -> str:
        return value.strip()

    @field_validator("message_mode")
    @classmethod
    def validate_message_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"plaintext", "compatible", "safe"}:
            raise ValueError("platforms.wechat.message_mode 必须是 plaintext、compatible 或 safe")
        return normalized

    @model_validator(mode="after")
    def validate_official_account_credentials(self):
        if self.encoding_aes_key and not re.fullmatch(r"[A-Za-z0-9]{43}", self.encoding_aes_key):
            raise ValueError("platforms.wechat.encoding_aes_key 必须是 43 位字母或数字")
        if self.retry_min_seconds > self.retry_max_seconds:
            raise ValueError("platforms.wechat.retry_min_seconds 不能大于 retry_max_seconds")
        if not self.enabled:
            return self
        missing = [
            name
            for name, value in (
                ("token", self.token),
                ("app_id", self.app_id),
                ("app_secret", self.app_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"platforms.wechat 启用时必须配置: {', '.join(missing)}")
        if not re.fullmatch(r"[A-Za-z0-9]{3,32}", self.token):
            raise ValueError("platforms.wechat.token 必须是 3-32 位字母或数字")
        if self.message_mode in {"compatible", "safe"} and not self.encoding_aes_key:
            raise ValueError("platforms.wechat 安全/兼容模式必须配置 encoding_aes_key")
        return self


class PlatformsConfig(StrictConfigModel):
    qq: PlatformConfig = Field(default_factory=PlatformConfig)
    telegram: TelegramPlatformConfig = Field(default_factory=TelegramPlatformConfig)
    wechat: WeChatOfficialPlatformConfig = Field(default_factory=WeChatOfficialPlatformConfig)


class RateLimitConfig(StrictConfigModel):
    enabled: bool = True
    max_per_minute: int = Field(default=20, ge=1)
    max_per_hour: int = Field(default=200, ge=1)
    max_per_group_minute: int = Field(default=60, ge=1)
    daily_message_cap: int = Field(default=500, ge=1)
    max_conversation_depth: int = Field(default=20, ge=0)  # 0 = unlimited


class ActiveHoursConfig(StrictConfigModel):
    enabled: bool = False
    start_hour: int = Field(default=8, ge=0, le=23)
    end_hour: int = Field(default=23, ge=0, le=23)

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_hour == self.end_hour:
            raise ValueError("活跃时段的开始和结束时间不能相同")
        return self


class SecurityConfig(StrictConfigModel):
    admin_users: list[str] = Field(default_factory=list)
    content_filter: bool = False
    max_message_length: int = Field(default=2000, ge=1, le=100000)
    active_hours: ActiveHoursConfig = Field(default_factory=ActiveHoursConfig)


class DelayConfig(StrictConfigModel):
    enabled: bool = False
    min_seconds: float = Field(default=0.5, ge=0, le=60)
    max_seconds: float = Field(default=3.0, ge=0, le=60)
    chars_per_second: float = Field(default=15.0, gt=0, le=10000)

    @model_validator(mode="after")
    def validate_range(self):
        if self.min_seconds > self.max_seconds:
            raise ValueError("delay.min_seconds 不能大于 max_seconds")
        return self


class LearningConfig(StrictConfigModel):
    enabled: bool = True
    trigger_interval: int = Field(default=50, ge=1)
    trigger_schedule: str = "03:00"
    max_history_for_learning: int = 200
    auto_apply: bool = True
    backup_before_learn: bool = True


class LoggingConfig(StrictConfigModel):
    level: str = "INFO"
    file: str = "data/logs/chatbot.log"


class WebConfig(StrictConfigModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    auth_token: str = ""

    @field_validator("auth_token")
    @classmethod
    def validate_auth_token(cls, value: str) -> str:
        value = value.strip()
        if value and len(value) < 16:
            raise ValueError("web.auth_token 至少需要 16 个字符")
        return value


class WebSearchPluginConfig(StrictConfigModel):
    enabled: bool = True
    max_results: int = Field(default=3, ge=1, le=10)


class PluginsConfig(StrictConfigModel):
    web_search: WebSearchPluginConfig = Field(default_factory=WebSearchPluginConfig)


class MediaConfig(StrictConfigModel):
    enabled: bool = True
    storage_dir: str = "data/media"
    outbound_dir: str = "data/media/outbound"
    retention_days: int = Field(default=7, ge=1, le=365)
    max_fetch_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    max_image_pixels: int = Field(default=64_000_000, ge=1_000_000)
    vision_max_side: int = Field(default=1280, ge=256, le=4096)
    vision_max_bytes: int = Field(default=1024 * 1024, ge=64 * 1024, le=8 * 1024 * 1024)
    max_images_per_message: int = Field(default=5, ge=1, le=8)
    group_images: bool = True
    link_preview: bool = True
    link_preview_max_links: int = Field(default=2, ge=0, le=5)
    link_preview_max_chars: int = Field(default=1800, ge=200, le=8000)
    max_video_bytes: int = Field(default=30 * 1024 * 1024, ge=1024 * 1024, le=64 * 1024 * 1024)
    video_frames: int = Field(default=6, ge=2, le=12)
    video_max_seconds: int = Field(default=180, ge=10, le=600)
    history_image_window: int = Field(default=2, ge=0, le=10)
    fetch_attempt_timeout: float = Field(default=8.0, gt=0, le=60)
    fetch_total_timeout: float = Field(default=20.0, gt=0, le=120)
    max_redirects: int = Field(default=5, ge=0, le=10)


class StickersConfig(StrictConfigModel):
    enabled: bool = True
    library_dir: str = "characters/stickers"
    auto_collect: bool = True
    review_required: bool = False
    tag_on_startup: bool = True
    auto_tag: bool = True
    tag_batch_size: int = Field(default=5, ge=1, le=100)
    tag_interval_seconds: int = Field(default=180, ge=30, le=86400)
    max_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    max_per_day: int = Field(default=50, ge=1, le=1000)
    max_total_bytes: int = Field(default=500 * 1024 * 1024, ge=1024 * 1024)
    send_probability: float = Field(default=0.2, ge=0, le=1)
    cooldown_seconds: int = Field(default=30, ge=0, le=3600)
    repeat_window_seconds: int = Field(default=600, ge=0, le=86400)


class HumanizeConfig(StrictConfigModel):
    enabled: bool = True
    max_bubbles: int = Field(default=3, ge=1, le=8)
    max_bubble_chars: int = Field(default=500, ge=20, le=2000)
    split_threshold: int = Field(default=60, ge=10, le=500)
    single_bubble_probability: float = Field(default=0.2, ge=0, le=1)
    typing_chars_per_second: float = Field(default=6.0, gt=0, le=100)
    min_delay: float = Field(default=0.8, ge=0, le=30)
    max_delay: float = Field(default=4.0, ge=0, le=60)
    quote_probability: float = Field(default=0.5, ge=0, le=1)
    group_reply_probability: float = Field(default=0.25, ge=0, le=1)
    group_reply_cooldown_seconds: int = Field(default=90, ge=0, le=3600)
    polish: bool = True


class VoiceConfig(StrictConfigModel):
    enabled: bool = False
    asr_provider: str = "faster_whisper"
    tts_provider: str = "edge_tts"
    asr_model: str = "base"
    max_audio_seconds: int = Field(default=60, ge=1, le=600)
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_dir: str = "data/media/tts"
    tts_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    voice_probability: float = Field(default=0.15, ge=0, le=1)
    voice_max_chars: int = Field(default=80, ge=1, le=500)
    per_minute: int = Field(default=2, ge=1, le=60)
    per_hour: int = Field(default=10, ge=1, le=600)
    unload_after_seconds: int = Field(default=600, ge=0, le=86400)


class DeliveryConfig(StrictConfigModel):
    audit_enabled: bool = True
    outbound_per_minute: int = Field(default=8, ge=1, le=120)
    outbound_per_hour: int = Field(default=60, ge=1, le=2000)
    failure_stop_threshold: int = Field(default=2, ge=1, le=10)


class ContextConfig(StrictConfigModel):
    enabled: bool = True
    max_prompt_tokens: int = Field(default=4000, ge=512, le=128000)
    recent_messages: int = Field(default=12, ge=1, le=100)
    summary_tokens: int = Field(default=300, ge=0, le=4000)
    user_trait_tokens: int = Field(default=150, ge=0, le=4000)
    global_trait_tokens: int = Field(default=150, ge=0, le=4000)
    memory_top_k: int = Field(default=3, ge=0, le=20)
    image_token_cost: int = Field(default=800, ge=0, le=8000)


class PersonaConfig(StrictConfigModel):
    enabled: bool = True
    evolve_after_messages: int = Field(default=30, ge=5, le=1000)
    evolve_schedule: str = "04:00"
    max_traits_per_update: int = Field(default=3, ge=1, le=20)
    min_global_confidence: float = Field(default=0.75, ge=0, le=1)
    min_user_confidence: float = Field(default=0.6, ge=0, le=1)
    auto_approve_global: bool = True
    max_global_active: int = Field(default=12, ge=1, le=100)
    max_user_active: int = Field(default=8, ge=1, le=100)
    max_global_core: int = Field(default=3, ge=1, le=20)
    max_user_core: int = Field(default=2, ge=1, le=20)
    core_confidence: float = Field(default=0.85, ge=0, le=1)
    decay_per_week: float = Field(default=0.05, ge=0, le=1)
    profile_max_chars: int = Field(default=2000, ge=100, le=20000)


class AdminAgentConfig(StrictConfigModel):
    enabled: bool = False
    trigger_prefix: str = "#"
    executor_socket: str = "data/admin.sock"
    executor_token: str = ""
    confirmation_ttl_seconds: int = Field(default=120, ge=30, le=600)
    require_confirmation: bool = True


class UsageConfig(StrictConfigModel):
    enabled: bool = True
    currency: str = "CNY"
    daily_budget_micros: int = Field(default=0, ge=0)
    peak_multiplier: float = Field(default=2.0, ge=1, le=10)
    offpeak_multiplier: float = Field(default=1.0, ge=0, le=10)
    peak_windows: str = "09:00-12:00,14:00-18:00"
    peak_weekdays_only: bool = True
    price_cache_miss_per_million: float = Field(default=0.0, ge=0)
    price_cache_hit_per_million: float = Field(default=0.0, ge=0)
    price_cache_write_per_million: float = Field(default=0.0, ge=0)
    price_output_per_million: float = Field(default=0.0, ge=0)


class AppConfig(StrictConfigModel):
    bot: BotConfig = Field(default_factory=BotConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    platforms: PlatformsConfig = Field(default_factory=PlatformsConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    delay: DelayConfig = Field(default_factory=DelayConfig)
    learning: LearningConfig = Field(default_factory=LearningConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    stickers: StickersConfig = Field(default_factory=StickersConfig)
    humanize: HumanizeConfig = Field(default_factory=HumanizeConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    delivery: DeliveryConfig = Field(default_factory=DeliveryConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    persona: PersonaConfig = Field(default_factory=PersonaConfig)
    usage: UsageConfig = Field(default_factory=UsageConfig)
    admin_agent: AdminAgentConfig = Field(default_factory=AdminAgentConfig)


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """加载配置文件和环境变量。

    搜索顺序: 1) config_path 参数  2) config/config.yaml  3) 默认值
    """
    load_dotenv(env_path)

    path = Path(config_path)
    if not path.exists():
        fallback = Path("config/config.yaml")
        if fallback.exists():
            path = fallback
            config_path = str(fallback)

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    api_key = os.getenv("CHATBOT_LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("CHATBOT_LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL", "")
    web_auth_token = os.getenv("CHATBOT_WEB_AUTH_TOKEN", "")
    telegram_token = os.getenv("CHATBOT_TELEGRAM_TOKEN", "")
    onebot_access_token = os.getenv("CHATBOT_ONEBOT_ACCESS_TOKEN", "")
    database_url = os.getenv("CHATBOT_DATABASE_URL", "")
    multi_instance = os.getenv("CHATBOT_MULTI_INSTANCE", "")

    llm_raw = raw.get("llm", {})
    if api_key:
        llm_raw["api_key"] = api_key
    if base_url:
        llm_raw["base_url"] = base_url
    raw["llm"] = llm_raw

    if web_auth_token:
        web_raw = raw.get("web", {})
        web_raw["auth_token"] = web_auth_token
        raw["web"] = web_raw

    platforms_raw = raw.get("platforms", {})
    if telegram_token:
        telegram_raw = platforms_raw.get("telegram", {})
        telegram_raw["token"] = telegram_token
        platforms_raw["telegram"] = telegram_raw
    if onebot_access_token:
        qq_raw = platforms_raw.get("qq", {})
        qq_raw["access_token"] = onebot_access_token
        platforms_raw["qq"] = qq_raw
    wechat_environment = {
        "enabled": os.getenv("CHATBOT_WECHAT_ENABLED", ""),
        "token": os.getenv("CHATBOT_WECHAT_TOKEN", ""),
        "app_id": os.getenv("CHATBOT_WECHAT_APP_ID", ""),
        "app_secret": os.getenv("CHATBOT_WECHAT_APP_SECRET", ""),
        "encoding_aes_key": os.getenv("CHATBOT_WECHAT_ENCODING_AES_KEY", ""),
        "message_mode": os.getenv("CHATBOT_WECHAT_MESSAGE_MODE", ""),
        "event_queue_size": os.getenv("CHATBOT_WECHAT_EVENT_QUEUE_SIZE", ""),
        "timeout": os.getenv("CHATBOT_WECHAT_TIMEOUT", ""),
        "timestamp_tolerance_seconds": os.getenv("CHATBOT_WECHAT_TIMESTAMP_TOLERANCE_SECONDS", ""),
        "max_body_bytes": os.getenv("CHATBOT_WECHAT_MAX_BODY_BYTES", ""),
        "max_msg_bytes": os.getenv("CHATBOT_WECHAT_MAX_MESSAGE_BYTES", ""),
        "delivery_retries": os.getenv("CHATBOT_WECHAT_DELIVERY_RETRIES", ""),
        "retry_min_seconds": os.getenv("CHATBOT_WECHAT_RETRY_MIN_SECONDS", ""),
        "retry_max_seconds": os.getenv("CHATBOT_WECHAT_RETRY_MAX_SECONDS", ""),
    }
    wechat_allowed_users = os.getenv("CHATBOT_WECHAT_ALLOWED_USERS", "")
    if any(wechat_environment.values()) or wechat_allowed_users:
        wechat_raw = platforms_raw.get("wechat", {})
        for field_name, value in wechat_environment.items():
            if value:
                wechat_raw[field_name] = value
        if wechat_allowed_users:
            wechat_raw["allowed_users"] = [value.strip() for value in wechat_allowed_users.split(",") if value.strip()]
        platforms_raw["wechat"] = wechat_raw
    raw["platforms"] = platforms_raw

    if database_url or multi_instance:
        persistence_raw = raw.get("persistence", {})
        if database_url:
            persistence_raw["database_url"] = database_url
        if multi_instance:
            persistence_raw["multi_instance"] = multi_instance
        raw["persistence"] = persistence_raw

    voice_enabled = os.getenv("CHATBOT_VOICE_ENABLED", "")
    if voice_enabled:
        voice_raw = raw.get("voice", {})
        voice_raw["enabled"] = voice_enabled.lower() in {"1", "true", "yes", "on"}
        raw["voice"] = voice_raw

    executor_token = os.getenv("CHATBOT_ADMIN_EXECUTOR_TOKEN", "")
    if executor_token:
        admin_raw = raw.get("admin_agent", {})
        admin_raw["executor_token"] = executor_token
        raw["admin_agent"] = admin_raw

    config = AppConfig(**raw)
    validate_config(config)
    return config


def validate_config(config: AppConfig):
    """验证关键配置项"""
    errors = []
    if config.llm.provider != "ollama" and (
        not config.llm.api_key or config.llm.api_key in ("", "your-api-key-here", "sk-xxxxx")
    ):
        errors.append("未配置 CHATBOT_LLM_API_KEY（也兼容 DEEPSEEK_API_KEY）")
    if not config.llm.base_url:
        errors.append("未配置 LLM base_url")
    if config.web.enabled and config.web.host not in {"127.0.0.1", "localhost", "::1"} and not config.web.auth_token:
        errors.append("Web 面板监听非本地地址时必须配置 CHATBOT_WEB_AUTH_TOKEN")
    if errors:
        raise ValueError("配置验证失败:\n" + "\n".join(f"  - {e}" for e in errors))


