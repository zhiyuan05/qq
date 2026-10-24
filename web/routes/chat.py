from __future__ import annotations

"""Web 面板流式对话。"""
import asyncio
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import web.app as web_app
from core.engine import EngineNotReadyError
from platforms.models import MessageType, Platform, UnifiedMessage
from web.idempotency import (
    IdempotencyCapacityError,
    IdempotencyConflictError,
    request_fingerprint,
    request_registry,
    web_user_id,
)
from web.schemas import ChatResponse

chat_page_router = APIRouter()
chat_api_router = APIRouter(tags=["chat"])
logger = logging.getLogger("web.chat")
_CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CHAT_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "chat.html"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100000)
    card: str = Field(default="", max_length=64)
    style: str = Field(default="", max_length=64)
    client_id: str = Field(default="default", max_length=64)
    request_id: str = Field(default="", pattern=r"^(?:[A-Za-z0-9._-]{1,64})?$")


@chat_page_router.get("/chat")
async def chat_page():
    return FileResponse(_CHAT_TEMPLATE, media_type="text/html")


@chat_api_router.post(
    "/chat",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def api_chat(data: ChatRequest, request: Request):
    engine = web_app.get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="引擎未就绪")
    message = data.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")

    remote_host = request.client.host if request.client else "unknown"
    client_id = data.client_id if _CLIENT_ID_PATTERN.fullmatch(data.client_id) else "default"
    user_id = web_user_id(client_id, remote_host)
    request_id = data.request_id or uuid.uuid4().hex
    msg = UnifiedMessage(
        platform=Platform.CONSOLE,
        user_id=user_id,
        content=message,
        message_type=MessageType.TEXT,
        message_id=request_id,
    )
    registry_key = f"http:{user_id}:{request_id}"

    try:
        claim = await request_registry.claim(
            registry_key,
            request_fingerprint(message, data.card, data.style),
        )
    except IdempotencyCapacityError:
        raise HTTPException(status_code=503, detail="服务繁忙，请稍后重试") from None
    except IdempotencyConflictError:
        raise HTTPException(status_code=409, detail="request_id 已用于不同请求") from None
    if not claim.owner:
        try:
            result = await asyncio.wait_for(
                asyncio.shield(claim.future),
                timeout=request_registry.inflight_timeout_seconds,
            )
        except TimeoutError:
            raise HTTPException(status_code=503, detail="请求仍在处理中，请稍后重试") from None
        return {**result, "replayed": True}

    owner_token = claim.owner_token or ""
    try:
        response = await engine.process_message(
            msg,
            character_card=data.card,
            style=data.style,
        )
        from web.routes.ws import manager

        payload = {"response": response or "", "request_id": request_id}
        await request_registry.complete(registry_key, owner_token, payload)
        await manager.broadcast({"type": "stats_updated"})
        return payload
    except EngineNotReadyError:
        await request_registry.abandon(
            registry_key,
            owner_token,
            {"error": "引擎正在关闭，请稍后重试", "request_id": request_id, "retryable": True},
        )
        raise HTTPException(status_code=503, detail="引擎正在关闭，请稍后重试") from None
    except asyncio.CancelledError:
        await request_registry.abandon(
            registry_key,
            owner_token,
            {"error": "请求已取消，可安全重试", "request_id": request_id, "retryable": True},
        )
        raise
    except HTTPException:
        await request_registry.abandon(
            registry_key,
            owner_token,
            {"error": "请求未完成，可安全重试", "request_id": request_id, "retryable": True},
        )
        raise
    except Exception:
        await request_registry.abandon(
            registry_key,
            owner_token,
            {"error": "请求失败，可安全重试", "request_id": request_id, "retryable": True},
        )
        logger.exception("Web 对话处理失败")
        raise HTTPException(status_code=500, detail="处理消息时发生内部错误")
