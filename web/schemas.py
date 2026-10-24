"""稳定的 Web API 请求/响应契约。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OkResponse(BaseModel):
    ok: bool = True


class HealthResponse(BaseModel):
    status: str
    check: str
    uptime_seconds: int = Field(ge=0)


class ReadinessChecks(BaseModel):
    engine: bool
    persistence: bool
    media: bool | None = None
    stickers: bool | None = None


class ReadinessResponse(HealthResponse):
    checks: ReadinessChecks


class StatsResponse(BaseModel):
    session_count: int = 0
    card_count: int = 0
    style_count: int = 0
    entity_count: int = 0


class SessionSummary(BaseModel):
    id: str
    platform: str
    character_card: str
    style: str
    message_count: int
    last_message: str


class SessionListResponse(BaseModel):
    sessions: list[SessionSummary] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 20


class ChatMessageView(BaseModel):
    role: str
    content: str
    tokens: int = 0


class SessionDetailResponse(BaseModel):
    id: str
    platform: str
    character_card: str
    style: str
    summary: str
    messages: list[ChatMessageView]


class CardDetailResponse(BaseModel):
    name: str
    description: str
    rules: list[str]
    system_prompt: str
    greeting: str


class MutationResponse(BaseModel):
    ok: bool
    name: str


class DetailedStatsResponse(BaseModel):
    total_sessions: int = 0
    total_messages: int = 0
    total_cards: int = 0
    by_platform: dict[str, int] = Field(default_factory=dict)
    uptime_seconds: int = 0
    runtime: dict[str, Any] = Field(default_factory=dict)


class SessionExportResponse(BaseModel):
    session_id: str
    platform: str
    character_card: str
    exported_at: str
    messages: list[str]


class LogsResponse(BaseModel):
    lines: list[str] = Field(default_factory=list)
    error: str | None = None


class PlatformStatus(BaseModel):
    name: str
    running: bool
    state: str = "unknown"
    ready: bool = False
    connected: bool | None = None
    last_error: str | None = None
    queue_depth: int | None = None
    offset: int | None = None
    stats: dict[str, int] = Field(default_factory=dict)


class PlatformStatusResponse(BaseModel):
    platforms: list[PlatformStatus] = Field(default_factory=list)


class ChatResponse(BaseModel):
    response: str
    request_id: str
    replayed: bool | None = None
