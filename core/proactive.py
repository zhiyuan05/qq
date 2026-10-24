"""Opt-in proactive private messages after a long silence.

The service only touches sessions whose user is explicitly allowlisted. It
uses the same LLM as normal chat, but generation is one short message and is
rate-limited per day so a restart cannot spam the owner.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.session import Session, SessionManager
from utils.logger import setup_logger

logger = setup_logger("proactive")
LOCAL_TZ = ZoneInfo("Asia/Shanghai")


class ProactiveStore:
    """Small JSON ledger of proactive sends, keyed by session id."""

    def __init__(self, path: str = "data/proactive.json"):
        self.path = Path(path)
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._data = {
                str(key): value
                for key, value in raw.items()
                if isinstance(value, dict)
            }

    def last_sent(self, session_id: str) -> float:
        try:
            return float(self._data.get(session_id, {}).get("last_sent", 0.0))
        except (TypeError, ValueError):
            return 0.0

    def count_today(self, session_id: str, day: str) -> int:
        record = self._data.get(session_id, {})
        if record.get("day") != day:
            return 0
        try:
            return int(record.get("count", 0))
        except (TypeError, ValueError):
            return 0

    def record(self, session_id: str, now: float) -> None:
        day = datetime.fromtimestamp(now, LOCAL_TZ).date().isoformat()
        count = self.count_today(session_id, day) + 1
        self._data[session_id] = {"last_sent": now, "day": day, "count": count}
        self._flush()

    def _flush(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            logger.warning("主动消息状态写入失败 (%s)", type(exc).__name__)


class ProactiveService:
    """Periodically offers idle allowlisted users one short message."""

    def __init__(
        self,
        store: ProactiveStore,
        sessions: SessionManager,
        sender: Callable[[Session, str], Awaitable[None]],
        generator: Callable[[Session], Awaitable[str | None]],
        *,
        enabled: bool = True,
        users: list[str] | None = None,
        min_idle_seconds: float = 4 * 3600,
        daily_limit: int = 2,
        quiet_start: int = 23,
        quiet_end: int = 8,
        interval: float = 300.0,
    ):
        self.store = store
        self.sessions = sessions
        self.sender = sender
        self.generator = generator
        self.enabled = enabled
        self.users = {str(user) for user in (users or [])}
        self.min_idle_seconds = max(0.0, float(min_idle_seconds))
        self.daily_limit = max(0, int(daily_limit))
        self.quiet_start = int(quiet_start)
        self.quiet_end = int(quiet_end)
        self.interval = max(1.0, float(interval))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if not self.enabled or self.daily_limit <= 0:
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="engine:proactive")
        logger.info(
            "主动消息服务已启动（用户 %d，最小空闲 %.0f 分钟，每日 %d 条）",
            len(self.users),
            self.min_idle_seconds / 60,
            self.daily_limit,
        )

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
                logger.warning("主动消息检查失败 (%s)", type(exc).__name__)

    def _allowed(self, session: Session) -> bool:
        if not self.users:
            return False
        return (
            session.user_id in self.users
            or session.session_id in self.users
            or f"{session.platform}:{session.user_id}" in self.users
        )

    def _quiet(self, hour: int) -> bool:
        if self.quiet_start == self.quiet_end:
            return False
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= hour < self.quiet_end
        return hour >= self.quiet_start or hour < self.quiet_end

    async def run_once(self, now: float | None = None) -> int:
        if not self.enabled or self.daily_limit <= 0:
            return 0
        moment = time.time() if now is None else now
        hour = datetime.fromtimestamp(moment, LOCAL_TZ).hour
        if self._quiet(hour):
            return 0

        day = datetime.fromtimestamp(moment, LOCAL_TZ).date().isoformat()
        delivered = 0
        for session_id in self.sessions.list_sessions():
            session = self.sessions.get(session_id)
            if session is None or session.platform != "qq":
                continue
            # Only private sessions: a group session's id is the group id,
            # while its user_id is whoever spoke last.
            if session.session_id != f"{session.platform}:{session.user_id}":
                continue
            if not self._allowed(session):
                continue
            if moment - session.last_active.timestamp() < self.min_idle_seconds:
                continue
            if moment - self.store.last_sent(session_id) < self.min_idle_seconds:
                continue
            if self.store.count_today(session_id, day) >= self.daily_limit:
                continue
            try:
                text = (await self.generator(session) or "").strip()
            except Exception as exc:
                logger.debug("主动消息生成失败 (%s)", type(exc).__name__)
                continue
            if not text:
                continue
            try:
                await self.sender(session, text)
            except Exception as exc:
                logger.warning("主动消息发送失败 (%s)", type(exc).__name__)
                continue
            self.store.record(session_id, moment)
            logger.info("已向 %s 发送主动消息", session_id)
            delivered += 1
        return delivered
