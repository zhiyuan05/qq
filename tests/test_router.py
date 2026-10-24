"""消息路由测试"""
from core.router import MessageRouter
from platforms.models import UnifiedMessage, Platform


def _make_msg(user_id="u1", content="hello", group_id=None):
    return UnifiedMessage(platform=Platform.WECHAT, user_id=user_id, content=content, group_id=group_id)


def test_route_normal_message():
    router = MessageRouter()
    ok, reason, cmd = router.route(_make_msg(content="你好"))
    assert ok is True
    assert cmd.is_command is False


def test_route_command():
    router = MessageRouter()
    ok, reason, cmd = router.route(_make_msg(content="/help"))
    assert ok is True
    assert cmd.is_command is True
    assert cmd.command == "help"


def test_route_dedup():
    router = MessageRouter()
    message = _make_msg(content="same")
    ok1, _, _ = router.route(message)
    router.mark_processed(message)
    ok2, _, _ = router.route(_make_msg(content="same"))
    assert ok1 is True
    assert ok2 is False


def test_route_prefers_platform_message_id_for_deduplication():
    router = MessageRouter()
    first = _make_msg(content="same")
    first.message_id = "message-1"
    second = _make_msg(content="same")
    second.message_id = "message-2"
    replay = _make_msg(content="changed")
    replay.message_id = "message-1"
    other_user = _make_msg(user_id="u2", content="same")
    other_user.message_id = "message-1"

    assert router.route(first)[0] is True
    router.mark_processed(first)
    assert router.route(second)[0] is True
    assert router.route(replay)[:2] == (False, "重复消息")
    assert router.route(other_user)[0] is True


def test_route_allowed_users():
    router = MessageRouter(allowed_users=["u1"])
    ok1, _, _ = router.route(_make_msg(user_id="u1"))
    ok2, _, _ = router.route(_make_msg(user_id="u2"))
    assert ok1 is True
    assert ok2 is False


def test_admin_identity_supports_platform_scope_without_cross_platform_grant():
    router = MessageRouter(admin_users=["qq:42"])

    assert router.is_admin("42", "qq") is True
    assert router.is_admin("42", "telegram") is False
