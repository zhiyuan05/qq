from __future__ import annotations

"""API routes for web管理面板"""
import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

import web.app as web_app
from web.schemas import (
    CardDetailResponse,
    DetailedStatsResponse,
    HealthResponse,
    LogsResponse,
    MutationResponse,
    PlatformStatusResponse,
    ReadinessResponse,
    SessionDetailResponse,
    SessionExportResponse,
    SessionListResponse,
    StatsResponse,
)

api_router = APIRouter(tags=["management"])


class CardPayload(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=4000)
    system_prompt: str = Field(default="", max_length=50000)
    rules: list[str] | str = Field(default_factory=list)
    greeting: str = Field(default="", max_length=4000)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        from characters.manager import validate_card_name

        return validate_card_name(value)

    @field_validator("rules")
    @classmethod
    def validate_rules(cls, value: list[str] | str) -> list[str]:
        rules = value.splitlines() if isinstance(value, str) else value
        cleaned = [rule.strip() for rule in rules if rule.strip()]
        if len(cleaned) > 100 or any(len(rule) > 1000 for rule in cleaned):
            raise ValueError("规则最多 100 条，每条不超过 1000 字")
        return cleaned


@api_router.get("/health", response_model=HealthResponse)
async def api_health():
    uptime = int(time.time() - web_app._start_time)
    return {"status": "ok", "check": "liveness", "uptime_seconds": uptime}


def _probe_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".ready-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _media_writable(media_store) -> bool:
    return _probe_writable(Path(media_store.config.storage_dir))


def _stickers_writable(sticker_library) -> bool:
    return _probe_writable(Path(sticker_library.database_path).parent)


@api_router.get("/health/ready", response_model=ReadinessResponse, response_model_exclude_none=True)
async def api_health_ready():
    """验证引擎及其关键持久化依赖是否已就绪。"""

    engine = web_app.get_engine()
    checks = {
        "engine": bool(engine and engine.is_ready),
        "persistence": True,
    }
    if engine and engine.session_store:
        checks["persistence"] = await engine.session_store.health_check()
    if engine:
        media_store = getattr(engine, "media_store", None)
        if media_store is not None:
            checks["media"] = _media_writable(media_store)
        sticker_library = getattr(engine, "sticker_library", None)
        if sticker_library is not None:
            checks["stickers"] = _stickers_writable(sticker_library)
    ready = all(checks.values())
    return JSONResponse(
        {
            "status": "ok" if ready else "not_ready",
            "check": "readiness",
            "checks": checks,
            "uptime_seconds": int(time.time() - web_app._start_time),
        },
        status_code=200 if ready else 503,
    )


@api_router.get("/usage", response_model=dict[str, Any])
async def api_usage(
    session_id: str = "",
    days: Annotated[int, Query(ge=1, le=90)] = 1,
):
    engine = web_app.get_engine()
    ledger = getattr(engine, "usage_ledger", None) if engine else None
    if ledger is None:
        return JSONResponse({"error": "Token 账本未启用"}, status_code=503)
    totals = await ledger.aggregate(session_id=session_id, days=days)
    totals.update(ledger.budget_snapshot(await ledger.daily_spend_micros()))
    return totals


@api_router.get("/metrics", response_model=dict[str, Any])
async def api_metrics():
    metrics = {"runtime": web_app.get_metrics().snapshot()}
    from web.idempotency import request_registry

    metrics["idempotency"] = await request_registry.stats()
    engine = web_app.get_engine()
    if engine:
        metrics["engine"] = engine.runtime_stats()
        if engine.session_store:
            try:
                metrics["persistence"] = await engine.session_store.stats()
            except Exception:
                metrics["persistence"] = {"status": "unavailable"}
        else:
            metrics["persistence"] = {"backend": "disabled"}
    else:
        metrics["engine"] = {"ready": False}
        metrics["persistence"] = {"backend": "disabled"}
    return metrics


@api_router.get("/stats", response_model=StatsResponse)
async def api_stats():
    engine = web_app.get_engine()
    if not engine:
        return {"session_count": 0, "card_count": 0, "style_count": 0, "entity_count": 0}
    return {
        "session_count": len(engine.session_manager.list_sessions()),
        "card_count": len(engine.character_manager.list_cards()),
        "style_count": len(engine.style_engine.list_styles()),
        "entity_count": 0,  # entity memory is async; detailed stats via /api/stats/detailed
    }


