from __future__ import annotations

"""Circuit breaker: CLOSED -> OPEN -> HALF_OPEN with configurable thresholds."""
import time
from enum import Enum
from utils.logger import setup_logger

logger = setup_logger("circuit_breaker")


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: float = 0.3,
        window_seconds: int = 300,
        recovery_seconds: int = 600,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_seconds = recovery_seconds

        self.state = CircuitState.CLOSED
        self._total_requests: int = 0
        self._total_failures: int = 0
        self._window_requests: int = 0
        self._window_failures: int = 0
        self._window_start: float = time.time()
        self._last_failure_time: float = 0
        self._state_changes: list[tuple[str, float]] = [("CLOSED", time.time())]

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_seconds:
                self._transition(CircuitState.HALF_OPEN)
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            return True

        return False

    def record_success(self):
        self._total_requests += 1
        self._window_requests += 1
        self._maybe_reset_window()

        if self.state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.CLOSED)
            self._window_requests = 0
            self._window_failures = 0

    def record_failure(self):
        self._total_requests += 1
        self._total_failures += 1
        self._window_requests += 1
        self._window_failures += 1
        self._last_failure_time = time.time()
        self._maybe_reset_window()

        if self.state == CircuitState.HALF_OPEN:
            self._transition(CircuitState.OPEN)
            return

        if self.state == CircuitState.CLOSED and self._window_requests > 0:
            failure_rate = self._window_failures / self._window_requests
            if failure_rate >= self.failure_threshold and self._window_requests >= 10:
                logger.warning(f"熔断器触发: 失败率 {failure_rate:.1%} >= {self.failure_threshold:.1%}")
                self._transition(CircuitState.OPEN)

    def force_open(self):
        self._last_failure_time = time.time()
        self._transition(CircuitState.OPEN)

    def force_close(self):
        self._transition(CircuitState.CLOSED)
        self._window_requests = 0
        self._window_failures = 0

    def stats(self) -> dict:
        return {
            "state": self.state.value,
            "total_requests": self._total_requests,
            "total_failures": self._total_failures,
            "window_requests": self._window_requests,
            "window_failures": self._window_failures,
            "failure_rate": self._window_failures / max(self._window_requests, 1),
            "last_failure": self._last_failure_time,
            "recent_state_changes": self._state_changes[-5:],
        }

    def _transition(self, new_state: CircuitState):
        old = self.state
        self.state = new_state
        self._state_changes.append((new_state.value, time.time()))
        if old != new_state:
            logger.info(f"熔断器状态变更: {old.value} -> {new_state.value}")

    def _maybe_reset_window(self):
        now = time.time()
        if now - self._window_start >= self.window_seconds:
            self._window_requests = 0
            self._window_failures = 0
            self._window_start = now

    def _force_state(self, state: CircuitState):
        self.state = state
