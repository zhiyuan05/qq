import pytest

from platforms.models import Platform, UnifiedMessage
from plugins import web_search as web_search_module
from plugins.web_search import WebSearchPlugin
from utils.search import _BingParser


SAMPLE = """
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://example.com/a">示例 <strong>标题</strong></a></h2>
    <div class="b_caption"><p>这是第一条摘要。</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="https://example.com/b">第二条</a></h2>
    <p>第二条摘要</p>
  </li>
  <li class="b_ans">广告</li>
</ol>
"""


def test_bing_parser_extracts_results():
    parser = _BingParser()
    parser.feed(SAMPLE)
    parser.close()
    assert [item["url"] for item in parser.results] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert parser.results[0]["title"] == "示例 标题"
    assert parser.results[0]["snippet"] == "这是第一条摘要。"


@pytest.mark.asyncio
async def test_plugin_injects_private_search_results(monkeypatch):
    async def fake_search(query, max_results=5):
        assert query == "python asyncio"
        return [{"title": "Python", "snippet": "Asyncio docs", "url": "https://example.com"}]

    monkeypatch.setattr(web_search_module, "duckduckgo_search", fake_search)
    plugin = WebSearchPlugin({"enabled": True, "max_results": 2})
    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="1",
        content="搜一下 python asyncio",
        model_content="搜一下 python asyncio",
    )
    result = await plugin.on_message(message)
    assert result is not None
    assert "[联网搜索结果" in result.content
    assert "Asyncio docs" in result.model_content
    assert result.group_id is None


@pytest.mark.asyncio
async def test_plugin_injects_weather_for_city(monkeypatch):
    async def fake_weather(city):
        assert city == "上海"
        return "上海\n现在 20°C，晴"

    async def fail_search(*args, **kwargs):
        raise AssertionError("search should not run for weather")

    monkeypatch.setattr(web_search_module, "weather_report", fake_weather)
    monkeypatch.setattr(web_search_module, "duckduckgo_search", fail_search)
    plugin = WebSearchPlugin({"enabled": True})
    message = UnifiedMessage(
        platform=Platform.QQ,
        user_id="1",
        content="明天上海天气怎么样",
        model_content="明天上海天气怎么样",
    )
    result = await plugin.on_message(message)
    assert result is not None
    assert "现在 20°C" in result.model_content
    assert "[实时天气数据" in result.content


@pytest.mark.asyncio
async def test_plugin_ignores_vague_weather(monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("should not query")

    monkeypatch.setattr(web_search_module, "weather_report", fail)
    plugin = WebSearchPlugin({"enabled": True})
    for content in ("今天天气", "天气怎么样", "天气不错"):
        message = UnifiedMessage(platform=Platform.QQ, user_id="1", content=content)
        assert await plugin.on_message(message) is None


@pytest.mark.asyncio
async def test_plugin_ignores_groups_and_plain_chat(monkeypatch):
    async def fail_search(*args, **kwargs):
        raise AssertionError("search should not run")

    monkeypatch.setattr(web_search_module, "duckduckgo_search", fail_search)
    plugin = WebSearchPlugin({"enabled": True})
    group = UnifiedMessage(platform=Platform.QQ, user_id="1", content="搜一下天气", group_id="9")
    plain = UnifiedMessage(platform=Platform.QQ, user_id="1", content="今天天气不错")
    assert await plugin.on_message(group) is None
    assert await plugin.on_message(plain) is None
