from __future__ import annotations

"""Safety middleware with circuit breaker check."""
from core.pipeline import Middleware, PipelineContext
from security.circuit_breaker import CircuitBreaker
from utils.logger import setup_logger

logger = setup_logger("mw_safety")


class SafetyMiddleware(Middleware):
    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker

    async def _check(self, ctx: PipelineContext) -> bool:
        if not self.breaker.allow_request():
            ctx.blocked = True
            ctx.block_reason = "服务暂时不可用，请稍后重试"
            logger.warning("熔断器开启，LLM 调用被阻断")
            return False
        return True

    async def on_llm(self, ctx: PipelineContext) -> bool:
        return await self._check(ctx)

    async def on_send(self, ctx: PipelineContext) -> bool:
        # 保留旧钩子的兼容行为，并防止生成期间刚开启的熔断器继续发送。
        return await self._check(ctx)
