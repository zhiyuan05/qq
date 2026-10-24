import pytest
from PIL import Image

from core.engine import Engine
from platforms.base import PlatformDeliveryError
from platforms.models import Attachment, MessageType, Platform, QuotedMessage, UnifiedMessage
from platforms.qq import QQPlatform
from platforms.qq_segments import MediaRef, parse_message


def test_array_segments_parse_text_mention_image_and_quote():
    raw = {
        "message": [
            {"type": "reply", "data": {"id": "123"}},
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": "看这个"}},
            {"type": "image", "data": {"url": "https://cdn.example/a.jpg", "file": "a.jpg"}},
        ]
    }

    parsed = parse_message(raw, self_id="999")

    assert parsed.quote_id == "123"
    assert parsed.at_user_ids == ["999"]
    assert parsed.mentioned_bot is True
    assert parsed.text == "[CQ:at,qq=999]看这个"
    assert parsed.model_text == "[CQ:at,qq=999]看这个[图片]"
    assert parsed.media[0].kind is MessageType.IMAGE
    assert parsed.media[0].url == "https://cdn.example/a.jpg"


def test_cq_string_parsing_and_emoji_fallback():
    emoji_id = "a" * 32
    raw = {
        "raw_message": f"[CQ:reply,id=42][CQ:image,file=f9-{emoji_id}.gif]你好&#91;x&#93;",
    }

    parsed = parse_message(raw)

    assert parsed.quote_id == "42"
    assert parsed.text == "你好[x]"
    assert parsed.model_text == "[图片]你好[x]"
    assert parsed.media[0].emoji_id == emoji_id


def test_voice_and_file_segments_become_placeholders():
    raw = {
        "message": [
            {"type": "record", "data": {"file": "voice.silk", "url": "https://cdn.example/v.silk"}},
            {"type": "file", "data": {"file": "doc.pdf"}},
        ]
    }

    parsed = parse_message(raw)

    assert parsed.text == ""
    assert parsed.model_text == "[语音][文件:doc.pdf]"
    assert [ref.kind for ref in parsed.media] == [MessageType.VOICE, MessageType.FILE]


def test_empty_message_has_no_segments():
    parsed = parse_message({})
    assert parsed.text == ""
    assert parsed.model_text == ""
    assert parsed.media == []


def _qq_config() -> dict:
    return {
        "onebot_url": "ws://onebot.test:3001",
        "event_queue_size": 10,
    }


@pytest.mark.asyncio
async def test_quote_cache_resolves_without_network():
    platform = QQPlatform(_qq_config())
    platform._remember_message("m1", QuotedMessage("m1", "u1", "小明", "原文", True))

    quoted = await platform._resolve_quote("m1")

    assert quoted is not None
    assert quoted.sender_name == "小明"
    assert quoted.text == "原文"


@pytest.mark.asyncio
async def test_invalid_quote_id_is_rejected():
    platform = QQPlatform(_qq_config())
    assert await platform._resolve_quote("../../etc/passwd") is None


class _FakeStore:
    class _Config:
        enabled = True
        max_images_per_message = 5

    config = _Config()

    async def fetch_image(self, url):
        return Attachment(kind=MessageType.IMAGE, path="/tmp/x.jpg", mime="image/jpeg", size=1, sha256="a" * 64)


@pytest.mark.asyncio
async def test_download_media_respects_limit():
    platform = QQPlatform(_qq_config(), media_store=_FakeStore())
    refs = [MediaRef(kind=MessageType.IMAGE, url=f"https://cdn.example/{index}.jpg") for index in range(7)]

    attachments = await platform._download_media(refs)

    assert len(attachments) == 5


@pytest.mark.asyncio
async def test_set_avatar_uses_local_onebot_action(tmp_path):
    image = tmp_path / "avatar.png"
    Image.new("RGB", (64, 64), color=(20, 120, 220)).save(image, format="PNG")

    class _Store:
        class _Config:
            enabled = True
            storage_dir = str(tmp_path)

        config = _Config()

    platform = QQPlatform(_qq_config(), media_store=_Store())
    calls = []

    async def fake_call(action, params):
        calls.append((action, params))
        return {"status": "ok", "retcode": 0}

    platform._call_action = fake_call
    await platform.set_avatar(str(image))

    assert calls == [("set_qq_avatar", {"file": str(image.resolve())})]


