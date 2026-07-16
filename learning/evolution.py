"""角色卡演化管理"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import yaml

from utils.logger import setup_logger

logger = setup_logger("learning.evolution")


class EvolutionManager:
    def __init__(self, cards_dir: str = "characters/cards", learning_dir: str = "data/learning"):
        self.cards_dir = Path(cards_dir)
        self.learning_dir = Path(learning_dir)
        self.learning_dir.mkdir(parents=True, exist_ok=True)

    def backup_card(self, card_name: str) -> str:
        src = self.cards_dir / f"{card_name}.yaml"
        if not src.exists():
            return ""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = self.learning_dir / f"{card_name}_backup_{timestamp}.yaml"
        shutil.copy2(src, dst)
        logger.info(f"角色卡已备份: {dst}")
        return str(dst)

    def apply_learned_traits(self, card_name: str, traits: Dict) -> bool:
        card_path = self.cards_dir / f"{card_name}.yaml"
        if not card_path.exists():
            logger.error(f"角色卡不存在: {card_name}")
            return False

        try:
            with open(card_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            existing_traits = data.get("learned_traits", {})
            for key, values in traits.items():
                if key == "summary":
                    continue
                if isinstance(values, list):
                    if key in existing_traits and isinstance(existing_traits[key], list):
                        combined = list(set(existing_traits[key] + values))
                        existing_traits[key] = combined[:20]
                    else:
                        existing_traits[key] = values

            existing_traits["last_updated"] = datetime.now().isoformat()
            existing_traits["learning_version"] = existing_traits.get("learning_version", 0) + 1
            data["learned_traits"] = existing_traits

            with open(card_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

            logger.info(f"角色卡 {card_name} 已更新学习特质")
            return True
        except Exception as e:
            logger.error(f"应用学习结果失败: {e}")
            return False

    def save_report(self, card_name: str, report: Dict):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.learning_dir / f"report_{card_name}_{timestamp}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(report, f, allow_unicode=True, default_flow_style=False)
        logger.info(f"学习报告已保存: {path}")

    def get_evolution_history(self, card_name: str) -> list:
        reports = []
        for f in sorted(self.learning_dir.glob(f"report_{card_name}_*.yaml")):
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    reports.append(yaml.safe_load(fp))
            except Exception:
                continue
        return reports

    def reset_card(self, card_name: str) -> bool:
        card_path = self.cards_dir / f"{card_name}.yaml"
        if not card_path.exists():
            return False
        try:
            with open(card_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data.pop("learned_traits", None)
            with open(card_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            return True
        except Exception as e:
            logger.error(f"重置失败: {e}")
            return False
