"""Active time window middleware: only respond during configured hours."""
from core.pipeline import Middleware, PipelineContext
from utils.logger import setup_logger

logger = setup_logger("active_window")


class ActiveWindowMiddleware(Middleware):
    def __init__(self, start_hour: int = 8, end_hour: int = 23):
        self.start_hour = start_hour
        self.end_hour = end_hour

    async def on_receive(self, ctx: PipelineContext) -> bool:
        if ctx.is_owner:
            return True

        hour = ctx.timestamp.hour
        if self.start_hour <= hour < self.end_hour:
            return True

        ctx.blocked = True
        ctx.block_reason = f"不在活跃时段({self.start_hour}:00-{self.end_hour}:00)"
        logger.info(f"活跃时间窗口外的消息被忽略: {ctx.user_id} at {ctx.timestamp}")
        return False
