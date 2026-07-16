"""API routes for web管理面板"""
import time

from fastapi import APIRouter

from web.app import _engine, _start_time

api_router = APIRouter()


@api_router.get("/api/health")
async def api_health():
    uptime = int(time.time() - _start_time)
    return {"status": "ok", "uptime_seconds": uptime}


@api_router.get("/api/stats")
async def api_stats():
    if not _engine:
        return {"session_count": 0, "card_count": 0, "style_count": 0, "entity_count": 0}
    return {
        "session_count": len(_engine.session_manager.list_sessions()),
        "card_count": len(_engine.character_manager.list_cards()),
        "style_count": len(_engine.style_engine.list_styles()),
        "entity_count": sum(len(v) for v in _engine.entity_memory._entities.values()),
    }


@api_router.get("/api/sessions")
async def api_sessions():
    if not _engine:
        return []
    sessions = []
    for sid in _engine.session_manager.list_sessions():
        session = _engine.session_manager.get(sid)
        if session:
            sessions.append({
                "id": sid,
                "platform": session.platform,
                "character_card": session.character_card,
                "style": session.style,
                "message_count": len(session.messages),
                "last_message": session.messages[-1].content[:50] if session.messages else "",
            })
    return sessions


@api_router.get("/api/sessions/{session_id}")
async def api_session_detail(session_id: str):
    if not _engine:
        return {"error": "引擎未就绪"}
    session = _engine.session_manager.get(session_id)
    if not session:
        return {"error": "会话不存在"}
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


@api_router.get("/api/cards")
async def api_cards():
    if not _engine:
        return []
    return _engine.character_manager.list_cards()


@api_router.get("/api/cards/{card_name}")
async def api_card_detail(card_name: str):
    if not _engine:
        return {"error": "引擎未就绪"}
    card = _engine.character_manager.get(card_name)
    if not card:
        return {"error": "角色卡不存在"}
    return {
        "name": card.name,
        "description": card.description,
        "rules": card.rules,
        "system_prompt": card.get_system_prompt()[:500],
    }


@api_router.get("/api/styles")
async def api_styles():
    if not _engine:
        return []
    return _engine.style_engine.list_styles()


@api_router.get("/api/learning/{card_name}")
async def api_learning_history(card_name: str):
    from learning.evolution import EvolutionManager
    em = EvolutionManager()
    return em.get_evolution_history(card_name)


@api_router.get("/api/sessions/{session_id}/export")
async def api_export_session(session_id: str):
    if not _engine:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "引擎未就绪"}, status_code=503)
    session = _engine.session_manager.get(session_id)
    if not session:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    lines = []
    for m in session.messages:
        role = "我" if m.role == "user" else "AI"
        lines.append(f"[{role}] {m.content}")
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "session_id": session_id,
        "platform": session.platform,
        "character_card": session.character_card,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "messages": lines,
    })
