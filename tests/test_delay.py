"""延迟工具测试"""
import pytest
from utils.delay import HumanDelay


def test_delay_default_values():
    d = HumanDelay()
    assert d.min_seconds == 0.5
    assert d.max_seconds == 3.0
    assert d.chars_per_second == 15.0
    assert d.enabled is True


@pytest.mark.asyncio
async def test_delay_disabled():
    d = HumanDelay(enabled=False, min_seconds=1.0, max_seconds=2.0)
    import time as t
    start = t.time()
    await d.wait(100)
    elapsed = t.time() - start
    assert elapsed < 0.5


@pytest.mark.asyncio
async def test_delay_enabled():
    d = HumanDelay(min_seconds=0.01, max_seconds=0.02, chars_per_second=9999)
    import time as t
    start = t.time()
    await d.wait(10)
    elapsed = t.time() - start
    assert 0.005 <= elapsed <= 2.0
