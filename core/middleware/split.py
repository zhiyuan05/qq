"""Message splitting middleware."""
from core.pipeline import Middleware, PipelineContext
from utils.message_splitter import split_message
from utils.logger import setup_logger

logger = setup_logger("mw_split")


class SplitMiddleware(Middleware):
    def __init__(self, max_length: int = 2000):
        self.max_length = max_length

    async def on_response(self, ctx: PipelineContext) -> bool:
        if ctx.response and len(ctx.response) > self.max_length:
            chunks = split_message(ctx.response, max_length=self.max_length)
            ctx.metadata["chunks"] = chunks
            ctx.response = chunks[0]
            logger.debug(f"消息已拆分为 {len(chunks)} 段")
        return True
