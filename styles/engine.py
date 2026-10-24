from __future__ import annotations

"""风格引擎"""
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("styles")


class Style:
    def __init__(self, data: dict):
        self.name: str = data.get("name", "")
        self.description: str = data.get("description", "")
        self.prompt_modifier: str = data.get("prompt_modifier", "")
        self.emoji_density: float = data.get("emoji_density", 0.0)
        self.max_length: int = data.get("max_length", 500)


class StyleEngine:
    def __init__(self, presets_path: str = "styles/presets.yaml"):
        self._styles: Dict[str, Style] = {}
        self._load_presets(presets_path)

    def _load_presets(self, path: str):
        p = Path(path)
        if not p.exists():
            logger.warning(f"风格预设文件不存在: {path}")
            return
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for key, val in data.get("styles", {}).items():
            self._styles[key] = Style(val)
            logger.info(f"加载风格: {key}")

    def get(self, name: str) -> Optional[Style]:
        return self._styles.get(name)

    def get_default(self) -> Style:
        return self._styles.get("casual", Style({"name": "默认", "prompt_modifier": ""}))

    def list_styles(self) -> List[str]:
        return list(self._styles.keys())

    def create(self, name: str, description: str, prompt_modifier: str) -> Style:
        style = Style({
            "name": name,
            "description": description,
            "prompt_modifier": prompt_modifier,
        })
        self._styles[name] = style
        return style

    def mix(self, name1: str, name2: str, ratio: float = 0.5) -> Optional[Style]:
        s1 = self._styles.get(name1)
        s2 = self._styles.get(name2)
        if not s1 or not s2:
            return None
        return Style({
            "name": f"{name1}+{name2}",
            "description": f"{s1.description} 与 {s2.description} 的混合",
            "prompt_modifier": f"{s1.prompt_modifier}\n同时也要：{s2.prompt_modifier}",
            "emoji_density": s1.emoji_density * ratio + s2.emoji_density * (1 - ratio),
            "max_length": int(s1.max_length * ratio + s2.max_length * (1 - ratio)),
        })
