from __future__ import annotations

"""角色卡管理器：Markdown 人格卡为主，兼容旧 YAML 卡。"""
import os
import random
import re
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("characters")

_SAFE_CARD_NAME = re.compile(r"^[\w\-一-鿿 ]{1,64}$", re.UNICODE)
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def validate_card_name(name: str) -> str:
    """验证用于磁盘文件名的角色卡标识，阻止路径穿越和控制字符。"""

    normalized = name.strip()
    if not _SAFE_CARD_NAME.fullmatch(normalized) or normalized in {".", ".."}:
        raise ValueError("角色卡名称仅可包含中文、字母、数字、空格、下划线和连字符，且不超过 64 字")
    return normalized


class CharacterCard:
    def __init__(self, data: dict):
        self.name: str = data.get("name", "未知")
        self.description: str = data.get("description", "")
        self.skills: str = data.get("skills", "")
        self.system_prompt: str = data.get("system_prompt", "")
        self.rules: List[str] = data.get("rules", [])
        self.personality: dict = data.get("personality", {})
        self.greeting: str = data.get("greeting", "")
        self.examples: List[dict] = data.get("examples", [])
        self.learned_traits: dict = data.get("learned_traits", {})
        self.scenario: str = str(data.get("scenario") or "")
        self.alternate_greetings: List[str] = [
            str(item) for item in (data.get("alternate_greetings") or []) if str(item).strip()
        ]
        self.lorebook: List[dict] = [
            entry for entry in (data.get("lorebook") or []) if isinstance(entry, dict)
        ]
        self.post_history_instructions: str = str(data.get("post_history_instructions") or "")
        self.tags: List[str] = [str(item) for item in (data.get("tags") or []) if str(item).strip()]
        self.creator: str = str(data.get("creator") or "")
        self.character_version: str = str(data.get("character_version") or "")
        self.creator_notes: str = str(data.get("creator_notes") or "")
        self.extensions: dict = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}

    def pick_greeting(self) -> str:
        """新会话开场白；有备用问候时随机选一条。"""

        choices = [str(item) for item in [self.greeting, *self.alternate_greetings] if str(item).strip()]
        return random.choice(choices) if choices else ""

    def get_system_prompt(self) -> str:
        rules_text = "\n".join(f"- {rule}" for rule in self.rules)
        prompt = self.system_prompt.replace("{{name}}", self.name)
        prompt = prompt.replace("{{description}}", self.description)
        prompt = prompt.replace("{{skills}}", self.skills)
        # SillyTavern 卡常用 {{char}} / {{user}}，导入后继续可用。
        prompt = prompt.replace("{{char}}", self.name)
        prompt = prompt.replace("{{user}}", "用户")
        has_rules_slot = "{{rules}}" in self.system_prompt
        prompt = prompt.replace("{{rules}}", rules_text)
        if self.rules and not has_rules_slot:
            prompt += f"\n\n规则:\n{rules_text}"

        if self.scenario and self.scenario not in prompt:
            prompt += f"\n\n当前场景：{self.scenario}"

        if self.post_history_instructions.strip():
            extra = self.post_history_instructions.replace("{{char}}", self.name).replace("{{user}}", "用户")
            prompt += f"\n\n额外要求:\n{extra.strip()}"

        if self.learned_traits:
            prompt += "\n\n根据以往交流经验:\n"
            for category, traits in self.learned_traits.items():
                if isinstance(traits, list):
                    for trait in traits:
                        prompt += f"- {trait}\n"

        return prompt.strip()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "skills": self.skills,
            "system_prompt": self.system_prompt,
            "rules": self.rules,
            "personality": self.personality,
            "greeting": self.greeting,
            "examples": self.examples,
            "learned_traits": self.learned_traits,
            "scenario": self.scenario,
            "alternate_greetings": self.alternate_greetings,
            "lorebook": self.lorebook,
            "post_history_instructions": self.post_history_instructions,
            "tags": self.tags,
            "creator": self.creator,
            "character_version": self.character_version,
            "creator_notes": self.creator_notes,
            "extensions": self.extensions,
        }

    def to_front_matter(self) -> dict:
        meta = {
            "name": self.name,
            "description": self.description,
            "greeting": self.greeting,
            "rules": self.rules,
            "personality": self.personality,
            "examples": self.examples,
        }
        if self.skills:
            meta["skills"] = self.skills
        if self.learned_traits:
            meta["learned_traits"] = self.learned_traits
        if self.scenario:
            meta["scenario"] = self.scenario
        if self.alternate_greetings:
            meta["alternate_greetings"] = self.alternate_greetings
        if self.lorebook:
            meta["lorebook"] = self.lorebook
        if self.post_history_instructions:
            meta["post_history_instructions"] = self.post_history_instructions
        if self.tags:
            meta["tags"] = self.tags
        if self.creator:
            meta["creator"] = self.creator
        if self.character_version:
            meta["character_version"] = self.character_version
        if self.creator_notes:
            meta["creator_notes"] = self.creator_notes
        if self.extensions:
            meta["extensions"] = self.extensions
        return meta

    def to_markdown(self) -> str:
        meta = yaml.safe_dump(
            self.to_front_matter(),
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
        body = self.system_prompt.strip()
        if not body:
            body = f"你是{self.name}。{self.description}".strip()
        return f"---\n{meta}---\n\n{body}\n"


class CharacterManager:
    def __init__(self, cards_dir: str = "characters/cards", roles_dir: str = "characters/roles"):
        self.cards_dir = Path(cards_dir)
        self.roles_dir = Path(roles_dir)
        self._cards: Dict[str, CharacterCard] = {}
        self._load_all()

    def _load_markdown(self, path: Path) -> CharacterCard:
        text = path.read_text(encoding="utf-8")
        match = _FRONT_MATTER.match(text)
        if match:
            meta = yaml.safe_load(match.group(1)) or {}
            body = match.group(2).strip()
        else:
            meta = {}
            body = text.strip()
        if not isinstance(meta, dict):
            raise ValueError("front matter 必须是 YAML 对象")
        meta.setdefault("name", path.stem)
        meta["system_prompt"] = body
        return CharacterCard(meta)

    def _load_all(self):
        if self.roles_dir.exists():
            for path in sorted(self.roles_dir.glob("*.md")):
                try:
                    card = self._load_markdown(path)
                    self._cards[path.stem] = card
                    logger.info(f"加载 Markdown 人格卡: {card.name}")
                except Exception as exc:
                    logger.error(f"加载 Markdown 人格卡失败 {path}: {exc}")

        # Legacy YAML cards remain readable; a same-named .md card wins.
        if self.cards_dir.exists():
            for path in sorted(self.cards_dir.glob("*.yaml")):
                if path.stem in self._cards:
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        data = yaml.safe_load(handle)
                    if data:
                        card = CharacterCard(data)
                        self._cards[path.stem] = card
                        logger.info(f"加载旧版角色卡: {card.name}")
                except Exception as exc:
                    logger.error(f"加载角色卡失败 {path}: {exc}")

    def get(self, name: str) -> Optional[CharacterCard]:
        return self._cards.get(name)

    def get_default(self) -> CharacterCard:
        card = self._cards.get("default")
        if not card:
            card = CharacterCard({"name": "默认助手", "description": "AI助手"})
            self._cards["default"] = card
        return card

    def list_cards(self) -> List[str]:
        return list(self._cards.keys())

    def save(self, name: str, card: CharacterCard):
        name = validate_card_name(name)
        card.name = card.name or name
        self._cards[name] = card
        self.roles_dir.mkdir(parents=True, exist_ok=True)
        path = self.roles_dir / f"{name}.md"
        temporary = self.roles_dir / f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(card.to_markdown(), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def import_card(
        self,
        raw: bytes | str,
        *,
        name: str | None = None,
        overwrite: bool = False,
    ) -> str:
        """导入 JSON/PNG 角色卡，返回落盘使用的卡名。"""

        from characters.codec import decode_card

        data = decode_card(raw)
        card = CharacterCard(data)
        target_name = validate_card_name(name or card.name)
        existing = self.get(target_name)
        if existing and not overwrite:
            raise FileExistsError(target_name)
        if existing:
            self.backup_card(target_name)
        self.save(target_name, card)
        return target_name

    def export_card(self, name: str, *, fmt: str = "v2", image_bytes: bytes | None = None) -> dict | bytes:
        """导出为 Character Card V2（dict）或 PNG（bytes）。"""

        card = self.get(name)
        if not card:
            raise KeyError(name)
        from characters.codec import encode_png, encode_v2

        if fmt in {"v2", "json"}:
            return encode_v2(card)
        if fmt == "png":
            return encode_png(card, image_bytes)
        raise ValueError("导出格式仅支持 v2 或 png")

    def backup_card(self, name: str) -> str:
        """Copy the current Markdown/YAML card into roles/.backups before mutation."""

        candidates = [self.roles_dir / f"{name}.md", self.cards_dir / f"{name}.yaml"]
        existing = next((path for path in candidates if path.is_file()), None)
        if existing is None:
            return ""
        backup_dir = self.roles_dir / ".backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = __import__("datetime").datetime.now().strftime("%Y%m%d%H%M%S")
        target = backup_dir / f"{name}.{stamp}{existing.suffix}"
        target.write_bytes(existing.read_bytes())
        return str(target)

    def delete(self, name: str) -> bool:
        name = validate_card_name(name)
        self.backup_card(name)
        removed = False
        for path in (self.roles_dir / f"{name}.md", self.cards_dir / f"{name}.yaml"):
            if path.is_file():
                path.unlink()
                removed = True
        self._cards.pop(name, None)
        return removed

    def migrate_legacy_cards(self) -> int:
        """Convert YAML cards without a Markdown counterpart; never overwrites .md."""

        migrated = 0
        if not self.cards_dir.exists():
            return migrated
        for path in sorted(self.cards_dir.glob("*.yaml")):
            target = self.roles_dir / f"{path.stem}.md"
            if target.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = yaml.safe_load(handle)
                if not data:
                    continue
                self.save(path.stem, CharacterCard(data))
                migrated += 1
            except Exception as exc:
                logger.error(f"迁移角色卡失败 {path}: {exc}")
        if migrated:
            logger.info("已迁移 %d 张旧版角色卡到 Markdown", migrated)
        return migrated

    def update_learned_traits(self, name: str, traits: dict):
        card = self._cards.get(name)
        if card:
            card.learned_traits = traits
            self.save(name, card)
