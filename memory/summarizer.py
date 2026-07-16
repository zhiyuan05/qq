"""对话摘要器"""
from typing import List, Dict


class ConversationSummarizer:
    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def summarize(self, messages: List[Dict[str, str]]) -> str:
        if not self._llm:
            recent = messages[-5:]
            return "；".join(m["content"][:50] for m in recent if m["role"] == "user")

        summary_prompt = [
            {"role": "system", "content": "请将以下对话压缩为简洁的摘要，保留关键信息和用户偏好，不超过200字。"},
            {"role": "user", "content": self._format_messages(messages)},
        ]
        return await self._llm.chat(summary_prompt)

    def _format_messages(self, messages: List[Dict[str, str]]) -> str:
        lines = []
        for m in messages:
            role = "用户" if m["role"] == "user" else "助手"
            lines.append(f"{role}: {m['content'][:200]}")
        return "\n".join(lines)
