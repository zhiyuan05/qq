"""Web 消息幂等协调，供断线重连安全复用 request_id。"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass


class IdempotencyCapacityError(RuntimeError):
    pass


class IdempotencyConflictError(RuntimeError):
    """A request id was reused for a different logical operation."""


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    owner: bool
    future: asyncio.Future[dict]
    owner_token: str | None = None


@dataclass
class _Entry:
    future: asyncio.Future[dict]
    owner_token: str
    fingerprint: str
    updated_at: float


class IdempotencyRegistry:
    def __init__(
        self,
        ttl_seconds: float = 300,
        max_entries: int = 2048,
        inflight_timeout_seconds: float = 120,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.inflight_timeout_seconds = inflight_timeout_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if (
                entry.future.done()
                and now - entry.updated_at >= self.ttl_seconds
                or not entry.future.done()
                and now - entry.updated_at >= self.inflight_timeout_seconds
            )
        ]
        for key in expired:
            entry = self._entries.pop(key, None)
            if entry is not None and not entry.future.done():
                entry.future.set_result({"error": "请求租约已过期", "retryable": True})

        while len(self._entries) >= self.max_entries:
            completed_key = next(
                (key for key, entry in self._entries.items() if entry.future.done()),
                None,
            )
            if completed_key is None:
                break
            self._entries.pop(completed_key, None)

    async def claim(self, key: str, fingerprint: str) -> IdempotencyClaim:
        """Claim one generation; only its opaque owner token may mutate it."""

        async with self._lock:
            now = time.monotonic()
            existing = self._entries.get(key)
            if existing is not None:
                active = (
                    not existing.future.done()
                    and now - existing.updated_at < self.inflight_timeout_seconds
                )
                replayable = existing.future.done() and now - existing.updated_at < self.ttl_seconds
                if active or replayable:
                    if not secrets.compare_digest(existing.fingerprint, fingerprint):
                        raise IdempotencyConflictError("request_id was reused with a different payload")
                    self._entries.move_to_end(key)
                    return IdempotencyClaim(owner=False, future=existing.future)
                self._entries.pop(key, None)
                if not existing.future.done():
                    existing.future.set_result({"error": "请求租约已过期", "retryable": True})
            self._prune(now)
            if len(self._entries) >= self.max_entries:
                raise IdempotencyCapacityError("幂等请求容量已满")

            future = asyncio.get_running_loop().create_future()
            owner_token = secrets.token_urlsafe(24)
            self._entries[key] = _Entry(
                future=future,
                owner_token=owner_token,
                fingerprint=fingerprint,
                updated_at=now,
            )
            return IdempotencyClaim(owner=True, future=future, owner_token=owner_token)

    async def complete(self, key: str, owner_token: str, result: dict) -> bool:
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None or not secrets.compare_digest(entry.owner_token, owner_token):
                return False
            if not entry.future.done():
                entry.future.set_result(dict(result))
            entry.updated_at = time.monotonic()
            self._entries.move_to_end(key)
            return True

    async def abandon(self, key: str, owner_token: str, result: dict) -> bool:
        """放弃所有权并唤醒等待者；之后相同 ID 可以重新成为首个处理者。"""

        async with self._lock:
            entry = self._entries.get(key)
            if entry is None or not secrets.compare_digest(entry.owner_token, owner_token):
                return False
            self._entries.pop(key, None)
            if not entry.future.done():
                entry.future.set_result(dict(result))
            return True

    async def stats(self) -> dict:
        async with self._lock:
            return {
                "entries": len(self._entries),
                "in_flight": sum(not entry.future.done() for entry in self._entries.values()),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "inflight_timeout_seconds": self.inflight_timeout_seconds,
            }


request_registry = IdempotencyRegistry()


def request_fingerprint(message: str, card: str = "", style: str = "") -> str:
    """Hash request semantics without retaining another plaintext message copy."""

    payload = "\x1f".join((message, card, style)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def web_user_id(client_id: str, remote_host: str) -> str:
    """稳定 client_id 优先；旧客户端使用来源地址隔离默认会话。"""

    identity = client_id if client_id != "default" else f"{remote_host}_default"
    return f"web_{identity}"
