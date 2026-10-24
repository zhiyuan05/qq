from __future__ import annotations

"""学习调度器"""
import asyncio
from collections.abc import Awaitable, Callable
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from utils.logger import setup_logger

logger = setup_logger("learning.scheduler")


class LearningScheduler:
    def __init__(
        self,
        trigger_interval: int = 50,
        trigger_schedule: str = "03:00",
        *,
        shutdown_timeout: float = 15.0,
        cancel_timeout: float = 2.0,
    ):
        self.trigger_interval = trigger_interval
        self.trigger_schedule = trigger_schedule
        self._scheduler = AsyncIOScheduler()
        self._learn_callback: Optional[Callable[[], Awaitable[None]]] = None
        self._conversation_count = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._learn_lock = asyncio.Lock()
        self._accepting = True
        self._shutdown_requested = False
        self._shutdown_timeout = max(0.01, shutdown_timeout)
        self._cancel_timeout = max(0.01, cancel_timeout)
        self._drain_timed_out = False

    def set_learn_callback(self, callback: Callable[[], Awaitable[None]]):
        self._learn_callback = callback

    def start(self):
        if self._scheduler.running:
            return
        self._accepting = True
        self._shutdown_requested = False
        self._drain_timed_out = False
        hour, minute = self.trigger_schedule.split(":")
        self._scheduler.add_job(
            self._scheduled_trigger,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id="scheduled_learn",
            name="定时学习",
        )
        self._scheduler.start()
        logger.info(f"学习调度器启动，定时: {self.trigger_schedule}")

    def stop(self):
        """Synchronously quiesce triggers; use ``drain`` to await active learning."""

        self._accepting = False
        if self._scheduler.running and not self._shutdown_requested:
            self._shutdown_requested = True
            self._scheduler.shutdown(wait=False)
        logger.info("学习调度器已停止")

    async def drain(self, timeout: float | None = None) -> bool:
        """Wait for owned jobs, then cancel them after a bounded timeout."""

        current = asyncio.current_task()
        tasks = {task for task in self._tasks if task is not current and not task.done()}
        if not tasks:
            return True

        _, pending = await asyncio.wait(tasks, timeout=self._shutdown_timeout if timeout is None else timeout)
        if not pending:
            return True

        self._drain_timed_out = True
        logger.warning("Learning shutdown timed out with %d active job(s); cancelling", len(pending))
        for task in pending:
            task.cancel()
        _, still_pending = await asyncio.wait(pending, timeout=self._cancel_timeout)
        if still_pending:
            logger.error("Learning scheduler has %d cancellation-resistant job(s)", len(still_pending))
        return False

    def on_conversation_end(self):
        if not self._accepting:
            return
        self._conversation_count += 1
        if self._conversation_count >= self.trigger_interval:
            self._conversation_count = 0
            self._create_task(self._trigger_learn())

    async def _scheduled_trigger(self) -> None:
        """Let APScheduler finish quickly while this class owns the real job."""

        if self._accepting:
            self._create_task(self._trigger_learn())

    def _create_task(self, coroutine: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine, name="learning:conversation-trigger")
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error("Learning background task failed (%s)", type(error).__name__)

    async def _trigger_learn(self):
        if not self._accepting:
            return

        current = asyncio.current_task()
        scheduler_owned = current is not None and current not in self._tasks
        if scheduler_owned:
            self._tasks.add(current)
        try:
            if not self._learn_callback:
                return
            if self._learn_lock.locked():
                logger.info("上一轮学习仍在进行，跳过本次触发")
                return
            logger.info("触发学习任务...")
            try:
                async with self._learn_lock:
                    await self._learn_callback()
            except Exception as exc:
                logger.error("学习任务执行失败 (%s)", type(exc).__name__)
        finally:
            if scheduler_owned and current is not None:
                self._tasks.discard(current)
