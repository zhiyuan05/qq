import time
from datetime import datetime

import pytest

from core.reminders import (
    LOCAL_TZ,
    Reminder,
    ReminderService,
    ReminderStore,
    next_clock_time,
    parse_duration,
    parse_natural_reminder,
)


def _at(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 24, hour, minute, tzinfo=LOCAL_TZ).timestamp()


def test_parse_duration_accepts_mixed_units():
    assert parse_duration("10m") == 600
    assert parse_duration("1h30m") == 5400
    assert parse_duration("2小时30分钟") == 9000
    assert parse_duration("45s") == 45


def test_parse_duration_rejects_unknown_text():
    assert parse_duration("soon") is None
    assert parse_duration("10mabc") is None
    assert parse_duration("") is None


def test_parse_natural_reminder_relative():
    parsed = parse_natural_reminder("提醒我10分钟后喝水", now=_at(15))
    assert parsed is not None
    assert parsed.due_at == _at(15) + 600
    assert parsed.text == "喝水"
    assert parsed.repeat == "once"


def test_parse_natural_reminder_tomorrow_morning():
    parsed = parse_natural_reminder("明天早上8点提醒我带伞", now=_at(15))
    assert parsed is not None
    assert parsed.due_at == _at(8) + 86400
    assert parsed.text == "带伞"


def test_parse_natural_reminder_daily_and_chinese_hour():
    parsed = parse_natural_reminder("每天晚上九点半提醒我吃药", now=_at(10))
    assert parsed is not None
    assert parsed.repeat == "daily"
    assert parsed.text == "吃药"
    due = datetime.fromtimestamp(parsed.due_at, LOCAL_TZ)
    assert (due.hour, due.minute) == (21, 30)


def test_parse_natural_reminder_handles_midnight_and_shorthand():
    at_23 = _at(23)
    late = parse_natural_reminder("凌晨12点提醒我喝水", now=at_23)
    assert late is not None
    assert datetime.fromtimestamp(late.due_at, LOCAL_TZ).strftime("%d %H:%M") == "25 00:00"

    noon = parse_natural_reminder("晚上12点提醒我喝水", now=_at(10))
    assert noon is not None
    assert datetime.fromtimestamp(noon.due_at, LOCAL_TZ).strftime("%d %H:%M") == "25 00:00"

    tonight = parse_natural_reminder("今晚8点提醒我吃药", now=_at(10))
    assert tonight is not None
    assert datetime.fromtimestamp(tonight.due_at, LOCAL_TZ).strftime("%d %H:%M") == "24 20:00"

    tomorrow = parse_natural_reminder("明早8点提醒我吃药", now=_at(6))
    assert tomorrow is not None
    assert datetime.fromtimestamp(tomorrow.due_at, LOCAL_TZ).strftime("%d %H:%M") == "25 08:00"


def test_parse_natural_reminder_keeps_incidental_daily_word():
    parsed = parse_natural_reminder("提醒我9点看每天新闻", now=_at(8))
    assert parsed is not None
    assert parsed.repeat == "once"
    assert "每天" in parsed.text


@pytest.mark.parametrize(
    "text",
    [
        "他叫我8点去开会",
        "同事提醒我9点交报告",
        "别提醒我下午3点开会",
        "晚一点提醒我",
        "别忘了还有一点",
        "你提醒我一下我昨天10点睡的吗",
        "你提醒我一下8点那件事怎么样了",
        "我晚上10点要加班，你提醒我一下明天带伞",
    ],
)
def test_parse_natural_reminder_rejects_non_requests(text):
    assert parse_natural_reminder(text, now=_at(10)) is None


def test_parse_natural_reminder_requires_time_and_text():
    assert parse_natural_reminder("提醒我一下你刚才说的", now=_at(15)) is None
    assert parse_natural_reminder("叫我起床", now=_at(15)) is None
    assert parse_natural_reminder("10分钟后提醒我", now=_at(15)) is None


def test_parse_natural_reminder_clock_rolls_forward():
    parsed = parse_natural_reminder("8点提醒我开会", now=_at(15))
    assert parsed is not None
    assert parsed.due_at == _at(8) + 86400
    assert parsed.text == "开会"


def test_next_clock_time_rolls_to_tomorrow():
    now = time.time()
    due = next_clock_time("09:00", now=now)
    assert due is not None
    assert due > now
    assert next_clock_time("25:00", now=now) is None


@pytest.mark.asyncio
async def test_engine_intercepts_natural_reminder(tmp_path):
    from unittest.mock import AsyncMock

    from core.engine import Engine
    from platforms.models import Platform, UnifiedMessage
    from utils.config import AppConfig

    engine = Engine(
        AppConfig(
            memory={"long_term": {"enabled": False}, "entity": {"auto_extract": False}},
            rate_limit={"enabled": False},
        ),
        llm_client=AsyncMock(),
        reminder_store=ReminderStore(str(tmp_path / "reminders.json")),
    )
    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="1",
        content="提醒我10分钟后喝水",
    )

    reply = await engine._maybe_natural_reminder(message)

    assert reply is not None
    assert "提醒你：喝水" in reply
    stored = await engine.reminder_store.list_for("user", "1")
    assert [item.text for item in stored] == ["喝水"]


@pytest.mark.asyncio
async def test_store_completes_once_and_advances_daily(tmp_path):
    store = ReminderStore(str(tmp_path / "reminders.json"))
    now = time.time()
    once = Reminder("aaa", "user", "1", "喝水", now - 5)
    daily = Reminder("bbb", "user", "1", "吃药", now - 5, repeat="daily")
    await store.add(once)
    await store.add(daily)

    assert {item.id for item in await store.peek_due(now)} == {"aaa", "bbb"}

    await store.complete(once, now)
    await store.complete(daily, now)
    remaining = await store.list_for("user", "1")

    assert [item.id for item in remaining] == ["bbb"]
    assert remaining[0].due_at > now


@pytest.mark.asyncio
async def test_service_delivers_and_retries_after_failure(tmp_path):
    store = ReminderStore(str(tmp_path / "reminders.json"))
    now = time.time()
    await store.add(Reminder("ccc", "user", "1", "开会", now - 1))
    sent = []

    async def sender(item):
        sent.append(item.id)

    service = ReminderService(store, sender)
    assert await service.run_once(now) == 1
    assert sent == ["ccc"]
    assert await store.list_for("user", "1") == []

    await store.add(Reminder("ddd", "user", "1", "开会", now - 1))
    failures = {"count": 0}

    async def broken_sender(item):
        failures["count"] += 1
        raise RuntimeError("offline")

    service = ReminderService(store, broken_sender)
    assert await service.run_once(now) == 0
    assert failures["count"] == 1
    assert await service.run_once(now + 10) == 0
    assert failures["count"] == 1
    assert [item.id for item in await store.list_for("user", "1")] == ["ddd"]
