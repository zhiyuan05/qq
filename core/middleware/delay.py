"""Human-like delay middleware."""
import asyncio
import random
from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("mw_delay")


class DelayMiddleware(Middleware):
    def __init__(self, min_seconds: float = 0.5, max_seconds: float = 3.0, chars_per_second: float = 15.0):
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self.chars_per_second = chars_per_second

    async def on_send(self, ctx: PipelineContext) -> bool:
        if ctx.response:
            base = random.uniform(self.min_seconds, self.max_seconds)
            typing_time = len(ctx.response) / self.chars_per_second
            total = min((base + typing_time) * random.uniform(0.8, 1.2), 10.0)
            total = max(0.3, total)
            await asyncio.sleep(total)
        return True
