import pytest

from database.db import dispose_db, init_db
from memory.persona import PersonaStore, TraitCandidate
from utils.config import PersonaConfig


def _candidate(value: str, confidence: float, *, scope: str = "user", key: str = "称呼") -> TraitCandidate:
    return TraitCandidate(
        scope=scope,
        category="relationship",
        trait_key=key,
        value=value,
        confidence=confidence,
        evidence="用户多次回应",
    )


@pytest.mark.asyncio
async def test_user_trait_merges_and_activates(tmp_path):
    db_path = str(tmp_path / "persona.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig())
    try:
        await store.apply_candidates([_candidate("叫我小明", 0.7)], user_key="qq:1", session_id="qq:1")
        traits = await store.active_traits("user", "qq:1")
        assert len(traits) == 1
        assert traits[0].value == "叫我小明"
        assert traits[0].confidence == pytest.approx(0.7)

        await store.apply_candidates([_candidate("叫我小明", 0.7)], user_key="qq:1", session_id="qq:2")
        traits = await store.active_traits("user", "qq:1")
        assert len(traits) == 1
        assert traits[0].confidence == pytest.approx(0.91)
        assert set(traits[0].source_sessions) == {"qq:1", "qq:2"}
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_conflicting_value_replaces_only_with_clear_margin(tmp_path):
    db_path = str(tmp_path / "persona-conflict.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig())
    try:
        await store.apply_candidates([_candidate("叫我小明", 0.6)], user_key="qq:1", session_id="qq:1")
        await store.apply_candidates([_candidate("叫我老板", 0.7)], user_key="qq:1", session_id="qq:2")
        active = await store.active_traits("user", "qq:1")
        assert [trait.value for trait in active] == ["叫我小明"]

        await store.apply_candidates([_candidate("叫我陛下", 0.9)], user_key="qq:1", session_id="qq:3")
        active = await store.active_traits("user", "qq:1")
        assert [trait.value for trait in active] == ["叫我陛下"]
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_global_trait_requires_two_sessions_and_admin_approval(tmp_path):
    db_path = str(tmp_path / "persona-global.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig(auto_approve_global=False))
    try:
        await store.apply_candidates([_candidate("少用敬语", 0.9, scope="global")], user_key="qq:1", session_id="qq:1")
        candidates = await store.show("global", "global")
        assert candidates and candidates[0].status == "candidate"
        assert await store.approve("global", "global", candidates[0].id) is False

        await store.apply_candidates([_candidate("少用敬语", 0.9, scope="global")], user_key="qq:2", session_id="qq:2")
        candidates = await store.show("global", "global")
        assert await store.approve("global", "global", candidates[0].id) is True
        assert [trait.value for trait in await store.active_traits("global", "global")] == ["少用敬语"]
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_global_trait_auto_approves_after_two_sessions(tmp_path):
    db_path = str(tmp_path / "persona-global-auto.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig(auto_approve_global=True))
    try:
        await store.apply_candidates([_candidate("少用敬语", 0.9, scope="global")], user_key="qq:1", session_id="qq:1")
        assert await store.active_traits("global", "global") == []

        await store.apply_candidates([_candidate("少用敬语", 0.9, scope="global")], user_key="qq:2", session_id="qq:2")
        active = await store.active_traits("global", "global")
        assert [trait.value for trait in active] == ["少用敬语"]
        assert active[0].confidence >= 0.75
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_forget_and_rollback(tmp_path):
    db_path = str(tmp_path / "persona-history.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig())
    try:
        await store.apply_candidates([_candidate("叫我小明", 0.8)], user_key="qq:1", session_id="qq:1")
        await store.apply_candidates([_candidate("喜欢猫", 0.8, key="宠物")], user_key="qq:1", session_id="qq:2")
        assert len(await store.active_traits("user", "qq:1")) == 2

        assert await store.forget("user", "qq:1", "小明") == 1
        assert [trait.value for trait in await store.active_traits("user", "qq:1")] == ["喜欢猫"]

        assert await store.rollback("user", "qq:1", 1) is True
        assert {trait.value for trait in await store.active_traits("user", "qq:1")} == {"叫我小明", "喜欢猫"}
    finally:
        await dispose_db(db_path)


@pytest.mark.asyncio
async def test_injectable_traits_wrap_only_active_values(tmp_path):
    db_path = str(tmp_path / "persona-inject.db")
    await init_db(db_path)
    store = PersonaStore(db_path, config=PersonaConfig())
    try:
        await store.apply_candidates(
            [
                _candidate("叫我小明", 0.8),
                _candidate("喜欢猫", 0.5, key="宠物"),
                _candidate("少用敬语", 0.9, scope="global"),
            ],
            user_key="qq:1",
            session_id="qq:1",
        )
        user_traits, global_traits = await store.injectable_traits("qq:1", "今天猫怎么样")
        assert user_traits == ["relationship: 叫我小明"]
        assert global_traits == []
    finally:
        await dispose_db(db_path)
