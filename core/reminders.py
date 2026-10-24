"""Persistent one-shot and daily reminders delivered through the chat platform."""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.logger import setup_logger

logger = setup_logger("reminders")

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_DURATION = re.compile(r"(\d+)\s*(d|天|h|小时|m|分钟|s|秒)", re.IGNORECASE)
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class NaturalReminder:
    due_at: float
    text: str
    repeat: str = "once"


@dataclass
class Reminder:
    id: str
    target_type: str
    target_id: str
    text: str
    due_at: float
    repeat: str = "once"
    created_by: str = ""


def parse_duration(text: str) -> float | None:
    """Parse ``10m``, ``2小时30分钟``, ``45s`` into seconds."""
    cleaned = text.strip().lower()
    if not cleaned:
        return None
    matches = list(_DURATION.finditer(cleaned))
    if not matches:
        return None
    # Reject leftover characters such as "10mabc".
    if _DURATION.sub("", cleaned).replace(" ", ""):
        return None
    total = 0
    for match in matches:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        total += amount * {
            "d": 86400,
            "天": 86400,
            "h": 3600,
            "小时": 3600,
            "m": 60,
            "分钟": 60,
            "s": 1,
            "秒": 1,
        }[unit]
    return float(total)


def next_clock_time(clock: str, *, now: float | None = None) -> float | None:
    """Next occurrence of a local ``HH:MM`` (Asia/Shanghai)."""
    match = _CLOCK.match(clock.strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    current = datetime.fromtimestamp(now if now is not None else time.time(), LOCAL_TZ)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target.timestamp()


def format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, LOCAL_TZ).strftime("%m-%d %H:%M")


_RELATIVE_TIME = re.compile(
    r"(?P<duration>(?:\d+\s*(?:天|小时|分钟|秒)\s*)+)后"
)
_NATURAL_CLOCK = re.compile(
    r"(?P<period>今天|明天|后天|今晚|明晚|今早|明早)?\s*"
    r"(?P<part>凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*"
    r"(?P<hour>\d{1,2}|[一二两三四五六七八九十]{1,3})\s*[点时]\s*"
    r"(?P<minute>半|\d{1,2}\s*分?)?"
)
_REMIND_TRIGGER = re.compile(
    r"(?:请|麻烦)?(?:你)?(?:记得)?(?:提醒我一下|提醒我|到时提醒我|叫我一下|叫我|别忘了)"
)
_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _number(value: str) -> int | None:
    value = value.strip()
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if value.startswith("十") and len(value) == 2:
        return 10 + _CN_DIGITS.get(value[1], -100)
    if value.endswith("十") and len(value) == 2:
        return _CN_DIGITS.get(value[0], 0) * 10
    if len(value) == 3 and "十" in value:
        head, _, tail = value.partition("十")
        return _CN_DIGITS.get(head, 0) * 10 + _CN_DIGITS.get(tail, 0)
    return _CN_DIGITS.get(value)


def _natural_clock(text: str, now: float) -> tuple[float, str, int, int] | None:
    match = _NATURAL_CLOCK.search(text)
    if not match:
        return None
    hour = _number(match.group("hour"))
    if hour is None or hour > 24:
        return None
    minute_text = (match.group("minute") or "").strip()
    if minute_text == "半":
        minute = 30
    elif minute_text:
        minute = int(re.sub(r"\D", "", minute_text))
    else:
        minute = 0
    if minute > 59:
        return None

    period = match.group("period") or ""
    part = match.group("part") or ""
    if period in {"今晚", "明晚"}:
        period = "明天" if period == "明晚" else "今天"
        part = part or "晚上"
    elif period in {"今早", "明早"}:
        period = "明天" if period == "明早" else "今天"
        part = part or "早上"

    before = text[: match.start()].rstrip()[-1:]
    if (
        not period
        and not part
        and not minute_text
        and before
        and before in "早晚快慢稍再等有这那多差"
    ):
        return None
    if not period and not part and not minute_text and match.group("hour") in {"一", "两", "二"}:
        return None

    roll = 0
    if part == "凌晨" and hour == 12:
        hour = 0
    elif part in {"晚上", "傍晚"} and hour == 12:
        hour, roll = 0, 1
    elif part in {"下午", "傍晚", "晚上"} and 0 < hour < 12:
        hour += 12
    elif part == "中午" and hour < 11:
        hour += 12
    if hour == 24:
        hour = 0
    if hour > 23:
        return None

    current = datetime.fromtimestamp(now, LOCAL_TZ)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target += timedelta(days=roll)
    if period == "明天":
        target += timedelta(days=1)
    elif period == "后天":
        target += timedelta(days=2)
    elif period == "今天":
        if target <= current:
            return None
    elif target <= current:
        target += timedelta(days=1)
    return target.timestamp(), match.group(0), match.start(), match.end()


