"""统一消息模型"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    FILE = "file"
    SYSTEM = "system"


class Platform(str, Enum):
    WECHAT = "wechat"
    QQ = "qq"
    TELEGRAM = "telegram"
    CONSOLE = "console"


@dataclass
class UnifiedMessage:
    platform: Platform
    user_id: str
    content: str
    message_type: MessageType = MessageType.TEXT
    group_id: Optional[str] = None
    nickname: str = ""
    message_id: str = ""
    raw_data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_group(self) -> bool:
        return self.group_id is not None

    @property
    def session_key(self) -> str:
        if self.group_id:
            return f"{self.platform.value}:{self.group_id}"
        return f"{self.platform.value}:{self.user_id}"
