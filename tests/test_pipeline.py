"""Pipeline and middleware tests."""
import pytest
import asyncio
import time
from datetime import datetime
from core.pipeline import Pipeline, Middleware, PipelineContext
from core.middleware.active_window import ActiveWindowMiddleware
from core.middleware.rate_limit import RateLimitMiddleware
from core.middleware.filter import FilterMiddleware
from core.middleware.safety import SafetyMiddleware
from core.middleware.content_filter import ContentFilterMiddleware
from core.middleware.split import SplitMiddleware
from core.middleware.delay import DelayMiddleware
from security.rate_limiter import RateLimiter
from security.circuit_breaker import CircuitBreaker
from security.content_filter import ContentFilter


# === Pipeline Core ===

class MockMiddleware(Middleware):
    def __init__(self, name: str, modify_response: str = None, block: bool = False):
        self.name = name
        self.modify_response = modify_response
        self.block = block
        self.called = False

    async def on_receive(self, ctx: PipelineContext) -> bool:
        self.called = True
        if self.block:
            return False
        ctx.metadata[f"{self.name}_receive"] = True
        return True

    async def on_llm(self, ctx: PipelineContext) -> bool:
        self.called = True
        if self.block:
            return False
        return True

    async def on_response(self, ctx: PipelineContext) -> bool:
        self.called = True
        if self.modify_response:
            ctx.response = self.modify_response
        return True

    async def on_send(self, ctx: PipelineContext) -> bool:
        self.called = True
        return True


@pytest.mark.asyncio
async def test_pipeline_runs_all_middleware():
    m1 = MockMiddleware("m1")
    m2 = MockMiddleware("m2")
    pipeline = Pipeline(middleware=[m1, m2])

    ctx = PipelineContext(user_id="user1", content="hello")
    await pipeline.process(ctx)

    assert m1.called is True
    assert m2.called is True


@pytest.mark.asyncio
async def test_pipeline_blocks_on_receive():
    blocker = MockMiddleware("blocker", block=True)
    after = MockMiddleware("after")
    pipeline = Pipeline(middleware=[blocker, after])

    ctx = PipelineContext(user_id="user1", content="hello")
    result = await pipeline.process(ctx)

    assert result is None
    assert blocker.called is True
    assert after.called is False


@pytest.mark.asyncio
async def test_pipeline_modifies_response():
    modifier = MockMiddleware("modifier", modify_response="modified")
    pipeline = Pipeline(middleware=[modifier])

    ctx = PipelineContext(user_id="user1", content="hello")
    ctx.response = "original"
    await pipeline.process(ctx)

    assert ctx.response == "modified"


@pytest.mark.asyncio
async def test_pipeline_stores_metadata():
    m1 = MockMiddleware("m1")
    pipeline = Pipeline(middleware=[m1])

    ctx = PipelineContext(user_id="user1", content="hello")
    await pipeline.process(ctx)

    assert ctx.metadata.get("m1_receive") is True


# === Active Window ===

@pytest.fixture
def active_mw():
    return ActiveWindowMiddleware(start_hour=8, end_hour=23)


@pytest.mark.asyncio
async def test_active_within_hours(active_mw):
    ctx = PipelineContext(user_id="user1", content="hello", timestamp=datetime(2026, 6, 20, 14, 0))
    assert await active_mw.on_receive(ctx) is True


@pytest.mark.asyncio
async def test_active_outside_hours(active_mw):
    ctx = PipelineContext(user_id="user1", content="hello", timestamp=datetime(2026, 6, 20, 3, 0))
    assert await active_mw.on_receive(ctx) is False


@pytest.mark.asyncio
async def test_active_owner_bypass(active_mw):
    ctx = PipelineContext(
        user_id="owner1", content="hello",
        timestamp=datetime(2026, 6, 20, 3, 0), is_owner=True,
    )
    assert await active_mw.on_receive(ctx) is True


# === Rate Limit Middleware ===

@pytest.mark.asyncio
async def test_rate_limit_allows_normal():
    limiter = RateLimiter(per_user_per_minute=10, global_per_hour=100)
    mw = RateLimitMiddleware(limiter)
    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_receive(ctx) is True


@pytest.mark.asyncio
async def test_rate_limit_blocks_excess():
    limiter = RateLimiter(per_user_per_minute=2, global_per_hour=100)
    mw = RateLimitMiddleware(limiter)
    for _ in range(2):
        ctx = PipelineContext(user_id="user1", content="hello")
        await mw.on_receive(ctx)
    ctx = PipelineContext(user_id="user1", content="hello")
    result = await mw.on_receive(ctx)
    assert result is False
    assert ctx.blocked is True


# === Filter Middleware ===

@pytest.mark.asyncio
async def test_filter_blocks_empty():
    mw = FilterMiddleware()
    ctx = PipelineContext(user_id="user1", content="   ")
    assert await mw.on_receive(ctx) is False


@pytest.mark.asyncio
async def test_filter_allows_valid():
    mw = FilterMiddleware()
    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_receive(ctx) is True


# === Safety Middleware ===

@pytest.mark.asyncio
async def test_safety_blocks_on_open_circuit():
    breaker = CircuitBreaker()
    breaker.force_open()
    mw = SafetyMiddleware(breaker)
    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_send(ctx) is False
    assert ctx.blocked is True


@pytest.mark.asyncio
async def test_safety_allows_on_closed_circuit():
    breaker = CircuitBreaker()
    mw = SafetyMiddleware(breaker)
    ctx = PipelineContext(user_id="user1", content="hello")
    assert await mw.on_send(ctx) is True


# === Content Filter Middleware ===

@pytest.mark.asyncio
async def test_content_filter_middleware(tmp_path):
    words_file = tmp_path / "sw.txt"
    words_file.write_text("赌博\n", encoding="utf-8")
    cf = ContentFilter(str(words_file))
    mw = ContentFilterMiddleware(cf)

    ctx = PipelineContext(user_id="user1", content="hello", response="关于赌博的故事")
    await mw.on_response(ctx)
    assert "赌博" not in ctx.response
    assert "***" in ctx.response


# === Split Middleware ===

@pytest.mark.asyncio
async def test_split_middleware_splits_long():
    """SplitMiddleware 仅记录日志不截断（平台层实际拆分）"""
    mw = SplitMiddleware(max_length=50)
    long_text = "这是第一句话。" * 10 + "这是第二句话。" * 10
    ctx = PipelineContext(user_id="user1", content="hello", response=long_text)
    await mw.on_response(ctx)
    # 不截断，保持完整响应 — 平台层通过 send_long_message 拆分
    assert ctx.response == long_text


@pytest.mark.asyncio
async def test_split_middleware_keeps_short():
    mw = SplitMiddleware(max_length=2000)
    ctx = PipelineContext(user_id="user1", content="hello", response="short")
    await mw.on_response(ctx)
    assert ctx.response == "short"


# === Delay Middleware ===

@pytest.mark.asyncio
async def test_delay_middleware():
    mw = DelayMiddleware(min_seconds=0.01, max_seconds=0.02)
    ctx = PipelineContext(user_id="user1", content="hello", response="response")
    start = time.time()
    await mw.on_send(ctx)
    elapsed = time.time() - start
    assert elapsed >= 0.01
