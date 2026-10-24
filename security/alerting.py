from __future__ import annotations

"""Alert manager with level-based filtering and callback support."""
import time
import asyncio
from enum import Enum
from typing import Callable, Awaitable, Optional
from utils.logger import setup_logger

logger = setup_logger("alerting")


class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertManager:
    def __init__(self, max_history: int = 1000):
        self._alerts: list[dict] = []
        self._max_history = max_history
        self._callbacks: list[Callable[[dict], Awaitable[None]]] = []

    def on_alert(self, callback: Callable[[dict], Awaitable[None]]):
        self._callbacks.append(callback)

    def create_alert(self, level: AlertLevel, title: str, message: str, metadata: Optional[dict] = None):
        alert = {
            "level": level.value,
            "title": title,
            "message": message,
            "metadata": metadata or {},
            "timestamp": time.time(),
        }

        self._alerts.append(alert)
        if len(self._alerts) > self._max_history:
            self._alerts = self._alerts[-self._max_history:]

        log_msg = f"[{level.value}] {title}: {message}"
        if level == AlertLevel.CRITICAL:
            logger.error(log_msg)
        elif level == AlertLevel.WARNING:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        for cb in self._callbacks:
            try:
                loop = asyncio.get_running_loop()
                asyncio.run_coroutine_threadsafe(cb(alert), loop)
            except RuntimeError:
                # 没有运行中的循环
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(cb(alert))
                    loop.close()
                except Exception as e2:
                    logger.error(f"告警回调失败（无循环）: {e2}")
            except Exception as e:
                logger.error(f"告警回调失败: {e}")

    def get_recent(self, limit: int = 50, level: Optional[AlertLevel] = None) -> list[dict]:
        alerts = self._alerts
        if level:
            alerts = [a for a in alerts if a["level"] == level.value]
        return alerts[-limit:]

    def stats(self) -> dict:
        by_level = {}
        for a in self._alerts:
            lvl = a["level"]
            by_level[lvl] = by_level.get(lvl, 0) + 1
        return {
            "total": len(self._alerts),
            "by_level": by_level,
        }