@pytest.mark.asyncio
async def test_set_avatar_falls_back_to_base64(tmp_path):
    image = tmp_path / "avatar.png"
    Image.new("RGB", (64, 64), color=(20, 120, 220)).save(image, format="PNG")

    class _Store:
        class _Config:
            enabled = True
            storage_dir = str(tmp_path)

        config = _Config()

    platform = QQPlatform(_qq_config(), media_store=_Store())
    calls = []

    async def fake_call(action, params):
        calls.append((action, params))
        if len(calls) == 1:
            raise PlatformDeliveryError("path not readable", code="invalid_path")
        return {"status": "ok", "retcode": 0}

    platform._call_action = fake_call
    await platform.set_avatar(str(image))

    assert calls[0][1]["file"] == str(image.resolve())
    assert calls[1][0] == "set_qq_avatar"
    assert calls[1][1]["file"].startswith("base64://")


@pytest.mark.asyncio
async def test_engine_updates_avatar_from_private_attached_image():
    from utils.config import AppConfig

    config = AppConfig()
    config.security.admin_users = ["qq:owner"]
    config.admin_agent.enabled = False
    config.rate_limit.enabled = False
    config.memory.long_term.enabled = False
    config.memory.entity.auto_extract = False
    engine = Engine(config)
    platform = QQPlatform(_qq_config())
    seen = []

    async def fake_set_avatar(path):
        seen.append(path)

    platform.set_avatar = fake_set_avatar
    engine.register_platform(platform)

    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="owner",
        content="换成这个头像",
        attachments=[Attachment(kind=MessageType.IMAGE, path="/tmp/avatar.png", mime="image/png")],
    )

    assert await engine.process_message(message) == "头像已更新"
    assert seen == ["/tmp/avatar.png"]

    command = UnifiedMessage(
        platform=Platform.QQ,
        user_id="owner",
        content="/avatar",
        attachments=[Attachment(kind=MessageType.IMAGE, path="/tmp/avatar.png", mime="image/png")],
    )
    assert await engine.process_message(command) == "头像已更新"
    assert seen == ["/tmp/avatar.png", "/tmp/avatar.png"]

    missing_image = UnifiedMessage(platform=Platform.QQ, user_id="owner", content="把这张图设成头像")
    assert "请把图片" in await engine.process_message(missing_image)


@pytest.mark.asyncio
async def test_engine_delegates_avatar_to_qq_platform():
    from utils.config import AppConfig

    engine = Engine(
        AppConfig(
            memory={"long_term": {"enabled": False}, "entity": {"auto_extract": False}},
            rate_limit={"enabled": False},
            media={"enabled": False},
        )
    )
    platform = QQPlatform(_qq_config())
    seen = []

    async def fake_set_avatar(path):
        seen.append(path)

    platform.set_avatar = fake_set_avatar
    engine.register_platform(platform)

    assert await engine.set_qq_avatar("/tmp/avatar.png") == "头像已更新"
    assert seen == ["/tmp/avatar.png"]


@pytest.mark.asyncio
async def test_set_avatar_rejects_paths_outside_media_root(tmp_path):
    platform = QQPlatform(_qq_config())

    with pytest.raises(Exception):
        await platform.set_avatar("/etc/passwd")


def test_mface_summary_is_preserved_for_vision_context():
    raw = {
        "message": [
            {
                "type": "mface",
                "data": {
                    "summary": "[开心]",
                    "url": "https://cdn.example/face.gif",
                    "emoji_id": "b" * 32,
                },
            }
        ]
    }

    parsed = parse_message(raw)

    assert parsed.model_text == "[表情:[开心]]"
    assert parsed.media[0].emoji_id == "b" * 32
