"""Read-only link summaries for chat messages and the /read command."""
from __future__ import annotations

import html
import json
import re
import time
from collections import OrderedDict
from html.parser import HTMLParser

from media.net import MediaFetchError, fetch_bytes
from utils.logger import setup_logger

logger = setup_logger("links")

_URL = re.compile(r"https?://[^\s<>\"'，。！？、）；】]+", re.IGNORECASE)
_CHARSET = re.compile(r"charset=([\w-]+)", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>，。；：！？）】」』"
_CACHE_TTL = 600.0
_CACHE_SIZE = 100
_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe"}
_BLOCK_TAGS = {
    "p",
    "br",
    "div",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "header",
    "footer",
}


def extract_urls(text: str, limit: int = 3) -> list[str]:
    seen: list[str] = []
    for match in _URL.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING)
        if url and url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


def _decode(content: bytes, content_type: str) -> str:
    match = _CHARSET.search(content_type or "")
    charset = match.group(1) if match else "utf-8"
    try:
        return content.decode(charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


class _PageExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.meta: dict[str, str] = {}
        self.text: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content", "").strip()
            if name and content:
                self.meta[name] = content
        elif tag in _BLOCK_TAGS:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
        else:
            self.text.append(data)


def _clean_text(parts: list[str], limit: int) -> str:
    collapsed = re.sub(r"[ \t\r\f\v]+", " ", "".join(parts))
    lines = [line.strip() for line in collapsed.splitlines()]
    kept: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if len(line) < 2 or line in seen:
            continue
        seen.add(line)
        kept.append(line)
        if sum(len(item) for item in kept) >= limit:
            break
    return "\n".join(kept)[:limit]


def parse_html(content: bytes, content_type: str = "", *, max_chars: int = 1800) -> str | None:
    page = _PageExtractor()
    try:
        page.feed(_decode(content, content_type))
        page.close()
    except Exception:
        return None
    title = (page.meta.get("og:title") or "".join(page.title) or "").strip()
    description = (
        page.meta.get("og:description")
        or page.meta.get("description")
        or page.meta.get("twitter:description")
        or ""
    ).strip()
    body = _clean_text(page.text, max_chars)
    if not title and not description and not body:
        return None
    lines = []
    if title:
        lines.append(f"标题：{html.unescape(title)[:200]}")
    if description:
        lines.append(f"摘要：{html.unescape(description)[:400]}")
    if body:
        lines.append(f"正文节选：{body}")
    return "\n".join(lines)


async def _bilibili_summary(final_url: str) -> str | None:
    match = re.search(r"bilibili\.com/video/(BV[0-9A-Za-z]+)", final_url)
    if not match:
        return None
    try:
        result = await fetch_bytes(
            f"https://api.bilibili.com/x/web-interface/view?bvid={match.group(1)}",
            max_bytes=300_000,
            total_timeout=8.0,
            attempt_timeout=5.0,
        )
        payload = json.loads(result.content.decode("utf-8", errors="replace"))
        data = payload.get("data") or {}
        if not isinstance(data, dict) or not data.get("title"):
            return None
        owner = data.get("owner") or {}
        duration = int(data.get("duration") or 0)
        lines = [
            f"标题：{data.get('title', '')[:200]}",
            f"UP主：{owner.get('name', '')}",
            f"时长：{duration // 60}分{duration % 60}秒",
        ]
        desc = str(data.get("desc") or "").strip()
        if desc:
            lines.append(f"简介：{desc[:600]}")
        return "\n".join(lines)
    except (MediaFetchError, ValueError, TypeError, OSError):
        return None


async def fetch_link_summary(url: str, *, max_chars: int = 1800) -> str | None:
    """Fetch a public URL and return a readable, SSRF-safe summary."""
    cached = _cache.get(url)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL:
        _cache.move_to_end(url)
        return cached[1]
    try:
        result = await fetch_bytes(
            url,
            max_bytes=1_500_000,
            total_timeout=10.0,
            attempt_timeout=6.0,
        )
    except MediaFetchError as exc:
        logger.info("链接抓取失败 (%s)", exc)
        return None

    final_url = result.final_url
    if "bilibili.com/video/" in final_url:
        summary = await _bilibili_summary(final_url)
        if summary:
            _remember(url, summary)
            return summary

    content_type = result.content_type.lower()
    if "html" in content_type or "xml" in content_type or not content_type:
        summary = parse_html(result.content, content_type, max_chars=max_chars)
    elif "json" in content_type:
        summary = result.content.decode("utf-8", errors="replace")[:max_chars]
    elif content_type.startswith("text/"):
        summary = _decode(result.content, content_type)[:max_chars]
    else:
        summary = f"（{content_type or '未知类型'}内容，未解析）"
    if summary:
        summary = f"链接：{final_url}\n{summary}"
        _remember(url, summary)
    return summary


def _remember(url: str, summary: str) -> None:
    _cache[url] = (time.monotonic(), summary)
    _cache.move_to_end(url)
    while len(_cache) > _CACHE_SIZE:
        _cache.popitem(last=False)
