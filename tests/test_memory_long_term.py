"""长时记忆测试"""
import pytest

from memory.forgetting import MemoryForgetting
from memory.long_term import LongTermMemory


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


def test_cleanup_archives_only_expired_memories():
    class FakeCollection:
        deleted = []

        def get(self, include=None):
            return {
                "ids": ["old", "new", "legacy"],
                "metadatas": [
                    {"last_access": 1, "access_count": 0},
                    {"last_access": 10**20, "access_count": 0},
                    {},
                ],
            }

        def delete(self, ids):
            self.deleted.extend(ids)

    memory = LongTermMemory.__new__(LongTermMemory)
    memory._collection = FakeCollection()
    memory._doc_counter = 3

    report = memory.cleanup(MemoryForgetting(archive_after_days=1))

    assert report["archive_ids"] == ["old"]
    assert memory._collection.deleted == ["old"]
    assert memory._doc_counter == 2
