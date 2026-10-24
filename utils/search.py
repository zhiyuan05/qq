"""联网搜索：Bing HTML 优先，DuckDuckGo 摘要兜底。"""
from __future__ import annotations

import base64
import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from utils.logger import setup_logger

logger = setup_logger("utils.search")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_TAG = re.compile(r"<[^>]+>")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", value or ""))).strip()


def _decode_href(href: str) -> str:
    if "bing.com/ck/a" not in href:
        return href
    try:
        encoded = parse_qs(urlparse(href).query).get("u", [""])[0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        padded = encoded + "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, TypeError, UnicodeDecodeError):
        return href


class _BingParser(HTMLParser):
    """Extract ``li.b_algo`` results from Bing's HTML response."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._depth = 0
        self._current: dict[str, str] | None = None
        self._in_h2 = False
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {key: (value or "") for key, value in attrs}
        classes = values.get("class", "").split()
        if tag == "li" and "b_algo" in classes and self._current is None:
            self._current = {"title": "", "snippet": "", "url": ""}
            self._depth = 1
            return
        if self._current is None:
            return
        if tag == "li":
            self._depth += 1
        elif tag == "h2":
            self._in_h2 = True
        elif tag == "a" and self._in_h2 and not self._current["url"]:
            href = _decode_href(values.get("href", ""))
            if href.startswith(("http://", "https://")):
                self._current["url"] = href
                self._start_capture("title")
        elif tag == "p" and self._current["url"] and not self._current["snippet"]:
            self._start_capture("snippet")

    def handle_endtag(self, tag: str) -> None:
        if self._current is None:
            return
        if tag in {"a", "p"}:
            if self._capture:
                self._current[self._capture] = "".join(self._buffer)
            self._capture = None
        elif tag == "h2":
            self._in_h2 = False
        elif tag == "li":
            self._depth -= 1
            if self._depth <= 0:
                self._finish()

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            self._buffer.append(data)

    def _start_capture(self, field: str) -> None:
        self._capture = field
        self._buffer = []

    def _finish(self) -> None:
        if self._current is not None:
            title = _clean(self._current["title"])
            snippet = _clean(self._current["snippet"])
            if title and self._current["url"]:
                self.results.append(
                    {
                        "title": title[:120],
                        "snippet": snippet[:400],
                        "url": self._current["url"],
                    }
                )
        self._current = None
        self._capture = None
        self._depth = 0

    def close(self) -> None:
        super().close()
        if self._current is not None:
            self._finish()


async def bing_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    try:
        async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=10) as client:
            response = await client.get(
                "https://www.bing.com/search",
                params={"q": query, "setlang": "zh-hans", "form": "QBLH"},
            )
    except httpx.HTTPError as exc:
        logger.warning("Bing 搜索失败 (%s)", type(exc).__name__)
        return []
    if response.status_code != 200:
        logger.warning("Bing 搜索返回 %s", response.status_code)
        return []
    parser = _BingParser()
    try:
        parser.feed(response.text)
        parser.close()
    except Exception:
        return []
    return parser.results[:max_results]


async def duckduckgo_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """执行联网搜索，返回结构化结果列表。

    每条结果包含: title, snippet, url。Bing 不可用时回退到 DuckDuckGo 摘要 API。
    """
    results = await bing_search(query, max_results=max_results)
    if results:
        return results
    try:
        async with httpx.AsyncClient(headers=_HEADERS, timeout=10) as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
            )
    except httpx.HTTPError as exc:
        logger.warning("DuckDuckGo 搜索失败 (%s)", type(exc).__name__)
        return []
    if response.status_code != 200:
        logger.warning("DuckDuckGo 搜索返回 %s", response.status_code)
        return []
    try:
        data = response.json()
    except ValueError:
        return []
    fallback: list[dict[str, Any]] = []
    if data.get("Abstract"):
        fallback.append(
            {
                "title": data.get("Heading", "") or query,
                "snippet": data["Abstract"],
                "url": data.get("AbstractURL", ""),
            }
        )
    for topic in data.get("RelatedTopics", []):
        if len(fallback) >= max_results:
            break
        if isinstance(topic, dict) and topic.get("Text"):
            fallback.append(
                {
                    "title": topic.get("Text", "")[:80],
                    "snippet": topic.get("Text", ""),
                    "url": topic.get("FirstURL", ""),
                }
            )
    return fallback[:max_results]


def format_search_results(query: str, results: list[dict[str, Any]]) -> str:
    """将搜索结果格式化为用户可见的文本。"""
    if not results:
        return f"未找到关于「{query}」的搜索结果"

    lines = [f"🔍 搜索结果: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['snippet']}")
        lines.append(f"   {r['url']}\n")
    return "\n".join(lines)
