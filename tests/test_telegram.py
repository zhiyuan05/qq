"""Telegram adapter and transport fault-injection tests."""
import asyncio

import httpx
import pytest
from platforms.base import PlatformConfigurationError, PlatformRateLimitedError, PlatformState
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


def _response(status_code: int, payload: dict, method: str = "test") -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", f"https://api.telegram.org/{method}"),
    )


class ReplayTelegramClient:
    def __init__(self):
        self.poll_offsets: list[int] = []
        self.send_calls = 0
        self.acknowledged = asyncio.Event()
        self.block = asyncio.Event()
        self.closed = False

    async def post(self, url, *, params=None, json=None):
        method = url.rsplit("/", 1)[-1]
        if method == "getMe":
            return _response(200, {"ok": True, "result": {"id": 42, "username": "TestBot"}}, method)
        if method == "sendMessage":
            self.send_calls += 1
            if self.send_calls == 1:
                return _response(503, {"ok": False, "error_code": 503}, method)
            return _response(200, {"ok": True, "result": {"message_id": 99}}, method)
        if method == "getUpdates":
            offset = int(params["offset"])
            self.poll_offsets.append(offset)
            if len(self.poll_offsets) <= 2:
                return _response(
                    200,
                    {
                        "ok": True,
                        "result": [
                            {
                                "update_id": 10,
                                "message": {
                                    "message_id": 8,
                                    "from": {"id": 7, "first_name": "Ada"},
                                    "chat": {"id": 7, "type": "private"},
                                    "text": "hello",
                                },
                            }
                        ],
                    },
                    method,
                )
            self.acknowledged.set()
            await self.block.wait()
            return _response(200, {"ok": True, "result": []}, method)
        raise AssertionError(method)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_telegram_retries_unacknowledged_delivery_without_reinvoking_callback(tg_config):
    client = ReplayTelegramClient()

    async def no_delay(_seconds):
        return None

    platform = TelegramPlatform(
        {**tg_config, "retry_min_seconds": 0, "retry_max_seconds": 0},
        client=client,
        sleep=no_delay,
    )
    callback_calls = 0

    async def callback(message):
        nonlocal callback_calls
        callback_calls += 1
        assert message.raw_data["update_id"] == 10
        return "world"

    platform.on_message(callback)
    await platform.start()
    await asyncio.wait_for(client.acknowledged.wait(), timeout=1)

    assert client.poll_offsets[:3] == [0, 0, 11]
    assert callback_calls == 1
    assert client.send_calls == 2
    assert platform.status_snapshot()["stats"]["duplicates"] == 1
    assert platform.status_snapshot()["stats"]["send_failures"] == 1

    await platform.stop()
    assert client.closed is False
    assert platform.state is PlatformState.STOPPED
    assert not platform._tasks


@pytest.mark.asyncio
async def test_telegram_group_filter_and_case_insensitive_exact_mention(tg_config):
    tg_config.update({"allowed_groups": ["-10"]})
    platform = TelegramPlatform(tg_config)
    platform._bot_username = "TestBot"
    received = []

    async def callback(message):
        received.append(message)
        return None

    platform.on_message(callback)
    await platform._process_update(
        {
            "update_id": 1,
            "message": {
                "message_id": 2,
                "from": {"id": 7},
                "chat": {"id": -10, "type": "group"},
                "text": "Hi @testbot there",
            },
        }
    )
    await platform._process_update(
        {
            "update_id": 2,
            "message": {
                "message_id": 3,
                "from": {"id": 7},
                "chat": {"id": -11, "type": "group"},
                "text": "@TestBot blocked group",
            },
        }
    )

    assert [message.content for message in received] == ["Hi  there"]
    assert received[0].group_id == "-10"
    assert platform.status_snapshot()["stats"]["ignored"] == 1


@pytest.mark.asyncio
async def test_telegram_rate_limit_exposes_retry_after_without_description_or_token(tg_config):
    class RateLimitedClient:
        async def post(self, *_args, **_kwargs):
            return _response(
                429,
                {
                    "ok": False,
                    "error_code": 429,
                    "description": "request containing test-token",
                    "parameters": {"retry_after": 7},
                },
            )

    platform = TelegramPlatform(tg_config, client=RateLimitedClient())
    with pytest.raises(PlatformRateLimitedError) as captured:
        await platform._call_api("sendMessage", json={"chat_id": "1", "text": "hello"})

    assert captured.value.retry_after == 7
    assert captured.value.code == 429
    assert "test-token" not in str(captured.value)


@pytest.mark.asyncio
async def test_telegram_empty_token_is_a_configuration_failure(tg_config):
    tg_config["token"] = ""
    platform = TelegramPlatform(tg_config)

    with pytest.raises(PlatformConfigurationError):
        await platform.start()

    assert platform.state is PlatformState.FAILED
    assert not platform._tasks
