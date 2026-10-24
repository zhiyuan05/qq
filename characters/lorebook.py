"""角色卡世界书（lorebook）的轻量关键词选择。

只实现聊天场景需要的一层：主关键词命中、可选二次关键词、常驻条目、
优先级和总字符预算。不实现 SillyTavern 的全部插入位置/递归扫描语义。
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_CJK = re.compile(r"[㐀-鿿]")


def select_lorebook_entries(
    entries: Iterable[Mapping[str, Any]] | None,
    text: str,
    *,
    limit: int = 3,
    max_chars: int = 1200,
) -> list[str]:
    """按当前消息返回最多 ``limit`` 条设定，总长度不超过 ``max_chars``。"""

    if not entries or not text or limit <= 0 or max_chars <= 0:
        return []

    normalized_text = text.casefold()
    matches: list[tuple[int, int, str]] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping):
            continue
        if not bool(raw.get("enabled", True)):
            continue
        content = str(raw.get("content") or "").strip()
        keys = _keys(raw.get("keys"))
        constant = bool(raw.get("constant", False))
        if not content or (not keys and not constant):
            continue

        case_sensitive = bool(raw.get("case_sensitive", False))
        primary_hit = any(_match_key(key, text, normalized_text, case_sensitive) for key in keys)
        if not constant and not primary_hit:
            continue

        if bool(raw.get("selective", False)) and not constant:
            secondary = _keys(raw.get("secondary_keys"))
            if secondary and not any(
                _match_key(key, text, normalized_text, case_sensitive) for key in secondary
            ):
                continue

        try:
            priority = int(raw.get("priority", 100))
        except (TypeError, ValueError):
            priority = 100
        matches.append((-priority, index, content))

    selected: list[str] = []
    used = 0
    for _, _, content in sorted(matches):
        if len(selected) >= limit:
            break
        if used + len(content) > max_chars:
            continue
        selected.append(content)
        used += len(content)
    return selected


def format_lorebook(entries: Iterable[str]) -> str:
    """把选中的设定包成一段系统提示；没有内容时返回空串。"""

    lines = [str(entry).strip() for entry in entries if str(entry).strip()]
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return f"相关设定（只在自然相关时使用，不要逐条复述或主动解释）:\n{body}"


def _keys(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,，\n]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _match_key(key: str, text: str, normalized_text: str, case_sensitive: bool) -> bool:
    if case_sensitive:
        needle, haystack = key, text
    else:
        needle, haystack = key.casefold(), normalized_text
    if not needle:
        return False
    if _CJK.search(needle):
        return needle in haystack
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])"
    return re.search(pattern, haystack) is not None
