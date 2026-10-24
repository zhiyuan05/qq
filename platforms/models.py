"""Platform-neutral message and delivery models."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    VOICE = "voice"
    FILE = "file"
    SYSTEM = "system"


class Platform(str, Enum):
    WECHAT = "wechat"
    QQ = "qq"
    TELEGRAM = "telegram"
    CONSOLE = "console"


@dataclass(frozen=True, slots=True)
class Attachment:
    """A locally stored inbound media file."""

    kind: MessageType
    path: str
    mime: str = ""
    size: int = 0
    sha256: str = ""
    duration: float | None = None
    transcript: str = ""


def attachment_to_dict(attachment: Attachment) -> dict[str, Any]:
    return {
        "kind": attachment.kind.value,
        "path": attachment.path,
        "mime": attachment.mime,
        "size": attachment.size,
        "sha256": attachment.sha256,
        "duration": attachment.duration,
        "transcript": attachment.transcript,
    }


def attachment_from_dict(data: dict[str, Any]) -> Attachment:
    return Attachment(
        kind=MessageType(data.get("kind", MessageType.FILE.value)),
        path=str(data.get("path", "")),
        mime=str(data.get("mime", "")),
        size=int(data.get("size", 0) or 0),
        sha256=str(data.get("sha256", "")),
        duration=data.get("duration"),
        transcript=str(data.get("transcript", "")),
    )


@dataclass(frozen=True, slots=True)
class QuotedMessage:
    message_id: str
    sender_id: str = ""
    sender_name: str = ""
    text: str = ""
    resolved: bool = False


@dataclass(slots=True)
class UnifiedMessage:
    platform: Platform
    user_id: str
    content: str
    message_type: MessageType = MessageType.TEXT
    group_id: str | None = None
    nickname: str = ""
    message_id: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    attachments: list[Attachment] = field(default_factory=list)
    model_content: str = ""
    quoted: QuotedMessage | None = None
    quoted_bot: bool = False

    @property
    def is_group(self) -> bool:
        return self.group_id is not None

    @property
    def principal_key(self) -> str:
        """Stable subject key until the future account/workspace layer binds identities."""

        return f"{self.platform.value}:{self.user_id}"

    @property
    def group_scope_key(self) -> str | None:
        return f"{self.platform.value}:{self.group_id}" if self.group_id else None

    @property
    def session_key(self) -> str:
        if self.group_id:
            return f"{self.platform.value}:{self.group_id}"
        return f"{self.platform.value}:{self.user_id}"

    @property
    def event_key(self) -> str:
        """Stable transport event key, empty when a platform has no event id."""

        if not self.message_id:
            return ""
        return f"{self.platform.value}:{self.session_key}:{self.user_id}:{self.message_id}"

    @property
    def prompt_text(self) -> str:
        """Model-facing text, which may include quote and media placeholders."""

        return self.model_content or self.content


@dataclass(slots=True)
class ReplyPlan:
    """A humanized outbound reply, delivered by the platform adapter."""

    bubbles: list[str] = field(default_factory=list)
    delays: list[float] = field(default_factory=list)
    humanized: bool = True
    reply_to: str = ""
    sticker_path: str = ""
    sticker_tag: str = ""
    sticker_hash: str = ""
    voice_text: str = ""
    voice_path: str = ""
    voice_name: str = ""
    voice_rate: str = ""
    voice_pitch: str = ""
    sticker_delay: float = 0.0
    voice_delay: float = 0.0
    fallback_text: str = ""
    delivered_bubbles: int = 0
    sticker_sent: bool = False
    voice_sent: bool = False

    def text(self) -> str:
        if self.bubbles:
            return "\n".join(self.bubbles)
        return self.fallback_text or self.voice_text

    def history_text(self) -> str:
        parts = list(self.bubbles)
        if self.sticker_tag:
            parts.append(f"[表情包:{self.sticker_tag}]")
        if self.voice_text:
            parts.append(f"[语音] {self.voice_text}")
        return "\n".join(parts) or self.fallback_text


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """Proof that a platform accepted an outbound message."""

    platform: Platform
    target_id: str
    message_id: str = ""
    accepted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
