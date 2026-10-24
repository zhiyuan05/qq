"""自然语言人设切换意图识别。

只处理明确的切换请求，避免把"用猫娘的语气说话"这类普通聊天误判为切换。
"""
from __future__ import annotations

import re
from collections.abc import Mapping

_NEGATION = re.compile(r"(?:不要|别|不用|不想|不需要|无需|禁止|不是要)\s*(?:切换|换|改|变|用|扮演)")


class PersonaSwitchAgent:
    """规则型切换 Agent：不调用 LLM，低延迟且可预测。"""

    def detect(self, text: str, aliases: Mapping[str, str]) -> str | None:
        """返回目标角色卡的 key；没有明确切换意图时返回 None。

        ``aliases`` 同时包含卡名 key 和展示名，例如
        ``{"猫娘": "猫娘", "默认助手": "default"}``。
        """

        stripped = text.strip()
        if not stripped or len(stripped) > 80:
            return None
        if _NEGATION.search(stripped):
            return None

        for alias in sorted(aliases, key=len, reverse=True):
            target = aliases[alias]
            escaped = re.escape(alias)
            if alias.casefold() not in stripped.casefold():
                continue
            if self._matches(stripped, escaped):
                return target
        return None

    def _matches(self, text: str, name: str) -> bool:
        quoted = rf"[「“\"']?{name}[」”\"']?"
        # "切换到猫娘" / "换成人设学姐" / "换人设：猫娘"
        anywhere = (
            rf"(?:切换|切|换|改|变)(?:成|到|为|一下|个)?\s*"
            rf"(?:人设|角色卡|人格|角色)?[：:，,\s]*{quoted}",
            rf"(?:切换|切|换|改)(?:一下)?(?:人设|角色卡|人格|角色)[：:，,\s]*{quoted}",
        )
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in anywhere):
            return True

        # "用猫娘人设" / "扮演猫娘" / "让猫娘来" —— 要求基本是整句话。
        whole = (
            rf"(?:请|帮我|给我|我想|我要|让我)?\s*(?:用|使用|扮演)\s*{quoted}\s*"
            rf"(?:的)?\s*(?:人设|角色卡|人格|角色)?\s*(?:吧|好吗|可以吗|来聊|跟我聊|陪我聊)?[。！!？?]*",
            rf"(?:让|请)\s*{quoted}\s*(?:来(?:陪我|陪你)?|陪(?:你|我)|跟我聊)[吧。！!？?]*",
            rf"(?:从现在开始|接下来|以后|今后)[，,\s]*(?:你|由你)?\s*"
            rf"(?:就是|是|来当|当|扮演)\s*{quoted}[。！!]*",
            rf"(?:switch\s+to|use)\s+{quoted}(?:\s+persona)?[.!？?]*",
        )
        return any(re.fullmatch(pattern, text, re.IGNORECASE) for pattern in whole)
