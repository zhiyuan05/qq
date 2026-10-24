"""Message splitting middleware — marks oversized responses for platform-level splitting."""
from __future__ import annotations

from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("mw_split")


class SplitMiddleware(Middleware):
    """Logs oversized responses; actual splitting is handled by the platform's send_long_message().

    This middleware is informational — it warns when a response exceeds the configured
    length so operators can tune prompt settings. The platform layer (BasePlatform.send_long_message)
    already splits long messages before sending.
    """

    def __init__(self, max_length: int = 2000):
        self.max_length = max_length

    async def on_response(self, ctx: PipelineContext) -> bool:
        if ctx.response and len(ctx.response) > self.max_length:
            logger.warning(
                "回复超长: %d 字符 (上限 %d, 将由平台层自动拆分)",
                len(ctx.response),
                self.max_length,
            )
        return True