@api_router.get("/sessions", response_model=SessionListResponse)
async def api_sessions(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    engine = web_app.get_engine()
    if not engine:
        return {"sessions": [], "total": 0}
    all_ids = engine.session_manager.list_sessions()
    total = len(all_ids)
    page_ids = all_ids[offset:offset + limit]
    sessions = []
    for sid in page_ids:
        s = engine.session_manager.get(sid)
        if s:
            sessions.append({
                "id": sid,
                "platform": s.platform,
                "character_card": s.character_card,
                "style": s.style,
                "message_count": len(s.messages),
                "last_message": s.messages[-1].content[:50] if s.messages else "",
            })
    return {"sessions": sessions, "total": total, "offset": offset, "limit": limit}


@api_router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def api_session_detail(session_id: str):
    engine = web_app.get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="引擎未就绪")
    session = engine.session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = []
    for m in session.messages[-50:]:
        messages.append({"role": m.role, "content": m.content, "tokens": m.token_count})
    return {
        "id": session_id,
        "platform": session.platform,
        "character_card": session.character_card,
        "style": session.style,
        "summary": session.summary or "",
        "messages": messages,
    }


@api_router.get("/cards", response_model=list[str])
async def api_cards():
    engine = web_app.get_engine()
    if not engine:
        return []
    return engine.character_manager.list_cards()


@api_router.get("/cards/{card_name}", response_model=CardDetailResponse)
async def api_card_detail(card_name: str):
    engine = web_app.get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="引擎未就绪")
    card = engine.character_manager.get(card_name)
    if not card:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    return {
        "name": card.name,
        "description": card.description,
        "rules": card.rules,
        # 管理 API 返回可无损编辑的原始配置；运行时渲染后的提示词属于 Engine 内部细节。
        "system_prompt": card.system_prompt,
        "greeting": card.greeting,
    }


@api_router.get("/styles", response_model=list[str])
async def api_styles():
    engine = web_app.get_engine()
    if not engine:
        return []
    return engine.style_engine.list_styles()


@api_router.post("/cards", response_model=MutationResponse)
async def api_create_card(data: CardPayload):
    engine = web_app.get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="引擎未就绪")
    name = data.name
    if engine.character_manager.get(name):
        raise HTTPException(status_code=409, detail="角色卡已存在，请使用编辑")
    from characters.manager import CharacterCard
    card = CharacterCard({
        "name": name,
        "description": data.description,
        "system_prompt": data.system_prompt or f"你是{name}。",
        "rules": data.rules,
        "greeting": data.greeting,
    })
    engine.character_manager.save(name, card)
    return {"ok": True, "name": name}


@api_router.put("/cards/{card_name}", response_model=MutationResponse)
async def api_update_card(card_name: str, data: CardPayload):
    engine = web_app.get_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="引擎未就绪")
    existing = engine.character_manager.get(card_name)
    if not existing:
        raise HTTPException(status_code=404, detail="角色卡不存在")
    from characters.manager import CharacterCard
    updated = CharacterCard({
        "name": data.name,
        "description": data.description,
        "skills": existing.skills,
        "system_prompt": data.system_prompt,
        "rules": data.rules,
        "personality": existing.personality,
        "greeting": data.greeting,
        "examples": existing.examples,
        "learned_traits": existing.learned_traits,
    })
    engine.character_manager.backup_card(card_name)
    engine.character_manager.save(card_name, updated)
    return {"ok": True, "name": card_name}


@api_router.get("/stats/detailed", response_model=DetailedStatsResponse)
async def api_stats_detailed():
    engine = web_app.get_engine()
    if not engine:
        return {}
    sessions = engine.session_manager.list_sessions()
    cards = engine.character_manager.list_cards()
    platform_counts = {}
    total_msgs = 0
    for sid in sessions:
        s = engine.session_manager.get(sid)
        if s:
            p = s.platform or "unknown"
            platform_counts[p] = platform_counts.get(p, 0) + 1
            total_msgs += len(s.messages)
    return {
        "total_sessions": len(sessions),
        "total_messages": total_msgs,
        "total_cards": len(cards),
        "by_platform": platform_counts,
        "uptime_seconds": int(time.time() - web_app._start_time),
        "runtime": web_app.get_metrics().snapshot(),
    }


@api_router.get("/learning/{card_name}", response_model=list[dict[str, Any]])
async def api_learning_history(card_name: str):
    from learning.evolution import EvolutionManager
    em = EvolutionManager()
    return em.get_evolution_history(card_name)


# ── Persona ──────────────────────────────────────────────────


class PersonaRollbackPayload(BaseModel):
    scope: Literal["global", "user"] = "user"
    scope_key: str = Field(default="", max_length=200)
    steps: int = Field(default=1, ge=1, le=10)


def _persona_store():
    engine = web_app.get_engine()
    return getattr(engine, "persona_store", None) if engine else None


def _trait_payload(trait) -> dict[str, Any]:
    return {
        "id": trait.id,
        "scope": trait.scope,
        "scope_key": trait.scope_key,
        "category": trait.category,
        "trait_key": trait.trait_key,
        "value": trait.value,
        "confidence": round(trait.confidence, 4),
        "tier": trait.tier,
        "status": trait.status,
        "source_sessions": list(trait.source_sessions),
    }


