"""消息路由"""
import time
from typing import Dict, Optional, Tuple

from platforms.models import UnifiedMessage
from core.command_parser import CommandParser, ParsedCommand
from utils.logger import setup_logger

logger = setup_logger("router")


class MessageRouter:
    def __init__(self, command_prefix: str = "/",
                 allowed_users: list = None, admin_users: list = None):
        self.command_parser = CommandParser(prefix=command_prefix)
        self.allowed_users = allowed_users or []
        self.admin_users = admin_users or []
        self._recent_messages: Dict[str, float] = {}
        self._dedup_window = 5

    def route(self, msg: UnifiedMessage) -> Tuple[bool, str, Optional[ParsedCommand]]:
        if self.allowed_users and msg.user_id not in self.allowed_users:
            return False, "未授权用户", None

        dedup_key = f"{msg.user_id}:{msg.content}:{msg.group_id}"
        now = time.time()
        if dedup_key in self._recent_messages:
            if now - self._recent_messages[dedup_key] < self._dedup_window:
                return False, "重复消息", None
        self._recent_messages[dedup_key] = now

        if len(self._recent_messages) > 1000:
            cutoff = now - self._dedup_window * 2
            self._recent_messages = {k: v for k, v in self._recent_messages.items() if v > cutoff}

        parsed = self.command_parser.parse(msg.content)
        return True, "", parsed

    def is_admin(self, user_id: str) -> bool:
        return user_id in self.admin_users
