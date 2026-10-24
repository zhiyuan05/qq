"""Contract tests shared by all platform adapters."""
from __future__ import annotations

import asyncio

import pytest

from platforms.base import BasePlatform, PlatformDeliveryError, PlatformNotReadyError, PlatformState
from platforms.manager import PlatformManager
from platforms.models import DeliveryReceipt, Platform, UnifiedMessage


class StubPlatform(BasePlatform):
    def __init__(self, name: str = "stub", *, fail_start: bool = False, fail_sends: int = 0):
        self.name = name
        super().__init__({"max_msg_length": 100})
        self.fail_start = fail_start
        self.fail_sends = fail_sends
        self.start_calls = 0
        self.stop_calls = 0
        self.send_calls: list[str] = []
        self.cancelled = asyncio.Event()

    async def _start(self) -> None:
        self.start_calls += 1
        if self.fail_start:
            raise RuntimeError("secret-bearing startup detail")

    async def _stop(self) -> None:
        self.stop_calls += 1

    async def _send_message(self, user_id: str, content: str, group_id: str | None = None) -> DeliveryReceipt:
        self.send_calls.append(content)
        if self.fail_sends:
            self.fail_sends -= 1
            raise PlatformDeliveryError("temporary delivery failure")
        return DeliveryReceipt(platform=Platform.CONSOLE, target_id=group_id or user_id, message_id="accepted")

    def add_background_task(self) -> None:
        async def wait_forever() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()

        self._create_task(wait_forever(), name="test-worker")


@pytest.mark.asyncio
async def test_lifecycle_is_idempotent_and_reaps_owned_tasks():
    platform = StubPlatform()
    await platform.start()
    await platform.start()
    platform.add_background_task()
    await asyncio.sleep(0)

    assert platform.state is PlatformState.RUNNING
    assert platform.is_ready is True
    assert platform.start_calls == 1

    await platform.stop()
    await platform.stop()

    assert platform.state is PlatformState.STOPPED
    assert platform.stop_calls == 1
    assert platform.cancelled.is_set()
    assert not platform._tasks


@pytest.mark.asyncio
async def test_failed_start_is_cleaned_and_exposes_only_error_type():
    platform = StubPlatform(fail_start=True)

    with pytest.raises(RuntimeError, match="startup detail"):
        await platform.start()

    assert platform.state is PlatformState.FAILED
    assert platform.stop_calls == 1
    assert platform.status_snapshot()["last_error"] == "RuntimeError"
    assert "secret" not in str(platform.status_snapshot())


@pytest.mark.asyncio
async def test_send_requires_readiness_and_returns_delivery_receipt():
    platform = StubPlatform()
    with pytest.raises(PlatformNotReadyError):
        await platform.send_message("u1", "hello")

    await platform.start()
    receipt = await platform.send_message("u1", "hello")

    assert receipt.target_id == "u1"
    assert receipt.message_id == "accepted"
    assert platform.status_snapshot()["stats"]["sent"] == 1
    await platform.stop()


@pytest.mark.asyncio
async def test_failed_response_delivery_replays_without_running_callback_twice():
    platform = StubPlatform(fail_sends=1)
    callback_calls = 0

    async def callback(_message: UnifiedMessage) -> str:
        nonlocal callback_calls
        callback_calls += 1
        return "reply"

    platform.on_message(callback)
    await platform.start()
    message = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="hello", message_id="event-1")

    with pytest.raises(PlatformDeliveryError):
        await platform.handle_callback(message)
    await platform.handle_callback(message)
    await platform.handle_callback(message)

    assert callback_calls == 1
    assert platform.send_calls == ["reply", "reply"]
    assert platform.status_snapshot()["stats"] == {
        "received": 3,
        "processed": 1,
        "ignored": 0,
        "duplicates": 2,
        "callback_failures": 0,
        "sent": 1,
        "send_failures": 1,
        "transport_errors": 0,
        "reconnects": 0,
        "dropped": 0,
    }
    await platform.stop()


