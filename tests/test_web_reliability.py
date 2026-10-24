import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from core.engine import EngineNotReadyError
from observability import RuntimeMetrics
from utils.logger import get_request_id
from web.app import set_engine
from web.idempotency import IdempotencyCapacityError, IdempotencyConflictError, IdempotencyRegistry
from web.middleware import SecurityAndAuthMiddleware
from web.routes.chat import ChatRequest, api_chat
from web.routes.ws import ConnectionManager, _handle_chat_message


@pytest.mark.asyncio
async def test_pure_asgi_middleware_preserves_stream_chunks_and_context():
    async def streaming_app(scope, receive, send):
        assert get_request_id() == "stream.req-1"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        await send({"type": "http.response.body", "body": b"second", "more_body": False})

    metrics = RuntimeMetrics()
    root_app = SimpleNamespace(state=SimpleNamespace(metrics=metrics))
    middleware = SecurityAndAuthMiddleware(streaming_app, fastapi_app=root_app)
    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/health",
        "raw_path": b"/api/health",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"x-request-id", b"stream.req-1")],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)

    assert [message.get("body") for message in messages[1:]] == [b"first", b"second"]
    headers = dict(messages[0]["headers"])
    assert headers[b"x-request-id"] == b"stream.req-1"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"cache-control"] == b"no-store, private"
    assert metrics.snapshot()["http"]["status_codes"] == {"200": 1}
    assert get_request_id() == ""


@pytest.mark.asyncio
async def test_idempotency_registry_coalesces_and_bounds_requests():
    registry = IdempotencyRegistry(ttl_seconds=60, max_entries=2)
    owner = await registry.claim("first", "same-payload")
    duplicate = await registry.claim("first", "same-payload")
    assert owner.owner is True
    assert duplicate.owner is False
    assert duplicate.future is owner.future

    assert await registry.complete("first", "wrong-token", {"value": 0}) is False
    assert await registry.complete("first", owner.owner_token or "", {"value": 1}) is True
    assert await duplicate.future == {"value": 1}
    assert (await registry.claim("second", "payload")).owner is True
    assert (await registry.claim("first", "same-payload")).owner is False
    assert (await registry.claim("third", "payload")).owner is True
    with pytest.raises(IdempotencyCapacityError):
        await registry.claim("fourth", "payload")


@pytest.mark.asyncio
async def test_idempotency_owner_token_and_payload_fingerprint_are_enforced():
    registry = IdempotencyRegistry(ttl_seconds=60, max_entries=2)
    first = await registry.claim("request", "payload-a")

    with pytest.raises(IdempotencyConflictError):
        await registry.claim("request", "payload-b")
    assert await registry.abandon("request", "not-the-owner", {"error": "stale"}) is False
    assert (await registry.claim("request", "payload-a")).owner is False
    assert await registry.abandon("request", first.owner_token or "", {"retryable": True}) is True

    replacement = await registry.claim("request", "payload-b")
    assert replacement.owner is True
    assert await registry.complete("request", first.owner_token or "", {"value": "stale"}) is False
    assert (await registry.claim("request", "payload-b")).future is replacement.future


@pytest.mark.asyncio
async def test_abandoned_inflight_claim_expires_and_can_be_taken_over():
    registry = IdempotencyRegistry(
        ttl_seconds=60,
        max_entries=1,
        inflight_timeout_seconds=0.01,
    )
    stale = await registry.claim("request", "payload")
    await asyncio.sleep(0.02)

    replacement = await registry.claim("request", "payload")

    assert replacement.owner is True
    assert replacement.owner_token != stale.owner_token
    assert await stale.future == {"error": "请求租约已过期", "retryable": True}
    assert await registry.complete("request", stale.owner_token or "", {"value": "late"}) is False


class _BlockingHttpEngine:
    def __init__(self):
        self.config = SimpleNamespace(web=SimpleNamespace(auth_token=""))
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def process_message(self, _message, **_preferences):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return "one result"


class _StoppingEngine:
    def __init__(self):
        self.config = SimpleNamespace(web=SimpleNamespace(auth_token=""))
        self.is_ready = True
        self.calls = 0

    async def process_message(self, _message, **_preferences):
        self.calls += 1
        raise EngineNotReadyError("engine is shutting down")

    async def process_message_stream(self, _message, **_preferences):
        self.calls += 1
        raise EngineNotReadyError("engine is shutting down")
        yield  # pragma: no cover - keeps this an async generator


