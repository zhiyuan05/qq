from __future__ import annotations
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

    @staticmethod
    def _dedup_key(msg: UnifiedMessage) -> str:
        return (
            f"message:{msg.session_key}:{msg.message_id}"
            if msg.message_id
            else f"content:{msg.platform.value}:{msg.user_id}:{msg.content}:{msg.group_id}"
        )

    def route(self, msg: UnifiedMessage) -> Tuple[bool, str, Optional[ParsedCommand]]:
        if self.allowed_users and msg.user_id not in self.allowed_users:
            return False, "未授权用户", None

        dedup_key = self._dedup_key(msg)
        now = time.monotonic()
        if dedup_key in self._recent_messages:
            if now - self._recent_messages[dedup_key] < self._dedup_window:
                return False, "重复消息", None

        parsed = self.command_parser.parse(msg.content)
        return True, "", parsed

    def mark_processed(self, msg: UnifiedMessage) -> None:
        """Commit replay protection only after the core operation succeeded."""

        now = time.monotonic()
        self._recent_messages[self._dedup_key(msg)] = now
        if len(self._recent_messages) > 1000:
            cutoff = now - self._dedup_window * 2
            self._recent_messages = {key: seen for key, seen in self._recent_messages.items() if seen > cutoff}

    def is_admin(self, user_id: str, platform: str = "") -> bool:
        scoped_identity = f"{platform}:{user_id}" if platform else ""
        return user_id in self.admin_users or scoped_identity in self.admin_users
