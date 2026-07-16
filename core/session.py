"""会话管理"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from platforms.models import UnifiedMessage


@dataclass
class ChatMessage:
    role: str
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    token_count: int = 0


@dataclass
class Session:
    session_id: str
    user_id: str
    platform: str
    character_card: str = "default"
    style: str = "casual"
    messages: List[ChatMessage] = field(default_factory=list)
    summary: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)

    def add_message(self, role: str, content: str, token_count: int = 0):
        self.messages.append(ChatMessage(role=role, content=content, token_count=token_count))
        self.last_active = datetime.now()

    def get_recent_messages(self, n: int = 20) -> List[Dict[str, str]]:
        recent = self.messages[-n:]
        return [{"role": m.role, "content": m.content} for m in recent]

    def get_context_messages(self, max_messages: int = 50) -> List[Dict[str, str]]:
        result = []
        if self.summary:
            result.append({"role": "system", "content": f"之前的对话摘要：{self.summary}"})
        recent = self.messages[-max_messages:]
        result.extend([{"role": m.role, "content": m.content} for m in recent])
        return result

    def undo_last_exchange(self):
        if len(self.messages) >= 2:
            self.messages = self.messages[:-2]


class SessionManager:
    def __init__(self, max_messages: int = 50, summarize_after: int = 30):
        self.max_messages = max_messages
        self.summarize_after = summarize_after
        self._sessions: Dict[str, Session] = {}

    def get_or_create(self, msg: UnifiedMessage, default_character: str = "default",
                      default_style: str = "casual") -> Session:
        key = msg.session_key
        if key not in self._sessions:
            self._sessions[key] = Session(
                session_id=key,
                user_id=msg.user_id,
                platform=msg.platform.value,
                character_card=default_character,
                style=default_style,
            )
        return self._sessions[key]

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> List[str]:
        return list(self._sessions.keys())

    def clear(self, session_id: str):
        self._sessions.pop(session_id, None)

    def should_summarize(self, session: Session) -> bool:
        return len(session.messages) > self.summarize_after
