"""角色卡管理器"""
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("characters")


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

    def get_system_prompt(self) -> str:
        rules_text = "\n".join(f"- {r}" for r in self.rules)
        prompt = self.system_prompt.replace("{{name}}", self.name)
        prompt = prompt.replace("{{description}}", self.description)
        prompt = prompt.replace("{{skills}}", self.skills)
        prompt = prompt.replace("{{rules}}", rules_text)

        if self.learned_traits:
            prompt += "\n\n根据以往交流经验:\n"
            for category, traits in self.learned_traits.items():
                if isinstance(traits, list):
                    for trait in traits:
                        prompt += f"- {trait}\n"

        return prompt

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
        }


class CharacterManager:
    def __init__(self, cards_dir: str = "characters/cards"):
        self.cards_dir = Path(cards_dir)
        self._cards: Dict[str, CharacterCard] = {}
        self._load_all()

    def _load_all(self):
        if not self.cards_dir.exists():
            return
        for f in self.cards_dir.glob("*.yaml"):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = yaml.safe_load(fp)
                if data:
                    card = CharacterCard(data)
                    self._cards[f.stem] = card
                    logger.info(f"加载角色卡: {card.name}")
            except Exception as e:
                logger.error(f"加载角色卡失败 {f}: {e}")

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
        self._cards[name] = card
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        path = self.cards_dir / f"{name}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(card.to_dict(), f, allow_unicode=True, default_flow_style=False)

    def update_learned_traits(self, name: str, traits: dict):
        card = self._cards.get(name)
        if card:
            card.learned_traits = traits
            self.save(name, card)
