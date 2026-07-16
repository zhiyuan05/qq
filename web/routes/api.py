"""API routes for web管理面板"""
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import web.app as web_app

api_router = APIRouter()


@api_router.get("/api/health")
async def api_health():
    uptime = int(time.time() - web_app._start_time)
    return {"status": "ok", "uptime_seconds": uptime}


@api_router.get("/api/stats")
async def api_stats():
    engine = web_app._engine
    if not engine:
        return {"session_count": 0, "card_count": 0, "style_count": 0, "entity_count": 0}
    return {
        "session_count": len(engine.session_manager.list_sessions()),
        "card_count": len(engine.character_manager.list_cards()),
        "style_count": len(engine.style_engine.list_styles()),
        "entity_count": sum(len(v) for v in engine.entity_memory._entities.values()),
    }


@api_router.get("/api/sessions")
async def api_sessions():
    engine = web_app._engine
    if not engine:
        return []
    sessions = []
    for sid in engine.session_manager.list_sessions():
        session = engine.session_manager.get(sid)
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
    engine = web_app._engine
    if not engine:
        return {"error": "引擎未就绪"}
    session = engine.session_manager.get(session_id)
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
    engine = web_app._engine
    if not engine:
        return []
    return engine.character_manager.list_cards()


@api_router.get("/api/cards/{card_name}")
async def api_card_detail(card_name: str):
    engine = web_app._engine
    if not engine:
        return {"error": "引擎未就绪"}
    card = engine.character_manager.get(card_name)
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
    engine = web_app._engine
    if not engine:
        return []
    return engine.style_engine.list_styles()


@api_router.post("/api/cards")
async def api_create_card(data: dict):
    engine = web_app._engine
    if not engine:
        return JSONResponse({"error": "引擎未就绪"}, status_code=503)
    name = data.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "名称不能为空"}, status_code=400)
    from characters.manager import CharacterCard
    rules = data.get("rules", [])
    if isinstance(rules, str):
        rules = [r.strip() for r in rules.split("\n") if r.strip()]
    card = CharacterCard({
        "name": name,
        "description": data.get("description", ""),
        "system_prompt": data.get("system_prompt", f"你是{name}。"),
        "rules": rules,
        "greeting": data.get("greeting", ""),
    })
    engine.character_manager.save(name, card)
    return {"ok": True, "name": name}


@api_router.put("/api/cards/{card_name}")
async def api_update_card(card_name: str, data: dict):
    engine = web_app._engine
    if not engine:
        return JSONResponse({"error": "引擎未就绪"}, status_code=503)
    existing = engine.character_manager.get(card_name)
    if not existing:
        return JSONResponse({"error": "角色卡不存在"}, status_code=404)
    from characters.manager import CharacterCard
    rules = data.get("rules", existing.rules)
    if isinstance(rules, str):
        rules = [r.strip() for r in rules.split("\n") if r.strip()]
    updated = CharacterCard({
        "name": data.get("name", existing.name),
        "description": data.get("description", existing.description),
        "skills": existing.skills,
        "system_prompt": data.get("system_prompt", existing.system_prompt),
        "rules": rules,
        "personality": existing.personality,
        "greeting": data.get("greeting", existing.greeting),
        "examples": existing.examples,
        "learned_traits": existing.learned_traits,
    })
    engine.character_manager.save(card_name, updated)
    return {"ok": True, "name": card_name}


@api_router.get("/api/stats/detailed")
async def api_stats_detailed():
    engine = web_app._engine
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
    }


@api_router.get("/api/learning/{card_name}")
async def api_learning_history(card_name: str):
    from learning.evolution import EvolutionManager
    em = EvolutionManager()
    return em.get_evolution_history(card_name)


@api_router.get("/api/sessions/{session_id}/export")
async def api_export_session(session_id: str):
    engine = web_app._engine
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
