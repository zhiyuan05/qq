"""无外部依赖的轻量运行指标聚合器。"""
from __future__ import annotations

import threading
import time
from collections import Counter


class RuntimeMetrics:
    def __init__(self):
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._requests_total = 0
        self._requests_in_flight = 0
        self._request_errors = 0
        self._request_latency_seconds = 0.0
        self._status_codes: Counter[str] = Counter()
        self._websocket_connections = 0
        self._websocket_messages = 0
        self._domain_counters: Counter[str] = Counter()

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._domain_counters[name] += value

    def request_started(self):
        with self._lock:
            self._requests_total += 1
            self._requests_in_flight += 1

    def request_finished(self, status_code: int, elapsed_seconds: float):
        with self._lock:
            self._requests_in_flight = max(0, self._requests_in_flight - 1)
            self._request_latency_seconds += elapsed_seconds
            self._status_codes[str(status_code)] += 1
            if status_code >= 500:
                self._request_errors += 1

    def websocket_connected(self):
        with self._lock:
            self._websocket_connections += 1

    def websocket_disconnected(self):
        with self._lock:
            self._websocket_connections = max(0, self._websocket_connections - 1)

    def websocket_message(self):
        with self._lock:
            self._websocket_messages += 1

    def snapshot(self) -> dict:
        with self._lock:
            completed = sum(self._status_codes.values())
            average_latency_ms = (
                self._request_latency_seconds / completed * 1000 if completed else 0.0
            )
            return {
                "uptime_seconds": int(time.time() - self.started_at),
                "http": {
                    "requests_total": self._requests_total,
                    "requests_in_flight": self._requests_in_flight,
                    "server_errors": self._request_errors,
                    "average_latency_ms": round(average_latency_ms, 3),
                    "status_codes": dict(self._status_codes),
                },
                "websocket": {
                    "active_connections": self._websocket_connections,
                    "messages_total": self._websocket_messages,
                },
                "domain": dict(self._domain_counters),
            }
