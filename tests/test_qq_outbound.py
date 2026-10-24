from pathlib import Path

import pytest

from platforms.delivery import OutboundGate
from platforms.models import Platform, ReplyPlan, UnifiedMessage
from platforms.qq import QQPlatform
from security.outbound_guard import OutboundGuard
from tests.test_qq import FakeConnector, FakeWebSocket, qq_config
from utils.config import DeliveryConfig


def _message(group_id=None) -> UnifiedMessage:
    return UnifiedMessage(
        platform=Platform.QQ,
        user_id="123",
        content="hi",
        group_id=group_id,
        message_id="m1",
    )


@pytest.mark.asyncio
async def test_private_plan_sends_segment_arrays_with_reply_on_first_bubble():
    websocket = FakeWebSocket()
    platform = QQPlatform(
        qq_config(),
        connector=FakeConnector(websocket),
        delivery_gate=OutboundGate(DeliveryConfig()),
    )
    await platform.start()

    plan = ReplyPlan(bubbles=["第一句", "第二句"], delays=[0.0], reply_to="m1")
    await platform.deliver_reply(_message(), plan)

    sends = [payload for payload in websocket.sent if payload["action"] == "send_private_msg"]
    assert len(sends) == 2
    assert sends[0]["params"]["message"][0] == {"type": "reply", "data": {"id": "m1"}}
    assert sends[0]["params"]["message"][1] == {"type": "text", "data": {"text": "第一句"}}
    assert sends[1]["params"]["message"] == [{"type": "text", "data": {"text": "第二句"}}]


@pytest.mark.asyncio
async def test_group_plan_uses_legacy_text_message():
    websocket = FakeWebSocket()
    platform = QQPlatform(qq_config(), connector=FakeConnector(websocket))
    await platform.start()

    plan = ReplyPlan(bubbles=["第一句", "第二句"], delays=[0.0], humanized=False)
    await platform.deliver_reply(_message(group_id="456"), plan)

    sends = [payload for payload in websocket.sent if payload["action"] == "send_group_msg"]
    assert len(sends) == 1
    assert sends[0]["params"]["message"] == "第一句\n第二句"


@pytest.mark.asyncio
async def test_retry_resumes_after_a_partial_send():
    class _FlakyPlatform(QQPlatform):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0

        async def _send_segments(self, user_id, segments, group_id=None):
            self.calls += 1
            if self.calls == 2:
                from platforms.base import PlatformDeliveryError

                raise PlatformDeliveryError("boom")
            return await super()._send_segments(user_id, segments, group_id)

    websocket = FakeWebSocket()
    platform = _FlakyPlatform(qq_config(), connector=FakeConnector(websocket))
    await platform.start()
    plan = ReplyPlan(bubbles=["第一句", "第二句"], delays=[0.0], reply_to="m1")

    await platform.deliver_reply(_message(), plan)
    assert plan.delivered_bubbles == 1

    await platform.deliver_reply(_message(), plan)

    texts = [
        segment["data"]["text"]
        for payload in websocket.sent
        if payload["action"] == "send_private_msg"
        for segment in payload["params"]["message"]
        if segment["type"] == "text"
    ]
    assert texts == ["第一句", "第二句"]


@pytest.mark.asyncio
async def test_outbound_guard_blocks_path_leaks():
    websocket = FakeWebSocket()
    platform = QQPlatform(
        qq_config(),
        connector=FakeConnector(websocket),
        outbound_guard=OutboundGuard(["super-secret-token"]),
    )
    await platform.start()

    await platform.deliver_reply(_message(), ReplyPlan(bubbles=["C:\\Users\\me\\secret.txt"]))
    await platform.deliver_reply(_message(), ReplyPlan(bubbles=["super-secret-token"]))

    assert [payload for payload in websocket.sent if payload["action"] == "send_private_msg"] == []


@pytest.mark.asyncio
async def test_sticker_is_copied_to_outbound_dir_and_sent(tmp_path):
    sticker_dir = tmp_path / "stickers"
    sticker_dir.mkdir()
    sticker_path = sticker_dir / "happy.png"
    sticker_path.write_bytes(b"fake-image")

    class _Library:
        root = sticker_dir

        async def mark_used_hash(self, digest):
            return None

    class _Media:
        class _Config:
            storage_dir = str(tmp_path / "media")
            outbound_dir = str(tmp_path / "media" / "outbound")

        config = _Config()

    websocket = FakeWebSocket()
    platform = QQPlatform(
        qq_config(),
        connector=FakeConnector(websocket),
        media_store=_Media(),
        sticker_library=_Library(),
    )
    await platform.start()

    plan = ReplyPlan(bubbles=["看"], sticker_path=str(sticker_path), sticker_hash="h" * 64)
    await platform.deliver_reply(_message(), plan)

    sends = [payload for payload in websocket.sent if payload["action"] == "send_private_msg"]
    image_segments = [
        segment
        for payload in sends
        for segment in payload["params"]["message"]
        if segment["type"] == "image"
    ]
    assert len(image_segments) == 1
    assert image_segments[0]["data"]["file"].startswith("file://")
    assert Path(sticker_path).exists()
