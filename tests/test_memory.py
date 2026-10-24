"""记忆系统测试"""
import pytest

from memory.entity_memory import EntityMemory


@pytest.fixture
async def mem(tmp_path):
    db = str(tmp_path / "test.db")
    m = EntityMemory(db_path=db)
    yield m


@pytest.mark.asyncio
async def test_entity_memory_store_and_query(tmp_path):
    mem = EntityMemory(db_path=str(tmp_path / "test.db"))
    await mem.store("user1", "preference", "喜欢的语言", "Python")
    await mem.store("user1", "fact", "职业", "程序员")
    await mem.store("user2", "preference", "喜欢的颜色", "蓝色")

    results = await mem.query("user1")
    assert len(results) == 2

    prefs = await mem.query("user1", "preference")
    assert len(prefs) == 1
    assert prefs[0].value == "Python"


@pytest.mark.asyncio
async def test_entity_memory_update(tmp_path):
    mem = EntityMemory(db_path=str(tmp_path / "test.db"))
    await mem.store("user1", "preference", "喜欢的语言", "Python")
    await mem.store("user1", "preference", "喜欢的语言", "Go")
    results = await mem.query("user1", "preference")
    assert len(results) == 1
    assert results[0].value == "Go"


@pytest.mark.asyncio
async def test_entity_memory_forget(tmp_path):
    mem = EntityMemory(db_path=str(tmp_path / "test.db"))
    await mem.store("user1", "fact", "职业", "程序员")
    assert await mem.forget("user1", "职业") is True
    assert len(await mem.query("user1")) == 0


@pytest.mark.asyncio
async def test_entity_memory_profile(tmp_path):
    mem = EntityMemory(db_path=str(tmp_path / "test.db"))
    await mem.store("user1", "preference", "语言", "Python")
    await mem.store("user1", "fact", "职业", "程序员")
    profile = await mem.get_profile_text("user1")
    assert "Python" in profile
    assert "程序员" in profile


def test_short_term_memory():
    """SessionManager 管理短期会话，ShortTermMemory 已废弃合并至 SessionManager"""
    from core.session import SessionManager
    mgr = SessionManager(max_messages=5, summarize_after=3)
    from platforms.models import UnifiedMessage, Platform
    msg = UnifiedMessage(platform=Platform.CONSOLE, user_id="1", content="hi")
    session = mgr.get_or_create(msg)
    for i in range(10):
        session.add_message("user", f"msg{i}")
    assert len(session.messages) == 10
    assert mgr.should_summarize(session) is True  # 10 > summarize_after(3)


@pytest.mark.asyncio
async def test_relevant_profile_injects_only_top_matches(tmp_path):
    mem = EntityMemory(db_path=str(tmp_path / "test.db"), inject_top_k=2)
    await mem.store("user1", "preference", "喜欢的语言", "Python")
    await mem.store("user1", "fact", "职业", "程序员")
    await mem.store("user1", "preference", "喜欢的颜色", "蓝色")
    await mem.store("user1", "fact", "城市", "郑州")

    profile = await mem.get_relevant_profile_text("user1", "我想聊 Python")

    lines = [line for line in profile.splitlines() if line.startswith("- ")]
    assert len(lines) == 1
    assert "Python" in lines[0]
    assert "用户画像:" in profile

    assert await mem.get_relevant_profile_text("user1", "今天天气如何") == ""


@pytest.mark.asyncio
async def test_relationship_entities_are_injected_without_keyword_overlap(tmp_path):
    mem = EntityMemory(db_path=str(tmp_path / "test.db"))
    await mem.store("user1", "preference", "喜欢的语言", "Python")
    await mem.store("user1", "relationship", "称呼", "平时叫他小明")

    profile = await mem.get_relevant_profile_text("user1", "今天天气如何")

    assert "小明" in profile
    assert "用户画像:" in profile
