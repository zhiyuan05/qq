"""Message filter middleware."""
from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("mw_filter")


class FilterMiddleware(Middleware):
    async def on_receive(self, ctx: PipelineContext) -> bool:
        if not ctx.content or not ctx.content.strip():
            logger.debug("空消息被过滤")
            return False
        return True
