"""Telegram adapter tests."""
import pytest
from platforms.models import Platform, UnifiedMessage, MessageType
from platforms.telegram import TelegramPlatform


def test_telegram_platform_enum():
    assert hasattr(Platform, 'TELEGRAM')
    assert Platform.TELEGRAM.value == "telegram"


def test_unified_message_for_telegram():
    msg = UnifiedMessage(
        platform=Platform.TELEGRAM,
        user_id="12345",
        content="hello",
        message_type=MessageType.TEXT,
        group_id=None,
        nickname="TestUser",
    )
    assert msg.platform == Platform.TELEGRAM
    assert msg.is_group is False
    assert msg.session_key == "telegram:12345"


def test_unified_message_telegram_group():
    msg = UnifiedMessage(
        platform=Platform.TELEGRAM,
        user_id="12345",
        content="hello",
        message_type=MessageType.TEXT,
        group_id="-100123456",
    )
    assert msg.is_group is True
    assert msg.session_key == "telegram:-100123456"


@pytest.fixture
def tg_config():
    return {
        "token": "test-token",
        "owner_id": "12345",
        "allowed_users": [],
        "group_mode": "mention",
        "max_msg_length": 4000,
    }


def test_telegram_platform_init(tg_config):
    platform = TelegramPlatform(tg_config)
    assert platform.name == "telegram"
    assert platform._token == "test-token"
    assert platform._owner_id == "12345"


def test_telegram_platform_with_user_whitelist(tg_config):
    tg_config["allowed_users"] = ["12345", "67890"]
    platform = TelegramPlatform(tg_config)
    assert platform._is_allowed("12345") is True
    assert platform._is_allowed("99999") is False


def test_telegram_platform_empty_whitelist_allows_all(tg_config):
    platform = TelegramPlatform(tg_config)
    assert platform._is_allowed("anyone") is True


def test_telegram_should_respond_in_group_mention_mode(tg_config):
    platform = TelegramPlatform(tg_config)
    platform._bot_username = "testbot"
    assert platform._should_respond_group("hello @testbot") is True
    assert platform._should_respond_group("hello") is False


def test_telegram_should_respond_in_group_always_mode(tg_config):
    tg_config["group_mode"] = "always"
    platform = TelegramPlatform(tg_config)
    assert platform._should_respond_group("hello") is True