@api_router.get("/persona", response_model=dict[str, Any])
async def api_persona(
    scope: Literal["global", "user"] = "global",
    scope_key: str = "",
):
    store = _persona_store()
    if store is None:
        return JSONResponse({"error": "人格演化未启用"}, status_code=503)
    resolved = "global" if scope == "global" else scope_key
    if not resolved:
        raise HTTPException(status_code=400, detail="user scope 需要 scope_key")
    traits = await store.show(scope, resolved)
    return {"scope": scope, "scope_key": resolved, "traits": [_trait_payload(trait) for trait in traits]}


@api_router.post("/persona/rollback", response_model=MutationResponse)
async def api_persona_rollback(payload: PersonaRollbackPayload):
    store = _persona_store()
    if store is None:
        return JSONResponse({"error": "人格演化未启用"}, status_code=503)
    resolved = "global" if payload.scope == "global" else payload.scope_key
    if not resolved:
        raise HTTPException(status_code=400, detail="user scope 需要 scope_key")
    ok = await store.rollback(payload.scope, resolved, payload.steps)
    return {"ok": ok, "name": "persona"}


@api_router.delete("/persona/traits", response_model=MutationResponse)
async def api_persona_forget(
    keyword: Annotated[str, Query(min_length=1, max_length=100)],
    scope: Literal["global", "user"] = "user",
    scope_key: str = "",
):
    store = _persona_store()
    if store is None:
        return JSONResponse({"error": "人格演化未启用"}, status_code=503)
    resolved = "global" if scope == "global" else scope_key
    if not resolved:
        raise HTTPException(status_code=400, detail="user scope 需要 scope_key")
    removed = await store.forget(scope, resolved, keyword)
    return {"ok": removed > 0, "name": "persona"}


@api_router.post("/persona/approve/{trait_id}", response_model=MutationResponse)
async def api_persona_approve(trait_id: int):
    store = _persona_store()
    if store is None:
        return JSONResponse({"error": "人格演化未启用"}, status_code=503)
    ok = await store.approve("global", "global", trait_id)
    return {"ok": ok, "name": "persona"}


# ── Stickers ─────────────────────────────────────────────────


def _sticker_library():
    engine = web_app.get_engine()
    return getattr(engine, "sticker_library", None) if engine else None


def _sticker_payload(sticker) -> dict[str, Any]:
    return {
        "hash": sticker.hash,
        "tags": list(sticker.tags),
        "emotion": sticker.emotion,
        "source": sticker.source,
        "status": sticker.status,
        "used_count": sticker.used_count,
    }


@api_router.get("/stickers", response_model=dict[str, Any])
async def api_stickers(
    status: str = "",
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
):
    library = _sticker_library()
    if library is None:
        return JSONResponse({"error": "表情包功能未启用"}, status_code=503)
    stickers = await library.list(status, limit)
    return {"status": status, "stickers": [_sticker_payload(sticker) for sticker in stickers]}


@api_router.post("/stickers/{digest}/approve", response_model=MutationResponse)
async def api_sticker_approve(digest: str):
    library = _sticker_library()
    if library is None:
        return JSONResponse({"error": "表情包功能未启用"}, status_code=503)
    return {"ok": await library.approve(digest), "name": "sticker"}


@api_router.post("/stickers/{digest}/reject", response_model=MutationResponse)
async def api_sticker_reject(digest: str):
    library = _sticker_library()
    if library is None:
        return JSONResponse({"error": "表情包功能未启用"}, status_code=503)
    return {"ok": await library.reject(digest), "name": "sticker"}


@api_router.get("/sessions/{session_id}/export", response_model=SessionExportResponse)
async def api_export_session(session_id: str):
    engine = web_app.get_engine()
    if not engine:
        return JSONResponse({"error": "引擎未就绪"}, status_code=503)
    session = engine.session_manager.get(session_id)
    if not session:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    lines = []
    for m in session.messages:
        role = "我" if m.role == "user" else "AI"
        lines.append(f"[{role}] {m.content}")
    return JSONResponse({
        "session_id": session_id,
        "platform": session.platform,
        "character_card": session.character_card,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "messages": lines,
    })


# ── Logs ─────────────────────────────────────────────────────


@api_router.get("/logs", response_model=LogsResponse, response_model_exclude_none=True)
async def api_logs(lines: Annotated[int, Query(ge=1, le=500)] = 50):
    engine = web_app.get_engine()
    if not engine:
        return {"lines": []}
    def _tail() -> list[str]:
        with open(engine.config.logging.file, "r", encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=lines))

    try:
        return {"lines": await asyncio.to_thread(_tail)}
    except Exception:
        return {"lines": [], "error": "无法读取日志"}


@api_router.get("/platforms/status", response_model=PlatformStatusResponse)
async def api_platforms_status():
    engine = web_app.get_engine()
    if not engine:
        return {"platforms": []}
    return {"platforms": engine.platform_statuses()}

