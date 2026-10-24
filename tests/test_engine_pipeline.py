"""Engine 与 Pipeline 集成测试"""
import pytest
from unittest.mock import patch

from core.engine import Engine
from utils.config import AppConfig
from platforms.models import UnifiedMessage, Platform


@pytest.fixture
def engine():
    cfg = AppConfig()
    cfg.llm.api_key = "test-key"
    cfg.memory.entity.auto_extract = False
    return Engine(cfg)


@pytest.mark.asyncio
async def test_pipeline_initialized(engine):
    """Engine 创建时应初始化 Pipeline"""
    assert hasattr(engine, "pipeline")
    assert len(engine.pipeline._middleware) > 0


@pytest.mark.asyncio
async def test_pipeline_has_rate_limit(engine):
    """Pipeline 应包含 RateLimitMiddleware"""
    from core.middleware.rate_limit import RateLimitMiddleware
    has = any(isinstance(mw, RateLimitMiddleware) for mw in engine.pipeline._middleware)
    assert has


@pytest.mark.asyncio
async def test_pipeline_has_split(engine):
    """Pipeline 应包含 SplitMiddleware"""
    from core.middleware.split import SplitMiddleware
    has = any(isinstance(mw, SplitMiddleware) for mw in engine.pipeline._middleware)
    assert has


@pytest.mark.asyncio
async def test_pipeline_empty_filter(engine):
    """空消息通过 process_message 应返回 None"""
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="   ")
    result = await engine.process_message(msg)
    assert result is None


@pytest.mark.asyncio
async def test_engine_uses_pipeline_flow(engine):
    """Engine.process_message 应经过 Pipeline 流程"""
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="u1", content="/help")
    result = await engine.process_message(msg)
    # 正常处理：指令应返回帮助文本，说明 pipeline 流程已执行
    assert result is not None
    assert "可用指令" in result or "/help" in result
