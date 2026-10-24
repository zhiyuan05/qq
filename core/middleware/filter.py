from __future__ import annotations

"""Message filter middleware."""
from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("mw_filter")


class FilterMiddleware(Middleware):
    def __init__(self, max_length: int = 2000):
        self.max_length = max_length

    async def on_receive(self, ctx: PipelineContext) -> bool:
        if (not ctx.content or not ctx.content.strip()) and not ctx.has_attachments:
            logger.debug("空消息被过滤")
            return False
        if len(ctx.content) > self.max_length:
            ctx.blocked = True
            ctx.block_reason = f"消息过长（最多 {self.max_length} 个字符）"
            logger.warning("消息过长被拒绝: user=%s length=%d", ctx.user_id, len(ctx.content))
            return False
        return True
