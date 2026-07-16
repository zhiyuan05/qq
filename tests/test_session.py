"""会话管理测试"""
from core.session import Session, SessionManager
from platforms.models import UnifiedMessage, Platform


def test_session_add_message():
    session = Session(session_id="test:1", user_id="1", platform="wechat")
    session.add_message("user", "你好")
    session.add_message("assistant", "你好呀！")
    assert len(session.messages) == 2
    assert session.messages[0].role == "user"


def test_session_get_recent():
    session = Session(session_id="test:1", user_id="1", platform="wechat")
    for i in range(10):
        session.add_message("user", f"msg{i}")
    recent = session.get_recent_messages(3)
    assert len(recent) == 3
    assert recent[0]["content"] == "msg7"


def test_session_undo():
    session = Session(session_id="test:1", user_id="1", platform="wechat")
    session.add_message("user", "问题1")
    session.add_message("assistant", "回答1")
    session.add_message("user", "问题2")
    session.add_message("assistant", "回答2")
    session.undo_last_exchange()
    assert len(session.messages) == 2
    assert session.messages[-1].content == "回答1"


def test_session_manager():
    mgr = SessionManager()
    msg = UnifiedMessage(platform=Platform.WECHAT, user_id="user1", content="hi")
    session = mgr.get_or_create(msg)
    assert session.session_id == "wechat:user1"
    assert mgr.get_or_create(msg) is session
