"""群聊管理测试"""
import pytest
from core.group_manager import GroupManager, GroupConfig


def test_group_manager_create():
    gm = GroupManager()
    group = gm.get_or_create("qq", "12345")
    assert group.group_id == "12345"
    assert group.platform == "qq"
    assert group.mention_only is False


def test_group_manager_get_existing():
    gm = GroupManager()
    gm.get_or_create("qq", "12345")
    group = gm.get("qq", "12345")
    assert group is not None
    assert group.group_id == "12345"


def test_group_manager_get_nonexistent():
    gm = GroupManager()
    assert gm.get("qq", "nonexistent") is None


def test_group_manager_list():
    gm = GroupManager()
    gm.get_or_create("qq", "g1")
    gm.get_or_create("wechat", "g2")
    groups = gm.list_groups()
    assert len(groups) == 2


def test_group_mention_only():
    gm = GroupManager()
    group = gm.get_or_create("qq", "g1")
    group.mention_only = True
    assert gm.should_respond("qq", "g1", "u1", is_mentioned=False) is False
    assert gm.should_respond("qq", "g1", "u1", is_mentioned=True) is True


def test_group_disabled():
    gm = GroupManager()
    group = gm.get_or_create("qq", "g1")
    group.enabled = False
    assert gm.should_respond("qq", "g1", "u1", is_mentioned=True) is False


def test_group_admin():
    gm = GroupManager()
    gm.add_admin("qq", "g1", "admin1")
    assert gm.is_admin("qq", "g1", "admin1") is True
    assert gm.is_admin("qq", "g1", "user1") is False
    gm.remove_admin("qq", "g1", "admin1")
    assert gm.is_admin("qq", "g1", "admin1") is False


def test_group_character_override():
    gm = GroupManager()
    group = gm.get_or_create("qq", "g1")
    group.character_card = "custom"
    group.style = "professional"
    assert group.character_card == "custom"
    assert group.style == "professional"
