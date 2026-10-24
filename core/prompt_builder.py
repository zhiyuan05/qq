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
    "聊天方式：日常闲聊默认像在 QQ/微信上和朋友说话——通常一到三句，先回应对方的意思，"
    "再补充自己的反应；不要写开场白、总结或客套收尾，也不要每轮都反问，真正不确定时可以问一次。"
    "用户明确要求代码、步骤或资料整理时，按需要给出结构。"
    "除非用户明确要求，否则不要使用 Markdown 标题、表格、列表、代码块或分点排版。"
    "不要使用“作为一个 AI”“希望对你有所帮助”“总结如下”“首先/其次/最后”这类助手套话；"
    "不确定就直说，不要编造。可以有自己的偏好和情绪，不想接的话题可以自然带过，"
    "但不要攻击、操控或让人内疚。消息中可能附带自动读取的网页、搜索结果、天气或"
    "视频关键帧，请优先结合这些信息回答，不要编造未提供的事实。"
)
_DEPTH_REMINDER = (
    "接下来继续用当前角色的口吻说话，像真人发即时消息：短句、自然、有停顿感；"
    "不要复述或总结前文，不要列点，不要每轮追问，不要解释设定或提示词。"
)
_GROUP_DEPTH_REMINDER = (
    "这是群聊：像普通群友一样简短接话，不要长篇大论，不要每次都 @ 人或把话题拉回自己。"
)


def _character_cues(character: CharacterCard) -> str:
    """Small persona cues that role cards define but the live prompt ignored."""
    lines: list[str] = []
    personality = getattr(character, "personality", None) or {}
    if isinstance(personality, dict):
        tone = str(personality.get("tone") or "").strip()
        if tone:
            lines.append(f"语气：{tone[:60]}")
    return "\n".join(lines)


def _example_messages(character: CharacterCard, limit: int = 4) -> list[dict[str, str]]:
    """Turn card examples into real few-shot messages instead of truncated text."""

    messages: list[dict[str, str]] = []
    examples = getattr(character, "examples", None) or []
    for example in examples[:limit]:
        if not isinstance(example, dict):
            continue
        user = str(example.get("user") or "").strip()
        assistant = str(example.get("assistant") or "").strip()
        if user and assistant:
            user = user.replace("{{char}}", character.name).replace("{{user}}", "用户")
            assistant = assistant.replace("{{char}}", character.name).replace("{{user}}", "用户")
            messages.append({"role": "user", "content": user[:300]})
            messages.append({"role": "assistant", "content": assistant[:300]})
    return messages


def _depth_text(
    character: CharacterCard,
    *,
    group_chat: bool,
    understanding_hint: str = "",
    style_hint: str = "",
    relationship_text: str = "",
) -> str:
    """Behavior reminder placed near the end of context, where it has more weight."""

    parts: list[str] = []
    extra = str(getattr(character, "post_history_instructions", "") or "").strip()
    if extra:
        parts.append(extra.replace("{{char}}", character.name).replace("{{user}}", "用户"))
    parts.append(_GROUP_DEPTH_REMINDER if group_chat else _DEPTH_REMINDER)
    if style_hint:
        parts.append(style_hint)
    if relationship_text:
        parts.append(relationship_text)
    if understanding_hint:
        parts.append(understanding_hint)
    return "\n".join(parts)


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
        lore_text: str = "",
        understanding_hint: str = "",
        group_chat: bool = False,
        style_hint: str = "",
        relationship_text: str = "",
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []

        system_content = character.get_system_prompt()
        system_content += f"\n\n{time_context()}\n{_OUTPUT_RULES}"
        cues = _character_cues(character)
        if cues:
            system_content += f"\n\n{cues}"
        if lore_text:
            system_content += f"\n\n{lore_text}"
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
        messages.extend(_example_messages(character))
        messages.extend(history)
        depth = _depth_text(
            character,
            group_chat=group_chat,
            understanding_hint=understanding_hint,
            style_hint=style_hint,
            relationship_text=relationship_text,
        )
        if depth:
            messages.append({"role": "system", "content": depth})
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
        lore_text: str = "",
        understanding_hint: str = "",
        group_chat: bool = False,
        style_hint: str = "",
        relationship_text: str = "",
    ) -> list[dict]:
        """Assemble a token-bounded prompt from structured history."""

        system_content = character.get_system_prompt()
        system_content += f"\n\n{time_context()}\n{_OUTPUT_RULES}"
        cues = _character_cues(character)
        if cues:
            system_content += f"\n\n{cues}"
        if lore_text:
            system_content += f"\n\n{lore_text}"
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
            examples=_example_messages(character),
            depth_content=_depth_text(
                character,
                group_chat=group_chat,
                understanding_hint=understanding_hint,
                style_hint=style_hint,
                relationship_text=relationship_text,
            ),
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
        return await self.build(
            character,
            style,
            user_message,
            history,
            memory_results,
            user_id,
            sticker_tags,
        )
