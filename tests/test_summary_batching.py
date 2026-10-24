import pytest

from core.engine import Engine
from core.session import Session
from utils.config import AppConfig


class _FakeArchive:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_after(self, session_id, after_id, limit=200):
        return [row for row in self.rows if row["id"] > after_id][:limit]


class _FakeSummarizer:
    def __init__(self):
        self.calls = []

    async def summarize(self, messages):
        self.calls.append(messages)
        return f"summary-{len(self.calls)}"


def _config() -> AppConfig:
    return AppConfig(
        memory={"long_term": {"enabled": False}, "entity": {"auto_extract": False}},
        rate_limit={"enabled": False},
        stickers={"enabled": False},
        persona={"enabled": False},
        usage={"enabled": False},
    )


@pytest.mark.asyncio
async def test_summary_advances_in_batches_without_skipping_rows():
    rows = [
        {"id": index + 1, "role": "user" if index % 2 == 0 else "assistant", "content": f"m{index}"}
        for index in range(100)
    ]
    archive = _FakeArchive(rows)
    summarizer = _FakeSummarizer()
    engine = Engine(_config(), archive_store=archive, summarizer=summarizer)
    session = Session("qq:1", "1", "qq")

    await engine._summarize_session(session)
    assert session.summary_until_id == 40
    assert len(summarizer.calls) == 1
    assert len(summarizer.calls[0]) == 40

    await engine._summarize_session(session)
    assert session.summary_until_id == 80
    assert len(summarizer.calls) == 2
    # The previous summary is carried into the next batch.
    assert summarizer.calls[1][0]["content"].startswith("之前的对话摘要")

    await engine._summarize_session(session)
    assert session.summary_until_id == 100
    assert len(summarizer.calls) == 3

    await engine._summarize_session(session)
    assert len(summarizer.calls) == 3
