"""轻量模糊理解提示。

不额外调用模型：只在消息明显依赖上下文、指代或省略时，给主模型一段
理解提示，让它先结合历史推断，必要时用一句话确认。
"""
from __future__ import annotations

import re

_GREETING = re.compile(
    r"^(?:你好|您好|hi|hello|在吗|早|早安|晚安|谢谢|多谢|哈哈+|好|可以|行|收到)"
    r"[呀啊哦呢吧~～！!。.\s]*$",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(
    r"^(?:继续|然后呢|接着|还有呢|所以呢|怎么说|怎么办|咋办|真的吗|是吗|好吧|然后|再然后)"
    r"[？?！!。.\s]*$"
)
_REFERENCE = re.compile(r"(?:那个|这个|它|他|她|他们|她们|上面|刚才|之前|继续|然后|这样|那样|你说的)")
_ELLIPSIS = re.compile(r"(?:……|\.\.\.)")

_HINT = (
    "理解提示：这条消息可能依赖上下文、指代或省略。先结合最近对话、引用消息和用户画像推断；"
    "如果仍有多种合理解释，先用一句话确认你的理解，再回答。不要编造用户没说的需求。"
)


def ambiguity_hint(text: str, *, has_history: bool = False) -> str:
    """Return a short comprehension hint, or an empty string for clear messages."""

    stripped = text.strip()
    if not stripped or len(stripped) > 60 or stripped.startswith("/") or stripped.startswith("#"):
        return ""
    if _GREETING.match(stripped):
        return ""
    if _REFERENCE.search(stripped) or _ELLIPSIS.search(stripped):
        return _HINT
    if has_history and (_CONTINUATION.match(stripped) or len(stripped) <= 6):
        return _HINT
    return ""
