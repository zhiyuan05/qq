"""Composition root for platform adapters."""
from __future__ import annotations

import asyncio
from collections.abc import Iterable

from platforms.base import BasePlatform, MessageCallback
from utils.logger import setup_logger

logger = setup_logger("platform-manager")


class PlatformManager:
    """Own adapter registration and failure-isolated lifecycle orchestration."""

    def __init__(self, callback: MessageCallback):
        self._callback = callback
        self._platforms: dict[str, BasePlatform] = {}

    @property
    def platforms(self) -> tuple[BasePlatform, ...]:
        return tuple(self._platforms.values())

    @property
    def running_platforms(self) -> tuple[BasePlatform, ...]:
        return tuple(platform for platform in self._platforms.values() if platform.is_running)

    def add(self, platform: BasePlatform) -> BasePlatform:
        if platform.name in self._platforms:
            raise ValueError(f"platform already registered: {platform.name}")
        platform.on_message(self._callback)
        self._platforms[platform.name] = platform
        return platform

    def get(self, name: str) -> BasePlatform | None:
        return self._platforms.get(name)

    async def start_all(self, platforms: Iterable[BasePlatform] | None = None) -> dict[str, BaseException | None]:
        selected = tuple(platforms or self._platforms.values())

        async def start_one(platform: BasePlatform) -> tuple[str, BaseException | None]:
            try:
                await platform.start()
            except Exception as exc:
                logger.error("Platform %s failed to start (%s)", platform.name, type(exc).__name__)
                return platform.name, exc
            return platform.name, None

        results = await asyncio.gather(*(start_one(platform) for platform in selected))
        return dict(results)

    async def quiesce_all(self) -> dict[str, BaseException | None]:
        """Quiesce every ingress concurrently before any adapter begins draining."""

        selected = tuple(reversed(self.platforms))

        async def quiesce_one(platform: BasePlatform) -> tuple[str, BaseException | None]:
            try:
                await platform.quiesce()
            except Exception as exc:
                logger.error("Platform %s failed to quiesce (%s)", platform.name, type(exc).__name__)
                return platform.name, exc
            return platform.name, None

        results = await asyncio.gather(*(quiesce_one(platform) for platform in selected))
        return dict(results)

    async def stop_all(self) -> dict[str, BaseException | None]:
        await self.quiesce_all()
        selected = tuple(reversed(self.platforms))

        async def stop_one(platform: BasePlatform) -> tuple[str, BaseException | None]:
            try:
                await platform.stop()
            except Exception as exc:
                logger.error("Platform %s failed to stop (%s)", platform.name, type(exc).__name__)
                return platform.name, exc
            return platform.name, None

        results = await asyncio.gather(*(stop_one(platform) for platform in selected))
        return dict(results)

    def status_snapshots(self) -> list[dict]:
        return [platform.status_snapshot() for platform in self._platforms.values()]
