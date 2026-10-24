"""对话摘要器 - 支持 LLM 或规则 fallback 摘要"""
from __future__ import annotations

from typing import Dict, List

from utils.logger import setup_logger

logger = setup_logger("summarizer")


class ConversationSummarizer:
    def __init__(self, llm_client=None):
        self._llm = llm_client
        self._llm_client_explicit = llm_client is not None

    def set_llm_client(self, llm_client) -> None:
        """仅在尚未显式注入客户端时绑定 Engine 的共享客户端。"""

        if not self._llm_client_explicit:
            self._llm = llm_client

    async def summarize(self, messages: List[Dict[str, str]]) -> str:
        if not messages:
            return ""

        if not self._llm:
            return self._fallback_summary(messages)

        try:
            summary_prompt = [
                {"role": "system", "content": "请将以下对话压缩为简洁的摘要，保留关键信息和用户偏好，不超过200字。"},
                {"role": "user", "content": self._format_messages(messages)},
            ]
            summary = await self._llm.chat(summary_prompt)
            if getattr(self._llm, "last_call_succeeded", None) is False or not summary.strip():
                return self._fallback_summary(messages)
            return summary
        except Exception as e:
            logger.warning(f"LLM 摘要失败，使用规则摘要: {e}")
            return self._fallback_summary(messages)

    def _fallback_summary(self, messages: List[Dict[str, str]]) -> str:
        """LLM 不可用时使用规则摘要，并保留已有摘要。"""
        previous = ""
        for message in messages:
            if message.get("role") != "system":
                continue
            content = str(message.get("content") or "")
            prefix = "之前的对话摘要："
            if content.startswith(prefix):
                previous = content[len(prefix) :].strip()
            break
        recent = messages[-12:]
        parts = []
        for m in recent:
            if m.get("role") == "user":
                content = (m.get("content") or "")[:60]
                if content:
                    parts.append(content)
        body = "；".join(parts[-5:])
        combined = f"{previous} {body}".strip() if previous else body
        return combined[:800]

    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        lines = []
        for m in messages[-50:]:  # 限制输入防 token 超限
            role = "用户" if m.get("role") == "user" else "助手"
            content = (m.get("content") or "")[:200]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
