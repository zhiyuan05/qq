"""Lightweight, persistent mood/energy/affinity state for private chats.

The state is deliberately rule-based: it costs no extra LLM call, survives
restarts through entity memory, and only nudges tone instead of writing a
whole personality for the model.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

from memory.entity_memory import EntityMemory
from utils.logger import setup_logger

logger = setup_logger("relationship")

_ENTITY_TYPE = "relationship_state"
_KEY = "state"
_POSITIVE = (
    "开心", "高兴", "喜欢", "谢谢", "爱你", "好玩", "不错", "哈哈",
    "嘿嘿", "太好了", "棒", "舒服", "期待",
)
_NEGATIVE = (
    "难过", "烦", "累", "生气", "讨厌", "伤心", "焦虑", "压力",
    "崩溃", "无聊", "失望", "唉", "孤独",
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class RelationshipState:
    mood: float = 0.0          # -1 sad .. 1 cheerful
    energy: float = 0.7        # 0 exhausted .. 1 energetic
    affinity: float = 1.0      # 0 stranger .. 10 close
    last_active: float = 0.0
    turns: int = 0


def update_relationship(
    state: RelationshipState,
    text: str,
    *,
    now: float | None = None,
) -> RelationshipState:
    """Apply one user turn plus offline recovery to the relationship state."""

    moment = time.time() if now is None else now
    idle_hours = max(0.0, (moment - state.last_active) / 3600) if state.last_active else 0.0

    energy = min(1.0, state.energy + min(0.4, idle_hours * 0.12))
    mood = state.mood * max(0.0, 1.0 - min(0.6, idle_hours * 0.03))

    lowered = (text or "").lower()
    positive = sum(lowered.count(word) for word in _POSITIVE)
    negative = sum(lowered.count(word) for word in _NEGATIVE)
    mood = _clamp(mood + 0.08 * min(positive, 3) - 0.10 * min(negative, 3), -1.0, 1.0)
    energy = _clamp(energy - 0.02, 0.0, 1.0)

    affinity = state.affinity
    if idle_hours > 72:
        # Relationships cool a little after a long absence, but never reset.
        affinity -= min(0.3, (idle_hours - 72) / 24 * 0.02)
    affinity += 0.03
    if positive and not negative:
        affinity += 0.02
    elif negative and not positive:
        affinity -= 0.02

    return RelationshipState(
        mood=mood,
        energy=energy,
        affinity=_clamp(affinity, 0.0, 10.0),
        last_active=moment,
        turns=state.turns + 1,
    )


def _mood_label(mood: float) -> str:
    if mood >= 0.35:
        return "心情不错"
    if mood <= -0.25:
        return "有点低落"
    return "心情平稳"


def _energy_label(energy: float) -> str:
    if energy <= 0.3:
        return "有点累"
    if energy >= 0.75:
        return "精神不错"
    return "精力一般"


def _affinity_label(affinity: float) -> str:
    if affinity < 1.5:
        return "刚认识"
    if affinity < 3.5:
        return "渐渐熟悉"
    if affinity < 6:
        return "比较熟"
    if affinity < 8:
        return "亲近"
    return "很有默契"


def describe_relationship(state: RelationshipState) -> str:
    """A short state hint plus only the guidance that actually applies."""

    hints = [f"你们目前：{_mood_label(state.mood)}，{_energy_label(state.energy)}，关系{_affinity_label(state.affinity)}"]
    if state.energy <= 0.3:
        hints.append("你精力不高，回复可以更短、更慢热，不要硬撑热情")
    if state.mood <= -0.2:
        hints.append("先接住对方的情绪，不要急着逗乐或讲道理")
    elif state.mood >= 0.4:
        hints.append("可以放松一点，允许自然的玩笑")
    if state.affinity >= 6:
        hints.append("你们已经比较亲近，可以更主动地接话")
    return "；".join(hints) + "。"


class RelationshipStore:
    """Persist one state row per user; failures never block a reply."""

    def __init__(self, entity_memory: EntityMemory | None):
        self.entity_memory = entity_memory

    async def load(self, user_id: str) -> RelationshipState:
        if self.entity_memory is None:
            return RelationshipState()
        try:
            rows = await self.entity_memory.query(str(user_id), _ENTITY_TYPE)
            if not isinstance(rows, list):
                return RelationshipState()
            for row in rows:
                if getattr(row, "key", "") != _KEY:
                    continue
                data = json.loads(row.value)
                return RelationshipState(
                    mood=_clamp(float(data.get("mood", 0.0)), -1.0, 1.0),
                    energy=_clamp(float(data.get("energy", 0.7)), 0.0, 1.0),
                    affinity=_clamp(float(data.get("affinity", 1.0)), 0.0, 10.0),
                    last_active=float(data.get("last_active", 0.0)),
                    turns=int(data.get("turns", 0)),
                )
        except Exception as exc:
            logger.debug("关系状态读取失败 (%s)", type(exc).__name__)
        return RelationshipState()

    async def save(self, user_id: str, state: RelationshipState) -> None:
        if self.entity_memory is None:
            return
        try:
            await self.entity_memory.store(
                str(user_id),
                _ENTITY_TYPE,
                _KEY,
                json.dumps(asdict(state), ensure_ascii=False),
                source="auto",
            )
        except Exception as exc:
            logger.debug("关系状态保存失败 (%s)", type(exc).__name__)
