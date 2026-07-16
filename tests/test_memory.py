"""记忆系统测试"""
from memory.entity_memory import EntityMemory
from memory.short_term import ShortTermMemory
from core.session import Session


def _make_mem(tmp_path=None):
    db = str(tmp_path / "test.db") if tmp_path else ":memory:"
    return EntityMemory(db_path=db)


def test_entity_memory_store_and_query(tmp_path):
    mem = _make_mem(tmp_path)
    mem.store("user1", "preference", "喜欢的语言", "Python")
    mem.store("user1", "fact", "职业", "程序员")
    mem.store("user2", "preference", "喜欢的颜色", "蓝色")

    results = mem.query("user1")
    assert len(results) == 2

    prefs = mem.query("user1", "preference")
    assert len(prefs) == 1
    assert prefs[0].value == "Python"


def test_entity_memory_update(tmp_path):
    mem = _make_mem(tmp_path)
    mem.store("user1", "preference", "喜欢的语言", "Python")
    mem.store("user1", "preference", "喜欢的语言", "Go")
    results = mem.query("user1", "preference")
    assert len(results) == 1
    assert results[0].value == "Go"


def test_entity_memory_forget(tmp_path):
    mem = _make_mem(tmp_path)
    mem.store("user1", "fact", "职业", "程序员")
    assert mem.forget("user1", "职业") is True
    assert len(mem.query("user1")) == 0


def test_entity_memory_profile(tmp_path):
    mem = _make_mem(tmp_path)
    mem.store("user1", "preference", "语言", "Python")
    mem.store("user1", "fact", "职业", "程序员")
    profile = mem.get_profile_text("user1")
    assert "Python" in profile
    assert "程序员" in profile


def test_short_term_memory():
    stm = ShortTermMemory(max_messages=5)
    session = Session(session_id="test:1", user_id="1", platform="test")
    for i in range(10):
        session.add_message("user", f"msg{i}")
    assert stm.trim_if_needed(session) is True
