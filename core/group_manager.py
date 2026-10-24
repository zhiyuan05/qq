from __future__ import annotations
"""群聊管理"""
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
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
    reply_probability: Optional[float] = None  # None=使用全局 humanize 配置
    reply_cooldown_seconds: Optional[int] = None
    welcome_message: str = ""


class GroupManager:
    def __init__(self, storage_path: str | None = None):
        self._groups: Dict[str, GroupConfig] = {}
        self._storage = Path(storage_path) if storage_path else None
        self._lock = threading.Lock()
        self._last_reply: Dict[str, float] = {}
        self._load()

    def _key(self, platform: str, group_id: str) -> str:
        return f"{platform}:{group_id}"

    def _load(self) -> None:
        if self._storage is None or not self._storage.is_file():
            return
        try:
            rows = json.loads(self._storage.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("群配置读取失败 (%s)", type(exc).__name__)
            return
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or not row.get("group_id") or not row.get("platform"):
                continue
            try:
                config = GroupConfig(
                    group_id=str(row["group_id"]),
                    platform=str(row["platform"]),
                    name=str(row.get("name", "")),
                    character_card=row.get("character_card") or None,
                    style=row.get("style") or None,
                    admins=[str(item) for item in row.get("admins") or []],
                    enabled=bool(row.get("enabled", True)),
                    mention_only=bool(row.get("mention_only", False)),
                    reply_probability=(
                        float(row["reply_probability"])
                        if row.get("reply_probability") is not None
                        else None
                    ),
                    reply_cooldown_seconds=(
                        int(row["reply_cooldown_seconds"])
                        if row.get("reply_cooldown_seconds") is not None
                        else None
                    ),
                    welcome_message=str(row.get("welcome_message", "")),
                )
            except (TypeError, ValueError):
                continue
            self._groups[self._key(config.platform, config.group_id)] = config

    def save(self) -> None:
        if self._storage is None:
            return
        with self._lock:
            rows = [asdict(group) for group in self._groups.values()]
            try:
                self._storage.parent.mkdir(parents=True, exist_ok=True)
                temporary = self._storage.with_suffix(self._storage.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(temporary, self._storage)
            except OSError as exc:
                logger.warning("群配置保存失败 (%s)", type(exc).__name__)

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

    def should_join(
        self,
        platform: str,
        group_id: str,
        user_id: str,
        text: str,
        *,
        is_mentioned: bool,
        is_admin: bool = False,
        message_id: str = "",
        bot_name: str = "",
        reply_probability: float = 0.25,
        cooldown_seconds: int = 90,
        now: float | None = None,
    ) -> bool:
        """Decide whether an unmentioned group message deserves a reply.

        People do not answer every message. Mentions, replies to the bot, and
        admins always get through; everything else is deterministic per
        message so retries do not flip the decision.
        """

        group = self.get(platform, group_id)
        if group and not group.enabled:
            return False
        if group and group.mention_only and not is_mentioned:
            return False
        if is_mentioned or is_admin:
            return True
        if bot_name and bot_name in (text or ""):
            return True  # being called by name counts as a direct address

        if group is not None and group.reply_probability is not None:
            reply_probability = group.reply_probability
        if group is not None and group.reply_cooldown_seconds is not None:
            cooldown_seconds = group.reply_cooldown_seconds

        key = self._key(platform, group_id)
        moment = time.monotonic() if now is None else now
        last_reply = self._last_reply.get(key)
        if last_reply is not None and moment - last_reply < cooldown_seconds:
            return False

        probability = max(0.0, min(1.0, reply_probability))
        if probability <= 0:
            return False
        if probability >= 1:
            return True

        seed = f"{key}:{message_id or text}:{user_id}".encode("utf-8")
        roll = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") / 2**64
        return roll < probability

    def mark_replied(self, platform: str, group_id: str, *, now: float | None = None) -> None:
        self._last_reply[self._key(platform, group_id)] = (
            time.monotonic() if now is None else now
        )

    def add_admin(self, platform: str, group_id: str, user_id: str):
        group = self.get_or_create(platform, group_id)
        if user_id not in group.admins:
            group.admins.append(user_id)
            self.save()

    def remove_admin(self, platform: str, group_id: str, user_id: str):
        group = self.get(platform, group_id)
        if group and user_id in group.admins:
            group.admins.remove(user_id)
            self.save()
