from __future__ import annotations

"""Content filter middleware for responses."""
from core.pipeline import Middleware, PipelineContext
from security.content_filter import ContentFilter
from utils.logger import setup_logger

logger = setup_logger("mw_content_filter")


class ContentFilterMiddleware(Middleware):
    def __init__(self, content_filter: ContentFilter):
        self.filter = content_filter

    async def on_response(self, ctx: PipelineContext) -> bool:
        if ctx.response:
            original = ctx.response
            ctx.response = self.filter.filter(ctx.response)
            if ctx.response != original:
                logger.info(f"回复内容已过滤: {ctx.user_id}")
        return True
