"""WebSocket 实时更新 + 流式对话"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from web.idempotency import (
    IdempotencyCapacityError,
    IdempotencyConflictError,
    request_fingerprint,
    request_registry,
    web_user_id,
)

ws_router = APIRouter()
logger = logging.getLogger("web.ws")
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _origin_allowed(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return True
    return urlparse(origin).netloc == ws.headers.get("host", "")


async def _authorize(ws: WebSocket) -> bool:
    from web.routes.auth import check_websocket_auth

    if not _origin_allowed(ws):
        await ws.close(code=4403, reason="来源不受信任")
        return False
    if not check_websocket_auth(ws):
        await ws.close(code=4401, reason="未认证")
        return False
    return True


class ConnectionManager:
    def __init__(self, send_timeout: float = 2.0):
        self._connections: list[WebSocket] = []
        self._send_timeout = send_timeout

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        from web.app import get_metrics

        get_metrics().websocket_connected()

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)
            from web.app import get_metrics

            get_metrics().websocket_disconnected()

    async def broadcast(self, data: dict):
        async def send_one(ws: WebSocket) -> WebSocket | None:
            try:
                await asyncio.wait_for(ws.send_json(data), timeout=self._send_timeout)
                return None
            except Exception:
                return ws

        connections = tuple(self._connections)
        dead = [ws for ws in await asyncio.gather(*(send_one(ws) for ws in connections)) if ws is not None]
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


async def _handle_chat_message(ws: WebSocket, data) -> None:
    if not isinstance(data, dict):
        await ws.send_json({"error": "请求必须是 JSON 对象"})
        return

    supplied_request_id = data.get("request_id")
    if supplied_request_id is not None and (
        not isinstance(supplied_request_id, str)
        or not _REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
    ):
        await ws.send_json({"error": "request_id 格式无效"})
        return
    request_id = supplied_request_id or uuid.uuid4().hex

    raw_message = data.get("message")
    message = raw_message.strip() if isinstance(raw_message, str) else ""
    if not message:
        await ws.send_json({"error": "消息不能为空", "request_id": request_id})
        return

    from web.app import get_engine

    engine = get_engine()
    if not engine or not getattr(engine, "is_ready", True):
        await ws.send_json({"error": "引擎未就绪", "request_id": request_id})
        return

    card = data.get("card") if isinstance(data.get("card"), str) else ""
    style = data.get("style") if isinstance(data.get("style"), str) else ""
    client_id = data.get("client_id") if isinstance(data.get("client_id"), str) else ""
    if not _CLIENT_ID_PATTERN.fullmatch(client_id):
        client_id = "default"
    remote_host = ws.client.host if ws.client else "unknown"
    user_id = web_user_id(client_id, remote_host)
    registry_key = f"{user_id}:{request_id}"

    try:
        claim = await request_registry.claim(
            registry_key,
            request_fingerprint(message, card, style),
        )
    except IdempotencyCapacityError:
        await ws.send_json({"error": "服务繁忙，请稍后重试", "request_id": request_id})
        return
    except IdempotencyConflictError:
        await ws.send_json(
            {
                "error": "request_id 已用于不同请求",
                "code": "idempotency_conflict",
                "request_id": request_id,
            }
        )
        return
    if not claim.owner:
        try:
            result = await asyncio.wait_for(
                asyncio.shield(claim.future),
                timeout=request_registry.inflight_timeout_seconds,
            )
        except TimeoutError:
            await ws.send_json(
                {
                    "error": "请求仍在处理中，请稍后重试",
                    "request_id": request_id,
                    "retryable": True,
                }
            )
            return
        await ws.send_json({**result, "replayed": True})
        return
    owner_token = claim.owner_token or ""

    from platforms.models import MessageType, Platform, UnifiedMessage
    from core.engine import EngineNotReadyError

    msg = UnifiedMessage(
        platform=Platform.CONSOLE,
        user_id=user_id,
        content=message,
        message_type=MessageType.TEXT,
        message_id=request_id,
    )
    final_payload = None
    stream = engine.process_message_stream(
        msg,
        character_card=card,
        style=style,
    )
    committed = False
    try:
        async for event in stream:
            if event["type"] == "chunk":
                await ws.send_json({"chunk": event["content"], "request_id": request_id})
            else:
                final_payload = {
                    "done": True,
                    "full": event["content"],
                    "replace": event.get("replace", False),
                    "request_id": request_id,
                }

        final_payload = final_payload or {
            "done": True,
            "full": "",
            "replace": False,
            "request_id": request_id,
        }
        await request_registry.complete(registry_key, owner_token, final_payload)
        committed = True
        await ws.send_json(final_payload)
        await manager.broadcast({"type": "stats_updated"})
    except EngineNotReadyError:
        error = {
            "error": "引擎正在关闭，请稍后重试",
            "code": "service_unavailable",
            "request_id": request_id,
            "retryable": True,
        }
        await request_registry.abandon(registry_key, owner_token, error)
        await ws.send_json(error)
    except WebSocketDisconnect:
        if not committed:
            await request_registry.abandon(
                registry_key,
                owner_token,
                {"error": "请求已中断，可安全重试", "request_id": request_id, "retryable": True},
            )
        raise
    except asyncio.CancelledError:
        if not committed:
            await request_registry.abandon(
                registry_key,
                owner_token,
                {"error": "请求已取消，可安全重试", "request_id": request_id, "retryable": True},
            )
        raise
    except Exception:
        logger.exception("WebSocket 对话消息处理失败")
        error = {
            "error": "处理消息时发生内部错误",
            "request_id": request_id,
            "retryable": True,
        }
        if not committed:
            await request_registry.abandon(registry_key, owner_token, error)
        await ws.send_json(error)
    finally:
        await stream.aclose()


@ws_router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """通用 WebSocket 心跳端点。"""
    if not await _authorize(ws):
        return
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
            from web.app import get_metrics

            get_metrics().websocket_message()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)


@ws_router.websocket("/ws/chat")
async def websocket_chat(ws: WebSocket):
    """流式对话 WebSocket。

    客户端发送 JSON: {"message": "...", "card?": "...", "style?": "..."}
    服务端逐个推送 JSON: {"chunk": "..."} 最后推送 {"done": true, "full": "..."}
    """
    if not await _authorize(ws):
        return
    await ws.accept()
    from web.app import get_metrics

    metrics = get_metrics()
    metrics.websocket_connected()
    try:
        while True:
            data = await ws.receive_json()
            metrics.websocket_message()
            await _handle_chat_message(ws, data)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket 对话失败")
        try:
            await ws.send_json({"error": "处理消息时发生内部错误"})
        except Exception:
            pass
    finally:
        metrics.websocket_disconnected()