@pytest.mark.asyncio
async def test_cancelled_http_waiter_cannot_abandon_the_active_owner():
    engine = _BlockingHttpEngine()
    request = Request({"type": "http", "client": ("127.0.0.1", 1234)})
    payload = ChatRequest(
        message="same operation",
        client_id="waiter-cancellation",
        request_id="waiter-cancellation-case",
    )
    set_engine(engine)
    try:
        owner_task = asyncio.create_task(api_chat(payload, request))
        await engine.entered.wait()

        waiter_task = asyncio.create_task(api_chat(payload, request))
        await asyncio.sleep(0)
        waiter_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter_task

        third_task = asyncio.create_task(api_chat(payload, request))
        await asyncio.sleep(0.01)
        assert engine.calls == 1
        assert not third_task.done()

        engine.release.set()
        owner_result, replayed_result = await asyncio.gather(owner_task, third_task)
        assert owner_result["response"] == "one result"
        assert replayed_result["response"] == "one result"
        assert replayed_result["replayed"] is True
    finally:
        set_engine(None)


@pytest.mark.asyncio
async def test_engine_shutdown_race_is_retryable_for_http_and_releases_idempotency_claim():
    engine = _StoppingEngine()
    request = Request({"type": "http", "client": ("127.0.0.1", 1234)})
    payload = ChatRequest(
        message="shutdown race",
        client_id="shutdown-http",
        request_id="shutdown-http-case",
    )
    set_engine(engine)
    try:
        for _ in range(2):
            with pytest.raises(HTTPException) as captured:
                await api_chat(payload, request)
            assert captured.value.status_code == 503
        assert engine.calls == 2
    finally:
        set_engine(None)


class _StreamingEngine:
    def __init__(self):
        self.config = SimpleNamespace(web=SimpleNamespace(auth_token=""))
        self.is_ready = True
        self.calls = 0
        self.messages = []

    async def process_message_stream(self, message, **preferences):
        self.calls += 1
        self.messages.append((message, preferences))
        yield {"type": "chunk", "content": "hel"}
        yield {"type": "done", "content": "hello", "replace": True}


class _FakeWebSocket:
    def __init__(self):
        self.client = SimpleNamespace(host="127.0.0.1")
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class _HangingWebSocket(_FakeWebSocket):
    async def send_json(self, payload):
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_websocket_broadcast_does_not_let_a_slow_client_block_others():
    manager = ConnectionManager(send_timeout=0.01)
    hanging = _HangingWebSocket()
    fast = _FakeWebSocket()
    manager._connections.extend((hanging, fast))

    await asyncio.wait_for(manager.broadcast({"type": "update"}), timeout=0.1)

    assert fast.sent == [{"type": "update"}]
    assert hanging not in manager._connections
    assert fast in manager._connections


@pytest.mark.asyncio
async def test_engine_shutdown_race_is_retryable_for_websocket():
    engine = _StoppingEngine()
    websocket = _FakeWebSocket()
    payload = {
        "message": "shutdown race",
        "client_id": "shutdown-ws",
        "request_id": "shutdown-ws-case",
    }
    set_engine(engine)
    try:
        await _handle_chat_message(websocket, payload)
        await _handle_chat_message(websocket, payload)
    finally:
        set_engine(None)

    assert engine.calls == 2
    assert [item["code"] for item in websocket.sent] == [
        "service_unavailable",
        "service_unavailable",
    ]


@pytest.mark.asyncio
async def test_websocket_request_id_replays_completed_result():
    engine = _StreamingEngine()
    websocket = _FakeWebSocket()
    set_engine(engine)
    payload = {
        "message": "hello",
        "client_id": "reliable-client",
        "request_id": "websocket-replay-case",
        "style": "professional",
    }
    try:
        await _handle_chat_message(websocket, payload)
        assert websocket.sent[0] == {
            "chunk": "hel",
            "request_id": "websocket-replay-case",
        }
        done = websocket.sent[1]
        assert done["done"] is True
        assert done["full"] == "hello"

        await _handle_chat_message(websocket, payload)
        replay = websocket.sent[2]
        assert replay["done"] is True
        assert replay["full"] == "hello"
        assert replay["replayed"] is True

        assert engine.calls == 1
        message, preferences = engine.messages[0]
        assert message.user_id == "web_reliable-client"
        assert message.message_id == "websocket-replay-case"
        assert preferences["style"] == "professional"
    finally:
        set_engine(None)
