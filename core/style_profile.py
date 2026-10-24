"""Summarize how a user has been texting so the reply can match their rhythm."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_COMMAND = re.compile(r"^[/#]")
_PARTICLES = ("哈哈", "嘿嘿", "嗯", "啊", "呢", "吧", "嘛", "哦", "诶", "唉", "呀")


def _message_text(message: Any) -> tuple[str, str]:
    if isinstance(message, dict):
        return str(message.get("role") or ""), str(message.get("content") or "")
    return str(getattr(message, "role", "")), str(getattr(message, "content", ""))


def _recent_user_texts(messages: list[Any], limit: int) -> list[str]:
    texts: list[str] = []
    for message in reversed(messages):
        role, content = _message_text(message)
        text = content.strip()
        if role != "user" or not text or _COMMAND.match(text):
            continue
        texts.append(text)
        if len(texts) >= limit:
            break
    return list(reversed(texts))


def format_style_hint(
    messages: list[Any],
    *,
    limit: int = 12,
    min_messages: int = 3,
) -> str:
    """Return a compact style hint, or an empty string when there is too little history."""

    texts = _recent_user_texts(messages, limit)
    if len(texts) < min_messages:
        return ""

    average = sum(len(text) for text in texts) / len(texts)
    length_label = "偏短" if average <= 10 else "中等" if average <= 25 else "偏长"
    cues: list[str] = [f"回复长度保持相近（对方平均约 {round(average)} 字，{length_label}）"]

    threshold = max(2, len(texts) // 3)
    if sum(1 for text in texts if "！" in text or "!" in text) >= threshold:
        cues.append("对方常用感叹号，语气可以稍微活一点")
    if sum(1 for text in texts if "…" in text or "..." in text) >= threshold:
        cues.append("对方常用省略号，可以留一点停顿感")
    if sum(1 for text in texts if _EMOJI.search(text)) >= threshold:
        cues.append("对方偶尔用表情或颜文字，可以少量呼应，不要刷屏")

    counts: Counter[str] = Counter()
    for text in texts:
        for particle in _PARTICLES:
            if particle in text:
                counts[particle] += 1
    common = [particle for particle, count in counts.most_common(2) if count >= 2]
    if common:
        cues.append("对方常用" + "、".join(f"「{particle}」" for particle in common) + "这类语气词，可以自然跟上")

    return "对方最近的聊天习惯：" + "；".join(cues) + "。不要刻意模仿错别字或复制对方原话。"
