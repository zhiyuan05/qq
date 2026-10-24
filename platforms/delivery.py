"""Outbound rate reservation and rollback for QQ private delivery."""
from __future__ import annotations

import time
from collections import deque

from utils.config import DeliveryConfig, VoiceConfig


class OutboundGate:
    """Per-principal sliding windows for text and voice sends.

    Quotas are reserved before a send. A pre-send failure may roll the
    reservation back; an ambiguous timeout counts as consumed.
    """

    def __init__(self, config: DeliveryConfig, voice: VoiceConfig | None = None):
        self.config = config
        self.voice = voice or VoiceConfig()
        self._windows: dict[str, deque[float]] = {}

    @staticmethod
    def _key(principal_key: str, voice: bool) -> str:
        return f"{'voice' if voice else 'text'}:{principal_key}"

    def reserve(self, principal_key: str, *, voice: bool = False) -> bool:
        key = self._key(principal_key, voice)
        now = time.monotonic()
        window = self._windows.setdefault(key, deque())
        while window and now - window[0] > 3600:
            window.popleft()
        per_minute = self.voice.per_minute if voice else self.config.outbound_per_minute
        per_hour = self.voice.per_hour if voice else self.config.outbound_per_hour
        if sum(1 for stamp in window if now - stamp <= 60) >= per_minute:
            return False
        if len(window) >= per_hour:
            return False
        window.append(now)
        return True

    def rollback(self, principal_key: str, *, voice: bool = False) -> None:
        window = self._windows.get(self._key(principal_key, voice))
        if window:
            window.pop()

    def snapshot(self) -> dict[str, int]:
        now = time.monotonic()
        return {
            key: sum(1 for stamp in window if now - stamp <= 60)
            for key, window in self._windows.items()
        }
