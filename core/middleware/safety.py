"""Safety middleware with circuit breaker check."""
from core.pipeline import Middleware, PipelineContext
from security.circuit_breaker import CircuitBreaker
from utils.logger import setup_logger

logger = setup_logger("mw_safety")


class SafetyMiddleware(Middleware):
    def __init__(self, breaker: CircuitBreaker):
        self.breaker = breaker

    async def on_send(self, ctx: PipelineContext) -> bool:
        if not self.breaker.allow_request():
            ctx.blocked = True
            ctx.block_reason = "熔断器开启，暂停发送"
            logger.warning("熔断器开启，消息发送被阻断")
            return False
        return True
