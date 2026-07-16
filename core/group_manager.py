"""群聊管理"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("group")


@dataclass
class GroupConfig:
    group_id: str
    platform: str
    name: str = ""
    character_card: Optional[str] = None  # None=使用全局默认
    style: Optional[str] = None
    admins: List[str] = field(default_factory=list)
    enabled: bool = True
    mention_only: bool = False  # True=只在被@时回复
    welcome_message: str = ""


class GroupManager:
    def __init__(self):
        self._groups: Dict[str, GroupConfig] = {}

    def _key(self, platform: str, group_id: str) -> str:
        return f"{platform}:{group_id}"

    def get_or_create(self, platform: str, group_id: str) -> GroupConfig:
        key = self._key(platform, group_id)
        if key not in self._groups:
            self._groups[key] = GroupConfig(group_id=group_id, platform=platform)
        return self._groups[key]

    def get(self, platform: str, group_id: str) -> Optional[GroupConfig]:
        return self._groups.get(self._key(platform, group_id))

    def list_groups(self) -> List[GroupConfig]:
        return list(self._groups.values())

    def is_admin(self, platform: str, group_id: str, user_id: str) -> bool:
        group = self.get(platform, group_id)
        if not group:
            return False
        return user_id in group.admins

    def should_respond(self, platform: str, group_id: str, user_id: str,
                       is_mentioned: bool, bot_user_id: str = "") -> bool:
        """判断是否应该回复群消息"""
        group = self.get(platform, group_id)
        if not group:
            return True  # 未配置的群默认回复

        if not group.enabled:
            return False

        if group.mention_only and not is_mentioned:
            return False

        return True

    def add_admin(self, platform: str, group_id: str, user_id: str):
        group = self.get_or_create(platform, group_id)
        if user_id not in group.admins:
            group.admins.append(user_id)

    def remove_admin(self, platform: str, group_id: str, user_id: str):
        group = self.get(platform, group_id)
        if group and user_id in group.admins:
            group.admins.remove(user_id)
