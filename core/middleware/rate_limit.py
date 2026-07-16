"""Rate limit middleware."""
from core.pipeline import Middleware, PipelineContext
from security.rate_limiter import RateLimiter
from utils.logger import setup_logger

logger = setup_logger("mw_rate_limit")


class RateLimitMiddleware(Middleware):
    def __init__(self, limiter: RateLimiter):
        self.limiter = limiter

    async def on_receive(self, ctx: PipelineContext) -> bool:
        allowed, reason = self.limiter.check(ctx.user_id, ctx.group_id)
        if not allowed:
            ctx.blocked = True
            ctx.block_reason = reason
            logger.warning(f"限流: {ctx.user_id} - {reason}")
            return False

        depth_ok, depth_reason = self.limiter.check_depth(ctx.user_id)
        if not depth_ok:
            ctx.blocked = True
            ctx.block_reason = depth_reason
            logger.warning(f"深度超限: {ctx.user_id} - {depth_reason}")
            return False

        return True
