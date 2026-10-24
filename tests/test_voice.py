import base64
import io
import sys
import types
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from media.voice import VoiceService
from platforms.models import MessageType, Platform, ReplyPlan, UnifiedMessage
from platforms.qq import QQPlatform
from platforms.qq_segments import MediaRef
from tests.test_qq import FakeConnector, FakeWebSocket, qq_config
from utils.config import MediaConfig, VoiceConfig


def _service(tmp_path, **overrides) -> VoiceService:
    config = VoiceConfig(**overrides)
    return VoiceService(config, MediaConfig(storage_dir=str(tmp_path / "media")))


def test_voice_is_disabled_by_default(tmp_path):
    assert _service(tmp_path).available is False


def test_unsupported_provider_disables_voice(tmp_path):
    service = _service(tmp_path, enabled=True, asr_provider="funasr")
    assert service.available is False


def test_wav_bytes_are_passed_through(tmp_path):
    service = _service(tmp_path)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 1600)

    path = service._decode_to_wav(buffer.getvalue())

    assert path is not None
    assert path.read_bytes().startswith(b"RIFF")


def test_unknown_audio_decoding_degrades_to_none(tmp_path):
    service = _service(tmp_path)
    assert service._decode_to_wav(b"\x00" * 100) is None


@pytest.mark.asyncio
async def test_tts_returns_none_when_unavailable(tmp_path):
    service = _service(tmp_path)
    assert await service.synthesize("你好") is None


@pytest.mark.asyncio
async def test_tts_uses_character_voice_profile(tmp_path, monkeypatch):
    captured = {}

    class FakeCommunicate:
        def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
            captured.update(text=text, voice=voice, rate=rate, pitch=pitch)

        async def save(self, path):
            Path(path).write_bytes(b"fake-mp3")

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeCommunicate))
    service = _service(
        tmp_path,
        enabled=True,
        tts_dir=str(tmp_path / "media" / "tts"),
    )
    service.available = True

    path = await service.synthesize(
        "你好",
        voice="zh-CN-XiaoyiNeural",
        rate="+8%",
        pitch="+18Hz",
    )

    assert path is not None
    assert captured == {
        "text": "你好",
        "voice": "zh-CN-XiaoyiNeural",
        "rate": "+8%",
        "pitch": "+18Hz",
    }


@pytest.mark.asyncio
async def test_tts_rejects_invalid_voice_profile_values(tmp_path, monkeypatch):
    captured = {}

    class FakeCommunicate:
        def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
            captured.update(voice=voice, rate=rate, pitch=pitch)

        async def save(self, path):
            Path(path).write_bytes(b"fake-mp3")

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeCommunicate))
    service = _service(
        tmp_path,
        enabled=True,
        tts_dir=str(tmp_path / "media" / "tts"),
        tts_voice="zh-CN-XiaoxiaoNeural",
    )
    service.available = True

    await service.synthesize("你好", voice="bad voice", rate="fast", pitch="high")

    assert captured == {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "pitch": "+0Hz",
    }


@pytest.mark.asyncio
async def test_voice_delivery_falls_back_to_text(tmp_path):
    class _FakeVoice:
        available = True

        async def synthesize(self, text, **_kwargs):
            return None

    websocket = FakeWebSocket()
    platform = QQPlatform(
        qq_config(),
        connector=FakeConnector(websocket),
        voice_service=_FakeVoice(),
    )
    await platform.start()
    message = UnifiedMessage(platform=Platform.QQ, user_id="123", content="hi", message_id="m1")

    await platform.deliver_reply(message, ReplyPlan(bubbles=[], voice_text="你好呀"))

    sends = [payload for payload in websocket.sent if payload["action"] == "send_private_msg"]
    assert len(sends) == 1
    assert sends[0]["params"]["message"] == [{"type": "text", "data": {"text": "你好呀"}}]


@pytest.mark.asyncio
async def test_voice_transcription_falls_back_to_path_and_file_id():
    class FakeVoice:
        available = True

        async def transcribe_bytes(self, _data):
            return None, "你好"

        async def transcribe_url(self, _url):
            return None, ""

    platform = object.__new__(QQPlatform)
    platform.voice_service = FakeVoice()
    encoded = "base64://" + base64.b64encode(b"voice").decode()
    platform._call_action = AsyncMock(
        side_effect=[{"data": {}}, {"data": {"base64": encoded}}]
    )
    ref = MediaRef(
        kind=MessageType.VOICE,
        file="voice.amr",
        path="/napcat/voice.amr",
        file_id="file-id",
    )

    attachment, transcript = await platform._transcribe_voice(ref)

    assert attachment is None
    assert transcript == "你好"
    assert [
        call.args[1]["file"] for call in platform._call_action.await_args_list
    ] == ["voice.amr", "/napcat/voice.amr"]


@pytest.mark.asyncio
async def test_transcribe_consumes_segments_and_releases_model_state(tmp_path):
    service = _service(
        tmp_path,
        enabled=True,
        tts_dir=str(tmp_path / "media" / "tts"),
    )
    service.available = True
    import time

    class FakeModel:
        def transcribe(self, *_args, **_kwargs):
            return iter([type("S", (), {"text": "你好呀"})()]), None

    service._asr = FakeModel()
    service._last_used = time.monotonic()
    path = tmp_path / "a.wav"
    path.write_bytes(b"RIFF")

    assert await service._transcribe(path) == "你好呀"
    assert service._asr is not None


@pytest.mark.asyncio
async def test_overlong_audio_is_rejected_before_asr(tmp_path, monkeypatch):
    service = _service(tmp_path, max_audio_seconds=1)
    service.available = True
    path = tmp_path / "long.wav"
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * 32000)
    path.write_bytes(buffer.getvalue())

    async def fake_fetch(*_args, **_kwargs):
        from media.net import FetchResult

        return FetchResult(content=b"x", content_type="audio/wav", final_url="https://cdn/x")

    monkeypatch.setattr("media.voice.fetch_bytes", fake_fetch)
    monkeypatch.setattr(service, "_decode_to_wav", lambda _data: path)

    attachment, transcript = await service.transcribe_url("https://cdn/x")

    assert attachment is None
    assert transcript == ""
