"""Lifecycle and delivery contract shared by every chat platform adapter."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from platforms.models import DeliveryReceipt, ReplyPlan, UnifiedMessage
from utils.delay import get_delay
from utils.logger import setup_logger
from utils.message_splitter import split_message

logger = setup_logger("platform")

MessageCallback = Callable[[UnifiedMessage], Awaitable[str | ReplyPlan | None]]


class PlatformState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


class PlatformError(RuntimeError):
    """Base class for actionable platform failures."""


class PlatformConfigurationError(PlatformError):
    """The adapter cannot start because its configuration is invalid."""


class PlatformNotReadyError(PlatformError):
    """The transport isn't connected and cannot accept outbound messages."""


class PlatformDeliveryError(PlatformError):
    """The remote platform rejected or failed an outbound delivery."""

    def __init__(self, message: str, *, code: int | str | None = None):
        super().__init__(message)
        self.code = code


class PlatformRateLimitedError(PlatformDeliveryError):
    def __init__(self, message: str, *, retry_after: float, code: int | str | None = None):
        super().__init__(message, code=code)
        self.retry_after = max(0.0, retry_after)


@dataclass(slots=True)
class _DispatchRecord:
    response: str | ReplyPlan | None
    delivered: bool = False


class BasePlatform(ABC):
    """Template-method adapter with idempotent lifecycle and bounded replay state."""

    name: str = "base"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._callback: MessageCallback | None = None
        self._max_msg_length = int(self.config.get("max_msg_length", 2000))
        self._state = PlatformState.STOPPED
        self._state_lock = asyncio.Lock()
        self._failed_start_cleaned = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._accepting_messages = True
        self._inflight_dispatches: dict[asyncio.Future[None], asyncio.Task[Any] | None] = {}
        self._shutdown_drain_timeout = max(0.01, float(self.config.get("shutdown_drain_timeout", 15.0)))
        self._shutdown_cancel_timeout = max(0.01, float(self.config.get("shutdown_cancel_timeout", 2.0)))
        self._shutdown_close_timeout = max(0.01, float(self.config.get("shutdown_close_timeout", 5.0)))
        self._shutdown_drain_timed_out = False
        self._last_error: str | None = None
        self._stats: dict[str, int] = {
            "received": 0,
            "processed": 0,
            "ignored": 0,
            "duplicates": 0,
            "callback_failures": 0,
            "sent": 0,
            "send_failures": 0,
            "transport_errors": 0,
            "reconnects": 0,
            "dropped": 0,
        }
        self._dispatch_cache_size = max(1, int(self.config.get("dedup_cache_size", 2048)))
        self._dispatch_cache: OrderedDict[str, _DispatchRecord] = OrderedDict()

    @property
    def state(self) -> PlatformState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state is PlatformState.RUNNING

    @property
    def is_ready(self) -> bool:
        return self.is_running and self._transport_ready

    @property
    def _transport_ready(self) -> bool:
        return True

    def on_message(self, callback: MessageCallback) -> None:
        if not callable(callback):
            raise TypeError("message callback must be callable")
        self._callback = callback

    async def start(self) -> None:
        """Start once; retries are allowed after a failed or completed stop."""

        async with self._state_lock:
            if self._state in {PlatformState.STARTING, PlatformState.RUNNING}:
                return
            if self._state is PlatformState.STOPPING:
                raise PlatformNotReadyError(f"{self.name} is shutting down")
            self._state = PlatformState.STARTING
            self._last_error = None
            self._failed_start_cleaned = False
            self._accepting_messages = True
            self._shutdown_drain_timed_out = False
            try:
                await self._start()
            except BaseException as exc:
                self._state = PlatformState.FAILED
                self._accepting_messages = False
                self._record_error(exc)
                cleanup_succeeded = False
                try:
                    await self._close_transport()
                    cleanup_succeeded = True
                except Exception as cleanup_error:
                    logger.error(
                        "%s cleanup after failed start also failed (%s)",
                        self.name,
                        type(cleanup_error).__name__,
                    )
                finally:
                    await self._cancel_background_tasks()
                    self._failed_start_cleaned = cleanup_succeeded
                raise
            self._state = PlatformState.RUNNING

    async def quiesce(self) -> None:
        """Stop accepting inbound messages while keeping delivery available for drain."""

        async with self._state_lock:
            if self._state in {PlatformState.STOPPED, PlatformState.FAILED, PlatformState.STOPPING}:
                self._accepting_messages = False
                return
            self._state = PlatformState.STOPPING
            self._accepting_messages = False
            try:
                await self._quiesce()
            except BaseException as exc:
                self._record_error(exc)
                raise

    async def stop(self) -> None:
        """Quiesce, drain accepted messages, then close and reap owned tasks."""

        quiesce_error: BaseException | None = None
        try:
            await self.quiesce()
        except BaseException as exc:
            quiesce_error = exc

        async with self._state_lock:
            if self._state is PlatformState.STOPPED:
                if quiesce_error is not None:
                    raise quiesce_error
                return
            if self._state is PlatformState.FAILED and self._failed_start_cleaned:
                self._state = PlatformState.STOPPED
                if quiesce_error is not None:
                    raise quiesce_error
                return
            self._state = PlatformState.STOPPING
            self._accepting_messages = False
            error: BaseException | None = quiesce_error
            try:
                await self._drain_inflight_dispatches()
            except BaseException as exc:
                error = error or exc
                self._record_error(exc)
            try:
                await self._close_transport()
            except BaseException as exc:
                error = error or exc
                self._record_error(exc)
            finally:
                await self._cancel_background_tasks()
                self._state = PlatformState.FAILED if error is not None else PlatformState.STOPPED
                self._failed_start_cleaned = error is None
            if error is not None:
                raise error

    async def _quiesce(self) -> None:
        """Adapter hook that stops ingress without closing outbound transport."""

        return None

    @abstractmethod
    async def _start(self) -> None:
        ...

    @abstractmethod
    async def _stop(self) -> None:
        ...

    async def send_message(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
    ) -> DeliveryReceipt:
        if self._state not in {PlatformState.STARTING, PlatformState.RUNNING, PlatformState.STOPPING} or not self._transport_ready:
            self._stats["send_failures"] += 1
            raise PlatformNotReadyError(f"{self.name} transport is not ready")
        try:
            receipt = await self._send_message(user_id, content, group_id)
        except BaseException as exc:
            self._stats["send_failures"] += 1
            self._record_error(exc)
            raise
        self._stats["sent"] += 1
        return receipt

    @abstractmethod
    async def _send_message(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
    ) -> DeliveryReceipt:
        ...

    async def send_image(self, user_id: str, image_path: str, group_id: str | None = None) -> DeliveryReceipt:
        raise NotImplementedError(f"{self.name} does not support image delivery")

    async def send_long_message(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
    ) -> list[DeliveryReceipt]:
        """Split a long response and return a receipt for every accepted chunk."""

        chunks = split_message(content, max_length=self._max_msg_length)
        delay = get_delay()
        receipts: list[DeliveryReceipt] = []
        for index, chunk in enumerate(chunks):
            if index:
                await delay.wait(len(chunk))
            receipts.append(await self.send_message(user_id, chunk, group_id))
        return receipts

    async def deliver_reply(self, message: UnifiedMessage, plan: ReplyPlan) -> None:
        """Default delivery: collapse bubbles back into the legacy text path."""

        text = plan.text()
        if text:
            await self.send_long_message(message.user_id, text, message.group_id)

    async def handle_callback(self, message: UnifiedMessage) -> str | ReplyPlan | None:
        """Dispatch an inbound event once and retry only an unfinished delivery."""

        self._stats["received"] += 1
        if not self._accepting_messages:
            self._stats["ignored"] += 1
            return None
        if self._callback is None:
            self._stats["ignored"] += 1
            return None

        loop = asyncio.get_running_loop()
        dispatch_done: asyncio.Future[None] = loop.create_future()
        self._inflight_dispatches[dispatch_done] = asyncio.current_task()

        try:
            return await self._dispatch_message(message)
        finally:
            self._inflight_dispatches.pop(dispatch_done, None)
            if not dispatch_done.done():
                dispatch_done.set_result(None)

    async def _dispatch_message(self, message: UnifiedMessage) -> str | ReplyPlan | None:
        """Run one already-admitted callback and its complete response delivery."""

        event_key = message.event_key
        record = self._dispatch_cache.get(event_key) if event_key else None
        if record is not None:
            self._dispatch_cache.move_to_end(event_key)
            self._stats["duplicates"] += 1
            if record.delivered:
                return record.response
            response = record.response
        else:
            try:
                response = await self._callback(message)
            except BaseException as exc:
                self._stats["callback_failures"] += 1
                self._record_error(exc)
                raise
            if event_key:
                record = _DispatchRecord(response=response)
                self._dispatch_cache[event_key] = record
                while len(self._dispatch_cache) > self._dispatch_cache_size:
                    self._dispatch_cache.popitem(last=False)

        if isinstance(response, ReplyPlan):
            await self.deliver_reply(message, response)
        elif response:
            await self.send_long_message(message.user_id, response, message.group_id)
        if record is not None:
            record.delivered = True
        self._stats["processed"] += 1
        return response

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.value,
            "running": self.is_running,
            "ready": self.is_ready,
            "accepting_messages": self._accepting_messages,
            "inflight_messages": len(self._inflight_dispatches),
            "background_tasks": sum(not task.done() for task in self._tasks),
            "shutdown_drain_timed_out": self._shutdown_drain_timed_out,
            "last_error": self._last_error,
            "stats": dict(self._stats),
        }

    def _record_ignored(self) -> None:
        self._stats["ignored"] += 1

    def _record_transport_error(self, exc: BaseException) -> None:
        self._stats["transport_errors"] += 1
        self._record_error(exc)

    def _record_error(self, exc: BaseException) -> None:
        # Exception text can contain credentials embedded in request URLs.
        self._last_error = type(exc).__name__

    def _create_task(self, coroutine: Awaitable[Any], *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=f"platform:{self.name}:{name}")
        self._tasks.add(task)

        def discard(completed: asyncio.Task[Any]) -> None:
            self._tasks.discard(completed)
            if completed.cancelled():
                return
            try:
                error = completed.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                self._record_transport_error(error)
                logger.error("%s background task %s failed (%s)", self.name, completed.get_name(), type(error).__name__)

        task.add_done_callback(discard)
        return task

    async def _drain_inflight_dispatches(self) -> None:
        current = asyncio.current_task()
        dispatches = {
            marker: owner
            for marker, owner in self._inflight_dispatches.items()
            if owner is not current and not marker.done()
        }
        if not dispatches:
            return

        _, pending = await asyncio.wait(dispatches, timeout=self._shutdown_drain_timeout)
        if not pending:
            return

        self._shutdown_drain_timed_out = True
        logger.warning(
            "%s shutdown drain timed out with %d message(s); cancelling owners",
            self.name,
            len(pending),
        )
        owners = {dispatches[marker] for marker in pending if dispatches[marker] is not None}
        for task in owners:
            if task is not None and not task.done():
                task.cancel()
        _, still_pending = await asyncio.wait(pending, timeout=self._shutdown_cancel_timeout)
        if still_pending:
            logger.error(
                "%s has %d cancellation-resistant message handler(s)",
                self.name,
                len(still_pending),
            )

    async def _close_transport(self) -> None:
        close_task = asyncio.create_task(self._stop(), name=f"platform:{self.name}:close")
        try:
            done, pending = await asyncio.wait({close_task}, timeout=self._shutdown_close_timeout)
        except asyncio.CancelledError:
            close_task.cancel()
            _, pending = await asyncio.wait({close_task}, timeout=self._shutdown_cancel_timeout)
            if pending:
                close_task.add_done_callback(self._observe_detached_task)
            raise
        if not pending:
            await close_task
            return

        logger.warning("%s transport close timed out; cancelling it", self.name)
        close_task.cancel()
        done, pending = await asyncio.wait(pending, timeout=self._shutdown_cancel_timeout)
        if pending:
            logger.error("%s transport close resisted cancellation and was detached", self.name)
            close_task.add_done_callback(self._observe_detached_task)
        elif done:
            await asyncio.gather(*done, return_exceptions=True)
        raise TimeoutError(f"{self.name} transport close timed out")

    @staticmethod
    def _observe_detached_task(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            return

    async def _cancel_background_tasks(self) -> None:
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self._shutdown_cancel_timeout)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            if pending:
                logger.error(
                    "%s has %d cancellation-resistant background task(s)",
                    self.name,
                    len(pending),
                )
        self._tasks.difference_update(task for task in tasks if task.done())
