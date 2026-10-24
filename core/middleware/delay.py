"""Human-like delay middleware — delegates to shared HumanDelay."""
from __future__ import annotations

from core.pipeline import Middleware, PipelineContext
from utils.delay import HumanDelay
from utils.logger import setup_logger

logger = setup_logger("mw_delay")


class DelayMiddleware(Middleware):
    """在发送响应前添加人类化的延迟（模拟打字节奏）。"""

    def __init__(
        self,
        min_seconds: float = 0.5,
        max_seconds: float = 3.0,
        chars_per_second: float = 15.0,
        enabled: bool = True,
    ):
        self._delay = HumanDelay(
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            chars_per_second=chars_per_second,
            enabled=enabled,
        )

    async def on_send(self, ctx: PipelineContext) -> bool:
        if ctx.response:
            await self._delay.wait(len(ctx.response))
        return True
