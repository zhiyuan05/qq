"""Mood / energy / affinity state tests。"""
import pytest

from core.relationship import (
    RelationshipState,
    RelationshipStore,
    describe_relationship,
    update_relationship,
)
from memory.entity_memory import EntityMemory


def test_positive_message_improves_mood_and_affinity():
    state = RelationshipState(mood=0.0, energy=0.8, affinity=2.0, last_active=1000.0)

    updated = update_relationship(state, "今天好开心，谢谢你", now=1060.0)

    assert updated.mood > state.mood
    assert updated.affinity > state.affinity
    assert updated.last_active == 1060.0
    assert updated.turns == 1


def test_negative_message_lowers_mood_without_breaking_affinity():
    state = RelationshipState(mood=0.2, energy=0.8, affinity=2.0, last_active=1000.0)

    updated = update_relationship(state, "今天很烦，压力好大", now=1060.0)

    assert updated.mood < state.mood
    assert updated.affinity >= 1.9


def test_offline_time_recovers_energy():
    state = RelationshipState(mood=0.0, energy=0.2, affinity=2.0, last_active=1000.0)

    updated = update_relationship(state, "嗯", now=1000.0 + 3 * 3600)

    assert updated.energy > state.energy


def test_description_reflects_state():
    tired = describe_relationship(RelationshipState(mood=0.5, energy=0.2, affinity=7.0))
    low = describe_relationship(RelationshipState(mood=-0.5, energy=0.6, affinity=2.0))

    assert "有点累" in tired
    assert "亲近" in tired or "默契" in tired
    assert "低落" in low
    assert "接住" in low


@pytest.mark.asyncio
async def test_relationship_state_round_trips_through_entity_memory(tmp_path):
    memory = EntityMemory(db_path=str(tmp_path / "relationship.db"))
    store = RelationshipStore(memory)
    state = RelationshipState(mood=0.4, energy=0.6, affinity=4.2, last_active=123.0, turns=5)

    await store.save("qq:1", state)
    loaded = await store.load("qq:1")

    assert loaded.mood == pytest.approx(0.4)
    assert loaded.energy == pytest.approx(0.6)
    assert loaded.affinity == pytest.approx(4.2)
    assert loaded.turns == 5


@pytest.mark.asyncio
async def test_missing_state_defaults_cleanly(tmp_path):
    memory = EntityMemory(db_path=str(tmp_path / "relationship.db"))
    store = RelationshipStore(memory)

    loaded = await store.load("qq:missing")

    assert loaded.mood == 0.0
    assert loaded.energy == 0.7
    assert loaded.affinity == 1.0
