"""联网搜索插件"""
import httpx
from typing import Callable, Dict, Optional

from plugins.base import BasePlugin
from platforms.models import UnifiedMessage
from utils.logger import setup_logger

logger = setup_logger("web_search")


class WebSearchPlugin(BasePlugin):
    name = "web_search"
    version = "1.0.0"
    description = "联网搜索，在回复中注入搜索结果"

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.enabled = config.get("enabled", True) if config else True
        self.max_results = config.get("max_results", 3) if config else 3

    async def on_response(self, response: str, message: UnifiedMessage) -> Optional[str]:
        """检测回复中是否需要搜索结果"""
        # 不做自动搜索，只响应 /search 指令
        return None

    def get_commands(self) -> Dict[str, Callable]:
        return {"search": self._search_command}

    async def _search_command(self, args: list, user_id: str = "") -> str:
        """执行搜索"""
        if not args:
            return "用法: /search <关键词>"

        query = " ".join(args)
        if not self.enabled:
            return "搜索功能未启用"

        try:
            results = await self._duckduckgo_search(query)
            if not results:
                return f"未找到关于「{query}」的搜索结果"

            lines = [f"🔍 搜索结果: {query}\n"]
            for i, r in enumerate(results[:self.max_results], 1):
                lines.append(f"{i}. {r['title']}")
                lines.append(f"   {r['snippet']}")
                lines.append(f"   {r['url']}\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return f"搜索失败: {e}"

    async def _duckduckgo_search(self, query: str) -> list:
        """使用 DuckDuckGo 搜索"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
                timeout=10,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            results = []

            # Abstract
            if data.get("Abstract"):
                results.append({
                    "title": data.get("Heading", ""),
                    "snippet": data["Abstract"],
                    "url": data.get("AbstractURL", ""),
                })

            # Related Topics
            for topic in data.get("RelatedTopics", [])[:self.max_results]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append({
                        "title": topic.get("Text", "")[:80],
                        "snippet": topic.get("Text", ""),
                        "url": topic.get("FirstURL", ""),
                    })

            return results[:self.max_results]
