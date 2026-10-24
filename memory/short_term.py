"""短期记忆 — 已废弃，由 core.session.SessionManager 替代。

此前 ShortTermMemory 仅是对 SessionManager 的浅封装，
该功能现已完全合并至 SessionManager，此模块保留仅为兼容导入。
"""
from __future__ import annotations

import warnings as _warnings
from core.session import Session


class ShortTermMemory:
    """已废弃 — 请使用 SessionManager。"""

    def __init__(self, max_messages: int = 50):
        _warnings.warn("ShortTermMemory 已废弃，请使用 SessionManager", DeprecationWarning, stacklevel=2)
        self.max_messages = max_messages

    def get_context(self, session: Session) -> list[dict[str, str]]:
        return session.get_context_messages(self.max_messages)

    def trim_if_needed(self, session: Session) -> bool:
        return len(session.messages) > self.max_messages
