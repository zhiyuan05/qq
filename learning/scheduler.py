"""学习调度器"""
import asyncio
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from utils.logger import setup_logger

logger = setup_logger("learning.scheduler")


class LearningScheduler:
    def __init__(self, trigger_interval: int = 50, trigger_schedule: str = "03:00"):
        self.trigger_interval = trigger_interval
        self.trigger_schedule = trigger_schedule
        self._scheduler = AsyncIOScheduler()
        self._learn_callback: Optional[Callable] = None
        self._conversation_count = 0

    def set_learn_callback(self, callback: Callable):
        self._learn_callback = callback

    def start(self):
        hour, minute = self.trigger_schedule.split(":")
        self._scheduler.add_job(
            self._trigger_learn,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id="scheduled_learn",
            name="定时学习",
        )
        self._scheduler.start()
        logger.info(f"学习调度器启动，定时: {self.trigger_schedule}")

    def stop(self):
        self._scheduler.shutdown()
        logger.info("学习调度器已停止")

    def on_conversation_end(self):
        self._conversation_count += 1
        if self._conversation_count >= self.trigger_interval:
            self._conversation_count = 0
            asyncio.create_task(self._trigger_learn())

    async def _trigger_learn(self):
        if self._learn_callback:
            logger.info("触发学习任务...")
            try:
                await self._learn_callback()
            except Exception as e:
                logger.error(f"学习任务执行失败: {e}")