@pytest.mark.asyncio
async def test_platform_manager_isolates_start_failure_and_stops_in_reverse_order():
    events: list[str] = []

    class OrderedPlatform(StubPlatform):
        async def _stop(self) -> None:
            events.append(self.name)
            await super()._stop()

    async def callback(_message: UnifiedMessage) -> None:
        return None

    manager = PlatformManager(callback)
    first = manager.add(OrderedPlatform("first"))
    broken = manager.add(OrderedPlatform("broken", fail_start=True))
    last = manager.add(OrderedPlatform("last"))

    results = await manager.start_all()

    assert results["first"] is None
    assert isinstance(results["broken"], RuntimeError)
    assert results["last"] is None
    assert manager.running_platforms == (first, last)

    await manager.stop_all()
    assert events == ["broken", "last", "first"]


def test_platform_manager_rejects_duplicate_adapter_names():
    async def callback(_message: UnifiedMessage) -> None:
        return None

    manager = PlatformManager(callback)
    manager.add(StubPlatform("same"))
    with pytest.raises(ValueError, match="already registered"):
        manager.add(StubPlatform("same"))


def test_message_subject_and_group_keys_are_platform_scoped():
    qq = UnifiedMessage(platform=Platform.QQ, user_id="42", content="hello", group_id="7")
    telegram = UnifiedMessage(platform=Platform.TELEGRAM, user_id="42", content="hello", group_id="7")

    assert qq.principal_key == "qq:42"
    assert telegram.principal_key == "telegram:42"
    assert qq.principal_key != telegram.principal_key
    assert qq.group_scope_key == "qq:7"
    assert telegram.group_scope_key == "telegram:7"


@pytest.mark.asyncio
async def test_platform_stop_drains_admitted_message_before_closing_transport():
    platform = StubPlatform()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def callback(_message: UnifiedMessage) -> str:
        entered.set()
        await release.wait()
        return "drained reply"

    platform.on_message(callback)
    await platform.start()
    handling = asyncio.create_task(
        platform.handle_callback(
            UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="hello", message_id="drain-1")
        )
    )
    await entered.wait()

    stopping = asyncio.create_task(platform.stop())
    await asyncio.sleep(0)

    assert platform.state is PlatformState.STOPPING
    assert platform.status_snapshot()["accepting_messages"] is False
    assert platform.status_snapshot()["inflight_messages"] == 1
    assert not stopping.done()

    rejected = await platform.handle_callback(
        UnifiedMessage(platform=Platform.CONSOLE, user_id="u2", content="late", message_id="drain-2")
    )
    assert rejected is None

    release.set()
    assert await handling == "drained reply"
    await stopping

    assert platform.send_calls == ["drained reply"]
    assert platform.state is PlatformState.STOPPED
    assert platform.status_snapshot()["shutdown_drain_timed_out"] is False


@pytest.mark.asyncio
async def test_platform_stop_cancels_message_after_bounded_drain_timeout():
    platform = StubPlatform()
    platform._shutdown_drain_timeout = 0.01
    platform._shutdown_cancel_timeout = 0.1
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def callback(_message: UnifiedMessage) -> str:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return "unreachable"

    platform.on_message(callback)
    await platform.start()
    handling = asyncio.create_task(
        platform.handle_callback(
            UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="hello", message_id="timeout-1")
        )
    )
    await entered.wait()

    await asyncio.wait_for(platform.stop(), timeout=0.5)

    with pytest.raises(asyncio.CancelledError):
        await handling
    assert cancelled.is_set()
    assert platform.status_snapshot()["shutdown_drain_timed_out"] is True
    assert platform.state is PlatformState.STOPPED


@pytest.mark.asyncio
async def test_platform_manager_quiesces_every_ingress_before_stopping_any_adapter():
    events: list[str] = []
    quiesced: set[str] = set()

    class PhasedPlatform(StubPlatform):
        async def _quiesce(self) -> None:
            events.append(f"quiesce:{self.name}")
            quiesced.add(self.name)

        async def _stop(self) -> None:
            assert quiesced == {"first", "second"}
            events.append(f"stop:{self.name}")
            await super()._stop()

    async def callback(_message: UnifiedMessage) -> None:
        return None

    manager = PlatformManager(callback)
    manager.add(PhasedPlatform("first"))
    manager.add(PhasedPlatform("second"))
    await manager.start_all()

    await manager.stop_all()

    stop_index = min(index for index, event in enumerate(events) if event.startswith("stop:"))
    assert all(event.startswith("quiesce:") for event in events[:stop_index])
