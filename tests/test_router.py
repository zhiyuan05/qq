"""消息路由测试"""
from core.router import MessageRouter
from platforms.models import UnifiedMessage, Platform


def _make_msg(user_id="u1", content="hello", group_id=None):
    return UnifiedMessage(platform=Platform.WECHAT, user_id=user_id, content=content, group_id=group_id)


def test_route_normal_message():
    router = MessageRouter(rate_limit_enabled=False)
    ok, reason, cmd = router.route(_make_msg(content="你好"))
    assert ok is True
    assert cmd.is_command is False


def test_route_command():
    router = MessageRouter(rate_limit_enabled=False)
    ok, reason, cmd = router.route(_make_msg(content="/help"))
    assert ok is True
    assert cmd.is_command is True
    assert cmd.command == "help"


def test_route_dedup():
    router = MessageRouter(rate_limit_enabled=False)
    ok1, _, _ = router.route(_make_msg(content="same"))
    ok2, _, _ = router.route(_make_msg(content="same"))
    assert ok1 is True
    assert ok2 is False


def test_route_allowed_users():
    router = MessageRouter(rate_limit_enabled=False, allowed_users=["u1"])
    ok1, _, _ = router.route(_make_msg(user_id="u1"))
    ok2, _, _ = router.route(_make_msg(user_id="u2"))
    assert ok1 is True
    assert ok2 is False


def test_route_rate_limit():
    router = MessageRouter(rate_limit_enabled=True, max_per_minute=2, max_per_hour=100)
    ok1, _, _ = router.route(_make_msg(content="msg1"))
    ok2, _, _ = router.route(_make_msg(content="msg2"))
    ok3, reason, _ = router.route(_make_msg(content="msg3"))
    assert ok1 is True
    assert ok2 is True
    assert ok3 is False
    assert "频繁" in reason
