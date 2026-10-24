"""Character Card V2/V3 兼容层。

Markdown 人格卡仍是本项目的事实来源；这里只负责把 SillyTavern 生态常见的
JSON/PNG 角色卡转成 CharacterCard 可用的字段，以及反向导出。
"""
from __future__ import annotations

import base64
import io
import json
import re
import zlib
from typing import Any

from PIL import Image, ImageOps, PngImagePlugin

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CARD_TEXT_KEYS = ("chara", "ccv3", "character")
_V2_SPEC = "chara_card_v2"
_V2_VERSION = "2.0"

_USER_LINE = re.compile(r"^\s*\{\{user\}\}\s*[:：]\s*(.*)$", re.IGNORECASE)
_CHAR_LINE = re.compile(r"^\s*\{\{char\}\}\s*[:：]\s*(.*)$", re.IGNORECASE)


class CardFormatError(ValueError):
    """角色卡内容无法识别。"""


def decode_card(raw: bytes | str) -> dict[str, Any]:
    """把 PNG 卡或 JSON 卡解析成 CharacterCard 构造参数。"""

    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    raw = raw.lstrip(b"\xef\xbb\xbf").strip()
    if not raw:
        raise CardFormatError("角色卡内容为空")

    if raw.startswith(_PNG_SIGNATURE):
        payload = _decode_png(raw)
    else:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CardFormatError("既不是 PNG 角色卡，也不是合法的 JSON") from exc

    return normalize_payload(payload)


def normalize_payload(payload: Any) -> dict[str, Any]:
    """兼容 V1、V2、V3 以及直接导出的裸 data 对象。"""

    if not isinstance(payload, dict):
        raise CardFormatError("角色卡必须是一个 JSON 对象")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    name = str(data.get("name") or "").strip()
    if not name:
        raise CardFormatError("角色卡缺少 name 字段")

    description = str(data.get("description") or "").strip()
    scenario = str(data.get("scenario") or "").strip()
    system_prompt = str(data.get("system_prompt") or "").strip()

    body_parts: list[str] = []
    if description and description not in system_prompt:
        body_parts.append(description)
    if system_prompt:
        body_parts.append(system_prompt)
    if scenario and scenario not in system_prompt:
        body_parts.append(f"场景：{scenario}")
    system_prompt = "\n\n".join(body_parts) or f"你是{name}。"

    raw_personality = data.get("personality")
    if isinstance(raw_personality, dict):
        personality = raw_personality
    else:
        personality_text = str(raw_personality or "").strip()
        personality = {"tone": personality_text[:80]} if personality_text else {}

    extensions = data.get("extensions")
    voice = data.get("voice")
    if not isinstance(voice, dict) and isinstance(extensions, dict):
        voice = extensions.get("voice")
    return {
        "name": name,
        "description": description,
        "scenario": scenario,
        "system_prompt": system_prompt,
        "personality": personality,
        "greeting": str(data.get("first_mes") or data.get("greeting") or ""),
        "alternate_greetings": _string_list(data.get("alternate_greetings")),
        "examples": _parse_mes_example(str(data.get("mes_example") or "")),
        "post_history_instructions": str(data.get("post_history_instructions") or ""),
        "lorebook": _normalize_character_book(data.get("character_book")),
        "tags": _string_list(data.get("tags")),
        "creator": str(data.get("creator") or ""),
        "creator_notes": str(data.get("creator_notes") or ""),
        "character_version": str(data.get("character_version") or ""),
        "extensions": extensions if isinstance(extensions, dict) else {},
        "voice": voice if isinstance(voice, dict) else {},
    }


def encode_v2(card: Any) -> dict[str, Any]:
    """把 CharacterCard 导出为 Character Card V2 结构。"""

    data: dict[str, Any] = {
        "name": card.name,
        "description": card.description,
        "personality": _personality_text(card.personality),
        "scenario": card.scenario,
        "first_mes": card.greeting,
        "mes_example": _build_mes_example(card.examples),
        "creator_notes": card.creator_notes,
        "system_prompt": card.system_prompt,
        "post_history_instructions": card.post_history_instructions,
        "alternate_greetings": list(card.alternate_greetings),
        "tags": list(card.tags),
        "creator": card.creator,
        "character_version": card.character_version,
        "extensions": dict(card.extensions),
    }
    if card.voice:
        data["extensions"]["voice"] = dict(card.voice)
    if card.lorebook:
        data["character_book"] = {
            "name": f"{card.name} 世界书",
            "entries": [_export_lore_entry(entry) for entry in card.lorebook],
        }
    return {"spec": _V2_SPEC, "spec_version": _V2_VERSION, "data": data}


def encode_png(card: Any, image_bytes: bytes | None = None) -> bytes:
    """导出可被 SillyTavern 识别的 PNG 卡；未提供图片时生成占位图。"""

    if image_bytes:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image.load()
        except Exception as exc:
            raise CardFormatError("无法读取角色卡图片") from exc
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        image.thumbnail((1024, 1024))
    else:
        image = _placeholder_image()

    payload = encode_v2(card)
    info = PngImagePlugin.PngInfo()
    info.add_text(
        "chara",
        base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii"),
    )
    output = io.BytesIO()
    image.save(output, format="PNG", pnginfo=info)
    return output.getvalue()