def parse_natural_reminder(text: str, *, now: float | None = None) -> NaturalReminder | None:
    """Parse private-chat phrases like ``明天早上8点提醒我带伞``."""
    raw = (text or "").strip()
    trigger = _REMIND_TRIGGER.search(raw)
    if not raw or trigger is None:
        return None
    prefix = raw[: trigger.start()]
    if re.search(r"(?:别|不要|不用|无需|不必)\s*$", prefix):
        return None
    if re.search(r"(?:他|她|它|同事|老板|朋友|老师|我妈|我爸|人家|别人)[^，。]{0,6}$", prefix):
        return None
    if re.search(r"(?:吗|呢|么)\s*[?？]?\s*$", raw) or re.search(r"[?？]\s*$", raw):
        return None

    current = now if now is not None else time.time()
    distance_limit = 3
    candidates: list[tuple[int, float, str, int, int]] = []
    clock_result = _natural_clock(raw, current)
    if clock_result is not None:
        due, matched, start, end = clock_result
        distance = (
            start - trigger.end()
            if start >= trigger.end()
            else trigger.start() - end
            if end <= trigger.start()
            else 0
        )
        if 0 <= distance <= distance_limit:
            candidates.append((distance, due, matched, start, end))
    relative = _RELATIVE_TIME.search(raw)
    if relative is not None:
        seconds = parse_duration(relative.group("duration"))
        if seconds:
            distance = (
                relative.start() - trigger.end()
                if relative.start() >= trigger.end()
                else trigger.start() - relative.end()
                if relative.end() <= trigger.start()
                else 0
            )
            if 0 <= distance <= distance_limit:
                candidates.append(
                    (distance, current + seconds, relative.group(0), relative.start(), relative.end())
                )
    if not candidates:
        return None
    _distance, due, matched, start, end = min(candidates, key=lambda item: item[0])
    if due <= current + 5:
        return None

    body = raw[:start] + " " + raw[end:]
    body = _REMIND_TRIGGER.sub(" ", body)
    daily = bool(
        clock_result is not None
        and re.search(r"每天\s*$", raw[: clock_result[2]])
    )
    if daily:
        body = re.sub(r"^\s*每天\s*", "", body)
    body = re.sub(r"^(?:一下|帮我|给我|记得|要|去|到时候|在)\s*", "", body.strip())
    body = body.strip(" \t，,。.！!？?、：:；;~～")
    if not body or len(body) > 200:
        return None
    if re.search(r"(?:吗|呢|怎么样|如何|是不是|什么|哪|几点|吗)", body):
        return None
    return NaturalReminder(
        due_at=due,
        text=body[:200],
        repeat="daily" if daily and clock_result is not None else "once",
    )


class ReminderStore:
    def __init__(self, path: str = "data/reminders.json"):
        self.path = Path(path)
        self._lock = asyncio.Lock()

    def _load(self) -> list[Reminder]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items: list[Reminder] = []
        for row in raw if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                items.append(
                    Reminder(
                        id=str(row["id"]),
                        target_type=str(row["target_type"]),
                        target_id=str(row["target_id"]),
                        text=str(row["text"]),
                        due_at=float(row["due_at"]),
                        repeat=str(row.get("repeat", "once")),
                        created_by=str(row.get("created_by", "")),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return items

    def _save(self, items: list[Reminder]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    async def add(self, reminder: Reminder) -> None:
        async with self._lock:
            items = self._load()
            items.append(reminder)
            self._save(items)

    async def list_for(self, target_type: str, target_id: str) -> list[Reminder]:
        async with self._lock:
            items = self._load()
        return sorted(
            (item for item in items if item.target_type == target_type and item.target_id == target_id),
            key=lambda item: item.due_at,
        )

    async def remove(self, reminder_id: str) -> bool:
        async with self._lock:
            items = self._load()
            remaining = [item for item in items if item.id != reminder_id]
            if len(remaining) == len(items):
                return False
            self._save(remaining)
            return True

    async def peek_due(self, now: float) -> list[Reminder]:
        async with self._lock:
            return [item for item in self._load() if item.due_at <= now]

    async def complete(self, reminder: Reminder, now: float) -> None:
        async with self._lock:
            items = self._load()
            for index, item in enumerate(items):
                if item.id != reminder.id:
                    continue
                if item.repeat == "daily":
                    while item.due_at <= now:
                        item.due_at += 86400
                else:
                    items.pop(index)
                break
            self._save(items)

    @staticmethod
    def new_id() -> str:
        return secrets.token_hex(4)


class ReminderService:
    """Checks due reminders and delivers them through an injected sender."""

    def __init__(
        self,
        store: ReminderStore,
        sender: Callable[[Reminder], Awaitable[None]],
        *,
        interval: float = 20.0,
    ):
        self.store = store
        self.sender = sender
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._retry_after: dict[str, float] = {}

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="engine:reminders")

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            self._task = None
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("提醒检查失败 (%s)", type(exc).__name__)

    async def run_once(self, now: float | None = None) -> int:
        current = now if now is not None else time.time()
        due = await self.store.peek_due(current)
        delivered = 0
        for item in due:
            if self._retry_after.get(item.id, 0) > current:
                continue
            try:
                await self.sender(item)
            except Exception as exc:
                self._retry_after[item.id] = current + 60
                logger.warning("提醒发送失败 (%s)", type(exc).__name__)
                continue
            await self.store.complete(item, current)
            self._retry_after.pop(item.id, None)
            delivered += 1
        return delivered
