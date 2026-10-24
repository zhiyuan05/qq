import pytest

from core import links
from media.net import FetchResult


def test_extract_urls_strips_punctuation_and_dedupes():
    text = "看这个 https://example.com/a。还有 https://example.com/a，以及 https://example.com/b?x=1"
    assert links.extract_urls(text) == [
        "https://example.com/a",
        "https://example.com/b?x=1",
    ]
    assert links.extract_urls(text, limit=1) == ["https://example.com/a"]


def test_parse_html_reads_title_meta_and_body():
    page = b"""
    <html><head>
      <title>Fallback</title>
      <meta property="og:title" content="Hello World">
      <meta name="description" content="A short summary">
      <script>ignored()</script>
    </head><body><p>First paragraph.</p><p>Second paragraph.</p></body></html>
    """
    summary = links.parse_html(page, "text/html; charset=utf-8")
    assert "标题：Hello World" in summary
    assert "摘要：A short summary" in summary
    assert "First paragraph." in summary
    assert "ignored" not in summary


def test_parse_html_rejects_empty_pages():
    assert links.parse_html(b"<html><body></body></html>", "text/html") is None


@pytest.mark.asyncio
async def test_fetch_link_summary_caches_and_formats(monkeypatch):
    calls = []

    async def fake_fetch(url, **kwargs):
        calls.append(url)
        return FetchResult(
            content="<title>Example</title><p>Body text</p>".encode(),
            content_type="text/html; charset=utf-8",
            final_url=url,
        )

    links._cache.clear()
    monkeypatch.setattr(links, "fetch_bytes", fake_fetch)
    first = await links.fetch_link_summary("https://example.com/page")
    second = await links.fetch_link_summary("https://example.com/page")

    assert first == second
    assert "标题：Example" in first
    assert "Body text" in first
    assert calls == ["https://example.com/page"]
