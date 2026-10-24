"""OneBot v11 message segment parsing (array and CQ-string forms)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from media.qq_cdn import emoji_id_from_file
from platforms.models import MessageType

_CQ_PATTERN = re.compile(r"\[CQ:([A-Za-z0-9_.-]+)((?:,[^\]]*)?)\]")


def unescape_cq(text: str) -> str:
    return (
        text.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _split_params(raw: str) -> dict[str, str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in raw:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    params: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        params[key.strip()] = unescape_cq(value.strip())
    return params


def _parse_cq(message: str) -> list[tuple[str, dict[str, Any]]]:
    segments: list[tuple[str, dict[str, Any]]] = []
    position = 0
    for match in _CQ_PATTERN.finditer(message):
        if match.start() > position:
            text = unescape_cq(message[position : match.start()])
            if text:
                segments.append(("text", {"text": text}))
        segments.append((match.group(1), _split_params(match.group(2).lstrip(","))))
        position = match.end()
    if position < len(message):
        text = unescape_cq(message[position:])
        if text:
            segments.append(("text", {"text": text}))
    return segments


@dataclass(frozen=True)
class MediaRef:
    kind: MessageType
    url: str = ""
    file: str = ""
    file_name: str = ""
    emoji_id: str = ""
    summary: str = ""
    path: str = ""
    file_id: str = ""


@dataclass
class ParsedSegments:
    text: str = ""
    model_text: str = ""
    media: list[MediaRef] = field(default_factory=list)
    quote_id: str = ""
    at_user_ids: list[str] = field(default_factory=list)
    mentioned_bot: bool = False


def _image_ref(data: dict[str, Any]) -> MediaRef:
    file_name = str(data.get("file", "") or "")
    emoji_id = str(data.get("emoji_id") or data.get("emojiId") or emoji_id_from_file(file_name))
    return MediaRef(
        kind=MessageType.IMAGE,
        url=str(data.get("url", "") or ""),
        file=file_name,
        file_name=file_name,
        emoji_id=emoji_id,
        summary=str(data.get("summary", "") or ""),
    )


def parse_message(raw: dict[str, Any], *, self_id: str = "") -> ParsedSegments:
    message = raw.get("message")
    if isinstance(message, list):
        segments = [
            (str(segment.get("type", "")), segment.get("data") or {})
            for segment in message
            if isinstance(segment, dict)
        ]
    elif isinstance(message, str):
        segments = _parse_cq(message)
    else:
        segments = _parse_cq(str(raw.get("raw_message", "") or ""))

    parsed = ParsedSegments()
    text_parts: list[str] = []
    model_parts: list[str] = []

    for segment_type, data in segments:
        if segment_type == "text":
            value = str(data.get("text", ""))
            text_parts.append(value)
            model_parts.append(value)
        elif segment_type == "at":
            user_id = str(data.get("qq", "") or "")
            if user_id:
                parsed.at_user_ids.append(user_id)
                if self_id and user_id == self_id:
                    parsed.mentioned_bot = True
                token = f"[CQ:at,qq={user_id}]"
                text_parts.append(token)
                model_parts.append(token)
        elif segment_type == "reply":
            parsed.quote_id = str(data.get("id", "") or "")
        elif segment_type == "image":
            parsed.media.append(_image_ref(data))
            model_parts.append("[图片]")
        elif segment_type == "mface":
            parsed.media.append(_image_ref(data))
            summary = str(data.get("summary") or data.get("brief") or "").strip()
            model_parts.append(f"[表情:{summary}]" if summary else "[表情]")
        elif segment_type == "face":
            model_parts.append("[表情]")
        elif segment_type == "record":
            parsed.media.append(
                MediaRef(
                    kind=MessageType.VOICE,
                    url=str(data.get("url", "") or ""),
                    file=str(data.get("file", "") or ""),
                    file_name=str(data.get("file", "") or ""),
                    path=str(data.get("path", "") or ""),
                    file_id=str(data.get("file_id", "") or ""),
                )
            )
            model_parts.append("[语音]")
        elif segment_type == "file":
            name = str(data.get("file", "") or data.get("name", "") or "")
            parsed.media.append(
                MediaRef(
                    kind=MessageType.FILE,
                    url=str(data.get("url", "") or ""),
                    file=str(data.get("file", "") or ""),
                    file_name=name,
                )
            )
            model_parts.append(f"[文件:{name}]" if name else "[文件]")
        elif segment_type == "video":
            parsed.media.append(
                MediaRef(
                    kind=MessageType.VIDEO,
                    url=str(data.get("url", "") or ""),
                    file=str(data.get("file", "") or ""),
                    file_name=str(data.get("file", "") or ""),
                )
            )
            model_parts.append("[视频]")

    parsed.text = "".join(text_parts).strip()
    parsed.model_text = "".join(model_parts).strip()
    return parsed
