import json

import pytest

from database.db import dispose_db, init_db
from learning.persona_evolution import PersonaEvolutionService
from memory.persona import PersonaStore
from utils.config import PersonaConfig


class _FakeArchive:
    def __init__(self, rows):
        self.rows = rows

    async def fetch_after(self, session_id, after_id, limit=200):
        return [row for row in self.rows if row["id"] > after_id][:limit]


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.response


def _rows(count: int):
    return [
        {
            "id": index + 1,
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"消息 {index}",
            "attachments_json": "[]",
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_evolution_applies_valid_traits_and_rejects_unsafe(tmp_path):
    db_path = str(tmp_path / "evolve.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig())
    response = json.dumps(
        {
            "traits": [
                {
                    "scope": "user",
                    "category": "speech_style",
                    "trait_key": "语气",
                    "value": "喜欢用短句",
                    "confidence": 0.8,
                    "evidence": "多次短句回复",
                },
                {
                    "scope": "user",
                    "category": "preference",
                    "trait_key": "密码",
                    "value": "密码是 123456",
                    "confidence": 0.9,
                    "evidence": "用户说了密码",
                },
            ]
        }
    )
    llm = _FakeLLM(response)
    service = PersonaEvolutionService(
        _FakeArchive(_rows(30)),
        store,
        lambda: llm,
        PersonaConfig(evolve_after_messages=30),
    )
    try:
        result = await service.evolve_session("qq:1", "qq:1", base_persona="基础人设")
        assert result["extracted"] == 1
        assert result["applied"] == 1
        traits = await store.active_traits("user", "qq:1")
        assert [trait.value for trait in traits] == ["喜欢用短句"]
        assert await store.get_cursor("qq:1") == 30
        assert "不可信" in llm.calls[0][0]["content"]
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_evolution_waits_for_enough_new_messages(tmp_path):
    db_path = str(tmp_path / "evolve-wait.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig())
    llm = _FakeLLM('{"traits": []}')
    service = PersonaEvolutionService(
        _FakeArchive(_rows(10)),
        store,
        lambda: llm,
        PersonaConfig(evolve_after_messages=30),
    )
    try:
        result = await service.evolve_session("qq:1", "qq:1")
        assert result["extracted"] == 0
        assert llm.calls == []
        assert await store.get_cursor("qq:1") == 0

        forced = await service.evolve_session("qq:1", "qq:1", force=True)
        assert forced["extracted"] == 0
        assert len(llm.calls) == 1
        assert await store.get_cursor("qq:1") == 10
    finally:
        await dispose_db(db_path)
