from __future__ import annotations

"""学习分析器"""
from typing import Dict, List

from utils.logger import setup_logger

logger = setup_logger("learning.analyzer")


class LearningAnalyzer:
    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def analyze(self, conversations: List[Dict]) -> Dict:
        if not self._llm or not conversations:
            return {}

        conv_text = self._format_conversations(conversations)

        prompt = [
            {"role": "system", "content": """分析以下对话记录，提取AI助手的人设演化建议。
返回JSON格式:
{
    "communication_style": ["建议1", "建议2"],
    "topic_expertise": ["建议1"],
    "behavioral_patterns": ["建议1"],
    "emotional_intelligence": ["建议1"],
    "rules_to_add": ["新规则1"],
    "summary": "一句话总结"
}
只返回JSON，不要其他内容。"""},
            {"role": "user", "content": conv_text},
        ]

        try:
            result = await self._llm.chat(prompt)
            import json
            return json.loads(result.strip().strip("```json").strip("```"))
        except Exception as e:
            logger.error(f"学习分析失败: {e}")
            return {}

    def _format_conversations(self, conversations: List[Dict]) -> str:
        lines = []
        for conv in conversations[-200:]:
            role = "用户" if conv.get("role") == "user" else "助手"
            content = conv.get("content", "")[:200]
            lines.append(f"{role}: {content}")
        return "\n".join(lines)
