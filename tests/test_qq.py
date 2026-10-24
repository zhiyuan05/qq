"""OneBot v11 WebSocket adapter boundary tests."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from platforms.base import PlatformConfigurationError, PlatformDeliveryError, PlatformState
from platforms.models import Platform
from platforms.qq import QQPlatform


class FakeWebSocket:
    _CLOSE = object()

    def __init__(self, *, reject_actions: bool = False):
        self.incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.reject_actions = reject_actions

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is self._CLOSE:
            raise StopAsyncIteration
        return value

    async def send(self, frame: str) -> None:
        payload = json.loads(frame)
        self.sent.append(payload)
        response = {
            "status": "failed" if self.reject_actions else "ok",
            "retcode": 100 if self.reject_actions else 0,
            "data": None if self.reject_actions else {"message_id": 88},
            "echo": payload["echo"],
        }
        await self.incoming.put(json.dumps(response))

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self.incoming.put(self._CLOSE)


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket):
        self.websocket = websocket

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_args):
        await self.websocket.close()


class FakeConnector:
    def __init__(self, websocket: FakeWebSocket):
        self.websocket = websocket
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return FakeConnection(self.websocket)


def qq_config(**overrides):
    config = {
        "onebot_url": "ws://onebot.test:3001/",
        "access_token": "top-secret",
        "connect_timeout": 0.2,
        "api_timeout": 0.2,
        "reconnect_min_seconds": 0,
        "reconnect_max_seconds": 0,
        "event_queue_size": 10,
    }
    config.update(overrides)
    return config


@pytest.mark.asyncio
async def test_onebot_socket_routes_action_responses_without_receive_deadlock():
    websocket = FakeWebSocket()
    connector = FakeConnector(websocket)
    platform = QQPlatform(qq_config(), connector=connector)

    await platform.start()
    receipt = await platform.send_message("123", "hello")
    await platform.start()

    assert receipt.platform is Platform.QQ
    assert receipt.target_id == "123"
    assert receipt.message_id == "88"
    assert websocket.sent[0]["action"] == "send_private_msg"
    assert websocket.sent[0]["params"] == {"user_id": 123, "message": "hello"}
    assert connector.calls[0][1]["additional_headers"] == {"Authorization": "Bearer top-secret"}
    assert len(connector.calls) == 1

    await platform.stop()
    assert websocket.closed is True
    assert platform.state is PlatformState.STOPPED
    assert not platform._tasks


@pytest.mark.asyncio
async def test_onebot_event_worker_normalizes_message_and_sends_group_response():
    websocket = FakeWebSocket()
    platform = QQPlatform(qq_config(), connector=FakeConnector(websocket))
    received = []
    completed = asyncio.Event()

    async def callback(message):
        received.append(message)
        completed.set()
        return "world"

    platform.on_message(callback)
    await platform.start()
    event = {
        "post_type": "message",
        "message_type": "group",
        "self_id": 999,
        "user_id": 123,
        "group_id": 456,
        "message_id": 7,
        "message": [
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": " hello"}},
        ],
        "sender": {"nickname": "Alice"},
    }
    await websocket.incoming.put(json.dumps(event))
    await asyncio.wait_for(completed.wait(), timeout=0.5)
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].content == "[CQ:at,qq=999] hello"
    assert received[0].group_id == "456"
    assert received[0].raw_data is event or received[0].raw_data == event
    assert websocket.sent[0]["action"] == "send_group_msg"
    assert websocket.sent[0]["params"]["group_id"] == 456
    await platform.stop()


@pytest.mark.asyncio
async def test_onebot_rejected_action_raises_typed_delivery_error():
    platform = QQPlatform(qq_config(), connector=FakeConnector(FakeWebSocket(reject_actions=True)))
    await platform.start()

    with pytest.raises(PlatformDeliveryError) as captured:
        await platform.send_message("123", "hello")

    assert captured.value.code == 100
    assert platform.status_snapshot()["stats"]["send_failures"] == 1
    await platform.stop()


@pytest.mark.asyncio
async def test_onebot_filters_unauthorized_user_and_group_before_callback():
    platform = QQPlatform(qq_config(allowed_users=["1"], allowed_groups=["10"]))
    called = 0

    async def callback(_message):
        nonlocal called
        called += 1

    platform.on_message(callback)
    await platform._process_raw_message(
        {"post_type": "message", "message_type": "private", "user_id": 2, "message": "blocked"}
    )
    await platform._process_raw_message(
        {"post_type": "message", "message_type": "group", "user_id": 1, "group_id": 11, "message": "blocked"}
    )

    assert called == 0
    assert platform.status_snapshot()["stats"]["ignored"] == 2


@pytest.mark.asyncio
async def test_onebot_invalid_transport_scheme_fails_before_spawning_tasks():
    platform = QQPlatform(qq_config(onebot_url="http://onebot.test:3001"))

    with pytest.raises(PlatformConfigurationError):
        await platform.start()

    assert platform.state is PlatformState.FAILED
    assert not platform._tasks


@pytest.mark.asyncio
async def test_onebot_queue_overflow_is_visible_in_stats():
    platform = QQPlatform(qq_config(event_queue_size=1))
    await platform._route_frame(json.dumps({"post_type": "meta_event", "id": 1}))
    await platform._route_frame(json.dumps({"post_type": "meta_event", "id": 2}))

    assert platform.status_snapshot()["stats"]["dropped"] == 1


@pytest.mark.asyncio
async def test_event_worker_processes_independent_sessions_concurrently():
    websocket = FakeWebSocket()
    platform = QQPlatform(
        qq_config(event_concurrency=2),
        connector=FakeConnector(websocket),
    )
    entered = 0
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def callback(_message):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await release.wait()
        return "ok"

    platform.on_message(callback)
    await platform.start()
    for user_id in (101, 102):
        await websocket.incoming.put(
            json.dumps(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "self_id": 999,
                    "user_id": user_id,
                    "message_id": user_id,
                    "message": [{"type": "text", "data": {"text": "hi"}}],
                }
            )
        )

    await asyncio.wait_for(both_entered.wait(), timeout=0.5)
    release.set()
    await platform.stop()
    assert entered == 2
