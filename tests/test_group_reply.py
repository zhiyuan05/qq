"""群聊参与决策测试。"""
from core.group_manager import GroupManager


def _manager(tmp_path) -> GroupManager:
    return GroupManager(str(tmp_path / "groups.json"))


def test_mention_always_replies_even_with_zero_probability(tmp_path):
    manager = _manager(tmp_path)

    assert manager.should_join(
        "qq", "g1", "u1", "随便聊聊",
        is_mentioned=True, reply_probability=0, cooldown_seconds=600,
    )


def test_unmentioned_message_can_stay_silent(tmp_path):
    manager = _manager(tmp_path)

    assert not manager.should_join(
        "qq", "g1", "u1", "随便聊聊",
        is_mentioned=False, reply_probability=0,
    )


def test_cooldown_suppresses_follow_up_chatter_but_not_mentions(tmp_path):
    manager = _manager(tmp_path)
    manager.mark_replied("qq", "g1", now=100.0)

    assert not manager.should_join(
        "qq", "g1", "u1", "继续聊",
        is_mentioned=False, reply_probability=1.0,
        cooldown_seconds=90, now=150.0,
    )
    assert manager.should_join(
        "qq", "g1", "u1", "继续聊",
        is_mentioned=True, reply_probability=0.0,
        cooldown_seconds=90, now=150.0,
    )
    assert manager.should_join(
        "qq", "g1", "u1", "继续聊",
        is_mentioned=False, reply_probability=1.0,
        cooldown_seconds=90, now=200.0,
    )


def test_admins_and_disabled_groups(tmp_path):
    manager = _manager(tmp_path)
    group = manager.get_or_create("qq", "g1")
    group.enabled = False

    assert not manager.should_join(
        "qq", "g1", "owner", "公告",
        is_mentioned=True, is_admin=True, reply_probability=1.0,
    )

    group.enabled = True
    assert manager.should_join(
        "qq", "g1", "owner", "公告",
        is_mentioned=False, is_admin=True, reply_probability=0.0,
    )


def test_mention_only_group_requires_a_mention(tmp_path):
    manager = _manager(tmp_path)
    manager.get_or_create("qq", "g1").mention_only = True

    assert not manager.should_join(
        "qq", "g1", "u1", "随便聊聊",
        is_mentioned=False, reply_probability=1.0,
    )


def test_decision_is_stable_for_the_same_message(tmp_path):
    manager = _manager(tmp_path)
    arguments = dict(
        is_mentioned=False, message_id="m-1", reply_probability=0.5,
    )

    first = manager.should_join("qq", "g1", "u1", "同一句话", **arguments)
    second = manager.should_join("qq", "g1", "u1", "同一句话", **arguments)

    assert first == second


def test_bot_name_in_text_raises_the_odds(tmp_path):
    manager = _manager(tmp_path)

    assert manager.should_join(
        "qq", "g1", "u1", "AI助手你在吗",
        is_mentioned=False, bot_name="AI助手", reply_probability=0.0,
    )
