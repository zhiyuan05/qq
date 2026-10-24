"""Prompt 构建器"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from characters.manager import CharacterCard
from core.context_assembler import ContextAssembler
from core.session import ChatMessage
from memory.entity_memory import EntityMemory
from styles.engine import Style

_WEEKDAYS = "一二三四五六日"
_OUTPUT_RULES = (
    "输出要求：像真人聊天一样自然，不要使用 Markdown 标题、表格、代码块或列表符号"
    "（用户明确要求，或当前回复风格要求结构化输出时除外）。消息中可能附带自动读取的"
    "网页、搜索结果、天气或视频关键帧，请优先结合这些信息回答，不要编造未提供的事实。"
)


def _character_cues(character: CharacterCard) -> str:
    """Small persona cues that role cards already define but the live prompt ignored."""
    lines: list[str] = []
    personality = getattr(character, "personality", None) or {}
    if isinstance(personality, dict):
        tone = str(personality.get("tone") or "").strip()
        if tone:
            lines.append(f"语气：{tone[:60]}")
    examples = getattr(character, "examples", None) or []
    pairs: list[str] = []
    for example in examples[:2]:
        if not isinstance(example, dict):
            continue
        user = str(example.get("user") or "").strip()[:100]
        assistant = str(example.get("assistant") or "").strip()[:100]
        if user and assistant:
            pairs.append(f"用户：{user}\n你：{assistant}")
    if pairs:
        lines.append("语气示例（只模仿语气和节奏，不要照抄内容）：\n" + "\n".join(pairs))
    return "\n".join(lines)


def time_context(now: datetime | None = None) -> str:
    """Current Beijing time so the model can resolve 今天/明天/现在 correctly."""

    moment = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return (
        f"当前时间：{moment:%Y年%m月%d日} 星期{_WEEKDAYS[moment.weekday()]} "
        f"{moment:%H:%M}（北京时间）。涉及今天、明天、现在或时效性的问题时以此为准。"
    )


class PromptBuilder:
    def __init__(self, entity_memory: Optional[EntityMemory] = None):
        self.entity_memory = entity_memory

    async def build(
        self,
        character: CharacterCard,
        style: Style,
        user_message: str,
        history: list[dict[str, str]],
        memory_results: Optional[list[dict]] = None,
        user_id: str = "",
        sticker_tags: Optional[list[str]] = None,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []

        system_content = character.get_system_prompt()
        system_content += f"\n\n{time_context()}\n{_OUTPUT_RULES}"
        cues = _character_cues(character)
        if cues:
            system_content += f"\n\n{cues}"
        if sticker_tags:
            system_content += (
                "\n\n表情包：情绪明显时可以自然配一张，在回复末尾写 [sticker:标签]，"
                "每条最多一次，不要解释这个标记。可用标签："
                + "、".join(sticker_tags[:12])
            )
        if style and style.prompt_modifier:
            system_content += f"\n\n回复风格要求：{style.prompt_modifier}"

        if self.entity_memory and user_id:
            try:
                profile = await self.entity_memory.get_relevant_profile_text(user_id, user_message)
                if profile:
                    system_content += f"\n\n{profile}"
            except Exception:
                pass

        if memory_results:
            memory_text = "\n".join(f"- {m['text']}" for m in memory_results[:5])
            system_content += f"\n\n相关记忆:\n{memory_text}"

        messages.append({"role": "system", "content": system_content})
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        return messages

    async def build_context(
        self,
        character: CharacterCard,
        style: Style,
        user_message: str | list[dict],
        history: list[ChatMessage],
        *,
        assembler: ContextAssembler,
        summary: str = "",
        memory_results: Optional[list[dict]] = None,
        user_traits: list[str] | None = None,
        global_traits: list[str] | None = None,
        user_id: str = "",
        sticker_tags: Optional[list[str]] = None,
    ) -> list[dict]:
        """Assemble a token-bounded prompt from structured history."""

        system_content = character.get_system_prompt()
        system_content += f"\n\n{time_context()}\n{_OUTPUT_RULES}"
        cues = _character_cues(character)
        if cues:
            system_content += f"\n\n{cues}"
        if sticker_tags:
            system_content += (
                "\n\n表情包：情绪明显时可以自然配一张，在回复末尾写 [sticker:标签]，"
                "每条最多一次，不要解释这个标记。可用标签："
                + "、".join(sticker_tags[:12])
            )
        if style and style.prompt_modifier:
            system_content += f"\n\n回复风格要求：{style.prompt_modifier}"

        profile_text = ""
        if self.entity_memory and user_id:
            try:
                query = user_message if isinstance(user_message, str) else " ".join(
                    str(block.get("text", ""))
                    for block in user_message
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                profile_text = await self.entity_memory.get_relevant_profile_text(user_id, query)
                if profile_text.startswith("用户画像:"):
                    profile_text = profile_text[len("用户画像:") :].strip()
            except Exception:
                pass

        return assembler.assemble(
            system_content=system_content,
            user_message=user_message,
            history=history,
            summary=summary,
            memory_results=memory_results or (),
            user_traits=user_traits or (),
            global_traits=global_traits or (),
            profile_text=profile_text,
        )

    async def build_with_examples(
        self,
        character: CharacterCard,
        style: Style,
        user_message: str,
        history: list[dict[str, str]],
        memory_results: Optional[list[dict]] = None,
        user_id: str = "",
        sticker_tags: Optional[list[str]] = None,
    ) -> list[dict[str, str]]:
        messages = await self.build(
            character,
            style,
            user_message,
            history,
            memory_results,
            user_id,
            sticker_tags,
        )

        if character.examples:
            examples: list[dict[str, str]] = []
            for ex in character.examples:
                examples.append({"role": "user", "content": ex.get("user", "")})
                examples.append({"role": "assistant", "content": ex.get("assistant", "")})
            messages = [messages[0]] + examples + messages[1:]

        return messages
