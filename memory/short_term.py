"""短期记忆"""
from typing import Dict, List

from core.session import Session


class ShortTermMemory:
    def __init__(self, max_messages: int = 50):
        self.max_messages = max_messages

    def get_context(self, session: Session) -> List[Dict[str, str]]:
        return session.get_context_messages(self.max_messages)

    def trim_if_needed(self, session: Session) -> bool:
        return len(session.messages) > self.max_messages
