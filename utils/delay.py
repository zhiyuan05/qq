"""随机延迟 - 模拟人类回复节奏"""
from __future__ import annotations

import asyncio
import random
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("delay")


class HumanDelay:
    """模拟人类打字和回复延迟"""


    def __init__(self, min_seconds: float = 0.5, max_seconds: float = 3.0,
                 chars_per_second: float = 15.0, enabled: bool = True):
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.chars_per_second = chars_per_second
        self.enabled = enabled

    async def wait(self, response_length: int = 0):
        """根据回复长度计算延迟"""
        if not self.enabled:
            return

        # 基础延迟（随机）
        base = random.uniform(self.min_seconds, self.max_seconds)

        # 根据回复长度增加延迟（模拟打字时间）
        typing_time = response_length / self.chars_per_second if response_length > 0 else 0

        # 随机波动 ±20%
        total = (base + typing_time) * random.uniform(0.8, 1.2)

        # 限制在合理范围
        total = max(0.3, min(total, 10.0))

        logger.debug(f"模拟延迟: {total:.1f}秒")
        await asyncio.sleep(total)


# 全局实例
_human_delay: Optional[HumanDelay] = None


def get_delay() -> HumanDelay:
    global _human_delay
    if _human_delay is None:
        _human_delay = HumanDelay()
    return _human_delay


def init_delay(min_seconds: float = 0.5, max_seconds: float = 3.0,
               chars_per_second: float = 15.0, enabled: bool = True):
    global _human_delay
    _human_delay = HumanDelay(min_seconds, max_seconds, chars_per_second, enabled)
    return _human_delay