def decode_png_text(raw: bytes) -> dict[str, str]:
    """读取 PNG 的文本块，主要供测试和诊断使用。"""

    if not raw.startswith(_PNG_SIGNATURE):
        raise CardFormatError("不是 PNG 文件")
    return _png_text_chunks(raw)


def _decode_png(raw: bytes) -> dict[str, Any]:
    texts = _png_text_chunks(raw)
    for key in _CARD_TEXT_KEYS:
        value = texts.get(key)
        if not value:
            continue
        try:
            return json.loads(base64.b64decode(value).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise CardFormatError("PNG 中没有找到有效的角色卡数据（chara 文本块）")


def _png_text_chunks(raw: bytes) -> dict[str, str]:
    texts: dict[str, str] = {}
    position = len(_PNG_SIGNATURE)
    while position + 12 <= len(raw):
        length = int.from_bytes(raw[position : position + 4], "big")
        chunk_type = raw[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(raw):
            break
        data = raw[position + 8 : position + 8 + length]
        if chunk_type == b"tEXt":
            key, _, value = data.partition(b"\x00")
            texts[key.decode("latin-1", "ignore")] = value.decode("latin-1", "ignore")
        elif chunk_type == b"iTXt":
            key, rest = data.split(b"\x00", 1) if b"\x00" in data else (data, b"")
            if len(rest) >= 2:
                compressed = rest[0] == 1
                rest = rest[2:]
                _, _, rest = rest.partition(b"\x00")
                _, _, value = rest.partition(b"\x00")
                if compressed:
                    try:
                        value = zlib.decompress(value)
                    except zlib.error:
                        value = b""
                texts[key.decode("latin-1", "ignore")] = value.decode("utf-8", "replace")
        position = end
        if chunk_type == b"IEND":
            break
    return texts


def _parse_mes_example(text: str) -> list[dict[str, str]]:
    examples: list[dict[str, str]] = []
    user_text: str | None = None
    assistant_lines: list[str] = []

    def flush() -> None:
        nonlocal user_text, assistant_lines
        if user_text is not None and assistant_lines:
            examples.append({"user": user_text.strip(), "assistant": "\n".join(assistant_lines).strip()})
        user_text = None
        assistant_lines = []

    for line in text.splitlines():
        if line.strip().upper() == "<START>":
            flush()
            continue
        user_match = _USER_LINE.match(line)
        if user_match:
            flush()
            user_text = user_match.group(1)
            continue
        char_match = _CHAR_LINE.match(line)
        if char_match:
            if user_text is not None:
                assistant_lines.append(char_match.group(1))
            continue
        if assistant_lines:
            assistant_lines.append(line)
    flush()
    return examples


def _build_mes_example(examples: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for example in examples:
        user = str(example.get("user") or "").strip()
        assistant = str(example.get("assistant") or "").strip()
        if user and assistant:
            blocks.append(f"<START>\n{{{{user}}}}: {user}\n{{{{char}}}}: {assistant}")
    return "\n".join(blocks)


def _normalize_character_book(book: Any) -> list[dict[str, Any]]:
    if not isinstance(book, dict):
        return []
    entries = book.get("entries")
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, list):
        return []

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        keys = _string_list(entry.get("keys") or entry.get("key"))
        constant = bool(entry.get("constant", False))
        if not keys and not constant:
            continue
        normalized.append(
            {
                "keys": keys,
                "secondary_keys": _string_list(entry.get("secondary_keys")),
                "content": content,
                "enabled": bool(entry.get("enabled", True)),
                "constant": constant,
                "selective": bool(entry.get("selective", False)),
                "priority": _as_int(entry.get("priority", entry.get("insertion_order")), 100),
                "case_sensitive": bool(entry.get("case_sensitive", False)),
                "name": str(entry.get("name") or entry.get("comment") or ""),
            }
        )
    return normalized


def _export_lore_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": list(entry.get("keys") or []),
        "secondary_keys": list(entry.get("secondary_keys") or []),
        "content": str(entry.get("content") or ""),
        "enabled": bool(entry.get("enabled", True)),
        "constant": bool(entry.get("constant", False)),
        "selective": bool(entry.get("selective", False)),
        "priority": _as_int(entry.get("priority"), 100),
        "case_sensitive": bool(entry.get("case_sensitive", False)),
        "name": str(entry.get("name") or ""),
    }


def _personality_text(personality: Any) -> str:
    if isinstance(personality, str):
        return personality
    if not isinstance(personality, dict):
        return ""
    tone = str(personality.get("tone") or "").strip()
    if tone:
        return tone
    return "，".join(f"{key}: {value}" for key, value in personality.items() if value not in (None, ""))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，\n]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _placeholder_image() -> Image.Image:
    gradient = Image.linear_gradient("L").resize((512, 768))
    return ImageOps.colorize(gradient, black="#184f95", white="#86b6ef").convert("RGB")
