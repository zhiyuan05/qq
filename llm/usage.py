"""Idempotent per-turn token usage ledger."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database.db import get_session_factory, run_migrations
from database.models import LLMUsageEvent
from utils.config import UsageConfig
from utils.logger import setup_logger

logger = setup_logger("usage")


@dataclass(frozen=True)
class UsageBuckets:
    cache_miss: int = 0
    cache_hit: int = 0
    cache_write: int = 0
    output: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def normalize_usage(usage: Any) -> UsageBuckets:
    """Map OpenAI/DeepSeek usage fields to disjoint buckets."""

    if usage is None:
        return UsageBuckets()
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return UsageBuckets()
    details = usage.get("prompt_tokens_details") or {}
    cache_hit = int(
        usage.get("prompt_cache_hit_tokens")
        or details.get("cached_tokens")
        or usage.get("cache_hit_tokens")
        or 0
    )
    cache_miss = int(
        usage.get("prompt_cache_miss_tokens")
        or usage.get("cache_miss_tokens")
        or max(0, int(usage.get("prompt_tokens", 0) or 0) - cache_hit)
    )
    return UsageBuckets(
        cache_miss=cache_miss,
        cache_hit=cache_hit,
        cache_write=int(usage.get("cache_write_tokens", 0) or 0),
        output=int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0),
    )


def _parse_windows(spec: str) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" not in part:
            continue
        start, end = part.split("-", 1)
        try:
            start_h, start_m = (int(value) for value in start.split(":", 1))
            end_h, end_m = (int(value) for value in end.split(":", 1))
        except (TypeError, ValueError):
            continue
        windows.append((start_h * 60 + start_m, end_h * 60 + end_m))
    return windows


class UsageLedger:
    def __init__(
        self,
        database: str,
        config: UsageConfig,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
    ):
        self.config = config
        self._database = database
        self._session_factory = get_session_factory(
            database,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()
        self._windows = _parse_windows(config.peak_windows)
        self._spend_cache: tuple[str, int] | None = None
        self._warned_budget_day: str | None = None

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if not self._schema_ready:
                await run_migrations(self._database)
                self._schema_ready = True

    def is_peak(self, at: datetime) -> bool:
        if self.config.peak_weekdays_only and at.weekday() >= 5:
            return False
        minute = at.hour * 60 + at.minute
        return any(start <= minute < end for start, end in self._windows)

    def cost_micros(self, buckets: UsageBuckets, at: datetime | None = None) -> int:
        at = at or datetime.now()
        multiplier = (
            self.config.peak_multiplier if self.is_peak(at) else self.config.offpeak_multiplier
        )
        cost = (
            buckets.cache_miss * self.config.price_cache_miss_per_million
            + buckets.cache_hit * self.config.price_cache_hit_per_million
            + buckets.cache_write * self.config.price_cache_write_per_million
            + buckets.output * self.config.price_output_per_million
        ) / 1_000_000
        return int(round(cost * multiplier * 1_000_000))

    @staticmethod
    def event_key(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    async def record(
        self,
        *,
        request_id: str,
        session_id: str,
        user_id: str,
        model: str,
        buckets: UsageBuckets,
        response_hash: str = "",
    ) -> bool:
        if not self.config.enabled or not any(buckets.as_dict().values()):
            return False
        await self._ensure_schema()
        key = self.event_key(request_id, model, response_hash, *[str(value) for value in buckets.as_dict().values()])
        occurred_at = datetime.now()
        values = {
            "event_key": key,
            "request_id": request_id[:255],
            "session_id": session_id[:200],
            "user_id": user_id[:200],
            "model": model[:200],
            "cache_miss_tokens": buckets.cache_miss,
            "cache_hit_tokens": buckets.cache_hit,
            "cache_write_tokens": buckets.cache_write,
            "output_tokens": buckets.output,
            "cost_micros": self.cost_micros(buckets, occurred_at),
            "currency": self.config.currency,
            "occurred_at": occurred_at,
        }
        async with self._session_factory() as database_session:
            async with database_session.begin():
                dialect = database_session.bind.dialect.name
                if dialect == "sqlite":
                    statement = sqlite_insert(LLMUsageEvent).values(**values)
                elif dialect == "postgresql":
                    statement = postgresql_insert(LLMUsageEvent).values(**values)
                else:
                    raise RuntimeError(f"unsupported usage dialect: {dialect}")
                result = await database_session.execute(statement.on_conflict_do_nothing())
        if result.rowcount:
            today = occurred_at.date().isoformat()
            if self._spend_cache is not None and self._spend_cache[0] == today:
                spent = self._spend_cache[1] + values["cost_micros"]
                self._spend_cache = (today, spent)
                limit = self.config.daily_budget_micros
                if (
                    limit > 0
                    and spent >= limit * 0.8
                    and self._warned_budget_day != today
                ):
                    self._warned_budget_day = today
                    logger.warning(
                        "每日 token 预算已使用 %d%%，达到上限后将拒绝新调用",
                        int(spent / limit * 100),
                    )
            else:
                self._spend_cache = None
        return bool(result.rowcount)

    async def daily_spend_micros(self) -> int:
        now = datetime.now()
        today = now.date().isoformat()
        if self._spend_cache is not None and self._spend_cache[0] == today:
            return self._spend_cache[1]
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        totals = await self.aggregate(days=1, since=start_of_day)
        spent = int(totals["cost_micros"])
        self._spend_cache = (today, spent)
        return spent

    async def over_budget(self) -> bool:
        if self.config.daily_budget_micros <= 0:
            return False
        return await self.daily_spend_micros() >= self.config.daily_budget_micros

    def budget_snapshot(self, spent_micros: int) -> dict[str, int]:
        limit = self.config.daily_budget_micros
        return {
            "daily_budget_micros": limit,
            "daily_spent_micros": spent_micros,
            "remaining_micros": max(0, limit - spent_micros) if limit else 0,
        }

    async def prune(self, days: int = 365) -> int:
        await self._ensure_schema()
        cutoff = datetime.now() - timedelta(days=max(1, days))
        async with self._session_factory() as database_session:
            async with database_session.begin():
                result = await database_session.execute(
                    LLMUsageEvent.__table__.delete().where(LLMUsageEvent.occurred_at < cutoff)
                )
        return int(result.rowcount or 0)

    async def aggregate(
        self,
        *,
        session_id: str = "",
        days: int = 1,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        await self._ensure_schema()
        since = since or datetime.now() - timedelta(days=max(1, days))
        async with self._session_factory() as database_session:
            conditions = [LLMUsageEvent.occurred_at >= since]
            if session_id:
                conditions.append(LLMUsageEvent.session_id == session_id)
            row = (
                await database_session.execute(
                    select(
                        func.coalesce(func.sum(LLMUsageEvent.cache_miss_tokens), 0),
                        func.coalesce(func.sum(LLMUsageEvent.cache_hit_tokens), 0),
                        func.coalesce(func.sum(LLMUsageEvent.cache_write_tokens), 0),
                        func.coalesce(func.sum(LLMUsageEvent.output_tokens), 0),
                        func.coalesce(func.sum(LLMUsageEvent.cost_micros), 0),
                        func.count(),
                    ).where(*conditions)
                )
            ).one()
        return {
            "cache_miss_tokens": int(row[0]),
            "cache_hit_tokens": int(row[1]),
            "cache_write_tokens": int(row[2]),
            "output_tokens": int(row[3]),
            "cost_micros": int(row[4]),
            "currency": self.config.currency,
            "turns": int(row[5]),
            "days": days,
        }
