"""长时记忆测试"""
import pytest


@pytest.mark.asyncio
async def test_long_term_init():
    from memory.long_term import LongTermMemory
    mem = LongTermMemory(top_k=5)
    assert mem is not None
    assert mem.top_k == 5


@pytest.mark.asyncio
async def test_long_term_search_no_chroma():
    from memory.long_term import LongTermMemory
    mem = LongTermMemory(top_k=5)
    if not mem._collection:
        result = mem.search("test")
        assert result == []


@pytest.mark.asyncio
async def test_long_term_store_no_chroma():
    from memory.long_term import LongTermMemory
    mem = LongTermMemory(top_k=5)
    if not mem._collection:
        mem.store("test", {"user_id": "u1"})
    # 不应抛出异常
