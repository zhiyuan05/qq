"""Three-tier rate limiter: per-user, per-group, global + daily cap + URL limit + depth control."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("rate_limiter")


class RateLimiter:
    """滑动窗口限流器，使用 deque 实现 O(1) 的过期清理。

    三层限流 + 每日上限 + 对话深度控制 + URL 频率限制。
    """

    def __init__(
        self,
        per_user_per_minute: int = 10,
        per_group_per_minute: int = 20,
        global_per_hour: int = 200,
        cooldown_minutes: int = 5,
        daily_message_cap: int = 500,
        url_per_hour: int = 5,
        max_conversation_depth: int = 20,
        depth_idle_reset_seconds: int = 1800,
    ):
        self.per_user_per_minute = per_user_per_minute
        self.per_group_per_minute = per_group_per_minute
        self.global_per_hour = global_per_hour
        self.cooldown_minutes = cooldown_minutes
        self.daily_message_cap = daily_message_cap
        self.url_per_hour = url_per_hour
        self.max_conversation_depth = max_conversation_depth
        self.depth_idle_reset = depth_idle_reset_seconds

        # deque 替代 list — O(1) 左侧弹出
        self._user_timestamps: dict[str, deque[float]] = defaultdict(deque)
        self._group_timestamps: dict[str, deque[float]] = defaultdict(deque)
        self._global_timestamps: deque[float] = deque()
        self._daily_count: int = 0
        self._daily_reset_time: float = time.time()
        self._url_timestamps: deque[float] = deque()
        self._depth_counts: dict[str, int] = defaultdict(int)
        self._depth_timestamps: dict[str, float] = defaultdict(float)

    def check(self, user_id: str, group_id: Optional[str] = None) -> tuple[bool, str]:
        now = time.time()

        self._maybe_reset_daily(now)
        if self._daily_count >= self.daily_message_cap:
            return False, f"每日消息上限({self.daily_message_cap})已达到"

        self._clean_old(self._global_timestamps, now, 3600)
        if len(self._global_timestamps) >= self.global_per_hour:
            return False, f"全局消息频率超限({self.global_per_hour}/小时)"

        self._clean_old(self._user_timestamps[user_id], now, 60)
        if len(self._user_timestamps[user_id]) >= self.per_user_per_minute:
            return False, f"用户消息频率超限({self.per_user_per_minute}/分钟)"

        if group_id:
            self._clean_old(self._group_timestamps[group_id], now, 60)
            if len(self._group_timestamps[group_id]) >= self.per_group_per_minute:
                return False, f"群消息频率超限({self.per_group_per_minute}/分钟)"

        self._user_timestamps[user_id].append(now)
        self._global_timestamps.append(now)
        if group_id:
            self._group_timestamps[group_id].append(now)
        self._daily_count += 1

        return True, ""

    def check_depth(self, user_id: str) -> tuple[bool, str]:
        now = time.time()
        if now - self._depth_timestamps[user_id] > self.depth_idle_reset:
            self._depth_counts[user_id] = 0

        if self.max_conversation_depth <= 0:
            return True, ""
        if self._depth_counts[user_id] >= self.max_conversation_depth:
            return False, f"对话深度超限({self.max_conversation_depth})，请稍后再聊"
        return True, ""

    def record_exchange(self, user_id: str):
        self._depth_counts[user_id] += 1
        self._depth_timestamps[user_id] = time.time()

    def reset_depth(self, user_id: str):
        self._depth_counts[user_id] = 0

    def check_url_limit(self) -> tuple[bool, str]:
        now = time.time()
        self._clean_old(self._url_timestamps, now, 3600)
        if len(self._url_timestamps) >= self.url_per_hour:
            return False, f"URL发送频率超限({self.url_per_hour}/小时)"
        return True, ""

    def record_url_sent(self):
        self._url_timestamps.append(time.time())

    def set_daily_cap(self, cap: int):
        self.daily_message_cap = cap

    def stats(self) -> dict:
        return {
            "user_counts": {uid: len(ts) for uid, ts in self._user_timestamps.items()},
            "group_counts": {gid: len(ts) for gid, ts in self._group_timestamps.items()},
            "global_count": len(self._global_timestamps),
            "daily_count": self._daily_count,
            "daily_cap": self.daily_message_cap,
            "url_count": len(self._url_timestamps),
        }

    @staticmethod
    def _clean_old(timestamps: deque, now: float, window: int) -> None:
        """移除窗口外的时间戳 — O(1) 左侧弹出。"""
        cutoff = now - window
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

    def _maybe_reset_daily(self, now: float):
        if now - self._daily_reset_time > 86400:
            self._daily_count = 0
            self._daily_reset_time = now
