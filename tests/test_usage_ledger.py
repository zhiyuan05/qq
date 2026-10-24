from datetime import datetime

import pytest

from database.db import dispose_db, init_db
from llm.usage import UsageBuckets, UsageLedger, normalize_usage
from utils.config import UsageConfig


def test_normalize_deepseek_usage_buckets():
    buckets = normalize_usage(
        {
            "prompt_tokens": 100,
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 60,
            "completion_tokens": 25,
        }
    )

    assert buckets == UsageBuckets(cache_miss=60, cache_hit=40, cache_write=0, output=25)


def test_normalize_openai_cached_tokens():
    buckets = normalize_usage(
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 30}, "completion_tokens": 10}
    )

    assert buckets.cache_hit == 30
    assert buckets.cache_miss == 70
    assert buckets.output == 10


@pytest.mark.asyncio
async def test_record_is_idempotent_and_aggregates(tmp_path):
    db_path = str(tmp_path / "usage.db")
    await init_db(db_path)
    ledger = UsageLedger(
        db_path,
        UsageConfig(
            price_cache_miss_per_million=10.0,
            price_cache_hit_per_million=1.0,
            price_output_per_million=20.0,
        ),
    )
    buckets = UsageBuckets(cache_miss=1000, cache_hit=500, output=200)
    try:
        assert await ledger.record(
            request_id="m1",
            session_id="qq:1",
            user_id="1",
            model="deepseek-chat",
            buckets=buckets,
            response_hash="abc",
        )
        assert not await ledger.record(
            request_id="m1",
            session_id="qq:1",
            user_id="1",
            model="deepseek-chat",
            buckets=buckets,
            response_hash="abc",
        )

        totals = await ledger.aggregate(session_id="qq:1", days=1)
        assert totals["cache_miss_tokens"] == 1000
        assert totals["cache_hit_tokens"] == 500
        assert totals["output_tokens"] == 200
        assert totals["turns"] == 1
        assert totals["cost_micros"] > 0
    finally:
        await dispose_db(db_path)


def test_peak_multiplier_applies_inside_window():
    ledger = UsageLedger(
        "data/chatbot.db",
        UsageConfig(
            peak_windows="09:00-12:00",
            peak_multiplier=2.0,
            offpeak_multiplier=1.0,
            price_output_per_million=1.0,
        ),
    )
    buckets = UsageBuckets(output=1_000_000)

    peak = ledger.cost_micros(buckets, datetime(2026, 10, 23, 10, 0))
    offpeak = ledger.cost_micros(buckets, datetime(2026, 10, 23, 20, 0))

    assert peak == 2_000_000
    assert offpeak == 1_000_000


@pytest.mark.asyncio
async def test_daily_budget_blocks_after_spend(tmp_path):
    db_path = str(tmp_path / "budget.db")
    await init_db(db_path)
    ledger = UsageLedger(
        db_path,
        UsageConfig(daily_budget_micros=1_000_000, price_output_per_million=1.0),
    )
    try:
        assert await ledger.over_budget() is False
        await ledger.record(
            request_id="m1",
            session_id="qq:1",
            user_id="1",
            model="deepseek-chat",
            buckets=UsageBuckets(output=1_000_000),
        )
        assert await ledger.over_budget() is True
        budget = ledger.budget_snapshot(await ledger.daily_spend_micros())
        assert budget["daily_budget_micros"] == 1_000_000
        assert budget["remaining_micros"] == 0
    finally:
        await dispose_db(db_path)
