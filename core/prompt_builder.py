from __future__ import annotations
"""Prompt 构建器"""
from typing import Dict, List, Optional

from characters.manager import CharacterCard
from styles.engine import Style
from memory.entity_memory import EntityMemory


class PromptBuilder:
    def __init__(self, entity_memory: Optional[EntityMemory] = None):
        self.entity_memory = entity_memory

    def build(
        self,
        character: CharacterCard,
        style: Style,
        user_message: str,
        history: List[Dict[str, str]],
        memory_results: Optional[List[Dict]] = None,
        user_id: str = "",
    ) -> List[Dict[str, str]]:
        messages = []

        system_content = character.get_system_prompt()
        if style and style.prompt_modifier:
            system_content += f"\n\n回复风格要求：{style.prompt_modifier}"

        if self.entity_memory and user_id:
            profile = self.entity_memory.get_profile_text(user_id)
            if profile:
                system_content += f"\n\n{profile}"

        if memory_results:
            memory_text = "\n".join(f"- {m['text']}" for m in memory_results[:5])
            system_content += f"\n\n相关记忆:\n{memory_text}"

        messages.append({"role": "system", "content": system_content})
        messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        return messages

    def build_with_examples(
        self,
        character: CharacterCard,
        style: Style,
        user_message: str,
        history: List[Dict[str, str]],
        memory_results: Optional[List[Dict]] = None,
        user_id: str = "",
    ) -> List[Dict[str, str]]:
        messages = self.build(character, style, user_message, history, memory_results, user_id)

        if character.examples:
            examples = []
            for ex in character.examples:
                examples.append({"role": "user", "content": ex.get("user", "")})
                examples.append({"role": "assistant", "content": ex.get("assistant", "")})
            messages = [messages[0]] + examples + messages[1:]

        return messages
