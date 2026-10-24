"""Opt-in proactive message tests。"""
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from core.proactive import ProactiveService, ProactiveStore
from core.session import Session, SessionManager

LOCAL = ZoneInfo("Asia/Shanghai")


def _manager(*sessions: Session) -> SessionManager:
    manager = SessionManager()
    manager.restore(list(sessions))
    return manager


def _session(user_id: str = "u1", *, last_active: float) -> Session:
    session = Session(session_id=f"qq:{user_id}", user_id=user_id, platform="qq")
    session.last_active = datetime.fromtimestamp(last_active)
    return session


def _service(tmp_path, manager, *, users=None, daily_limit=2, interval=300):
    store = ProactiveStore(str(tmp_path / "proactive.json"))
    generator = AsyncMock(return_value="在干嘛，今天还顺利吗？")
    sender = AsyncMock()
    service = ProactiveService(
        store,
        manager,
        sender,
        generator,
        users=users if users is not None else ["u1"],
        min_idle_seconds=3600,
        daily_limit=daily_limit,
        quiet_start=23,
        quiet_end=8,
        interval=interval,
    )
    return service, store, generator, sender


@pytest.mark.asyncio
async def test_idle_allowlisted_user_gets_one_message(tmp_path):
    now = datetime(2026, 10, 24, 12, 0, tzinfo=LOCAL).timestamp()
    manager = _manager(_session(last_active=now - 7200))
    service, store, generator, sender = _service(tmp_path, manager)

    assert await service.run_once(now) == 1
    sender.assert_awaited_once()
    generator.assert_awaited_once()
    assert store.count_today("qq:u1", "2026-10-24") == 1


@pytest.mark.asyncio
async def test_recent_activity_and_cooldown_prevent_spam(tmp_path):
    now = datetime(2026, 10, 24, 12, 0, tzinfo=LOCAL).timestamp()
    manager = _manager(_session(last_active=now - 7200))
    service, store, _generator, sender = _service(tmp_path, manager)

    assert await service.run_once(now) == 1
    assert await service.run_once(now + 60) == 0
    assert await service.run_once(now + 7200) == 1
    assert await service.run_once(now + 14400) == 0  # daily limit reached
    assert sender.await_count == 2


@pytest.mark.asyncio
async def test_quiet_hours_and_allowlist_are_respected(tmp_path):
    late = datetime(2026, 10, 24, 23, 30, tzinfo=LOCAL).timestamp()
    manager = _manager(_session(last_active=late - 7200))
    service, _store, _generator, sender = _service(tmp_path, manager)
    assert await service.run_once(late) == 0

    noon = datetime(2026, 10, 24, 12, 0, tzinfo=LOCAL).timestamp()
    manager = _manager(_session(last_active=noon - 7200))
    service, _store, _generator, sender = _service(tmp_path, manager, users=["someone-else"])
    assert await service.run_once(noon) == 0
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_sessions_are_not_used_for_private_proactive_messages(tmp_path):
    now = datetime(2026, 10, 24, 12, 0, tzinfo=LOCAL).timestamp()
    group = Session(session_id="qq:123456", user_id="u1", platform="qq")
    group.last_active = datetime.fromtimestamp(now - 7200)
    manager = _manager(group)
    service, _store, _generator, sender = _service(tmp_path, manager)

    assert await service.run_once(now) == 0
    sender.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_generation_is_not_recorded(tmp_path):
    now = datetime(2026, 10, 24, 12, 0, tzinfo=LOCAL).timestamp()
    manager = _manager(_session(last_active=now - 7200))
    service, store, generator, sender = _service(tmp_path, manager)
    generator.return_value = "   "

    assert await service.run_once(now) == 0
    assert store.last_sent("qq:u1") == 0.0
    sender.assert_not_awaited()


def test_store_survives_restart(tmp_path):
    path = str(tmp_path / "proactive.json")
    store = ProactiveStore(path)
    now = datetime(2026, 10, 24, 12, 0, tzinfo=LOCAL).timestamp()

    store.record("qq:u1", now)
    reloaded = ProactiveStore(path)

    assert reloaded.count_today("qq:u1", "2026-10-24") == 1
    assert reloaded.last_sent("qq:u1") == now
