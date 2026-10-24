"""学习系统测试"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from learning.evolution import EvolutionManager
from learning.scheduler import LearningScheduler


class TestEvolutionManager:
    def test_backup_card(self):
        em = EvolutionManager()
        result = em.backup_card("nonexistent")
        assert result is not None

    def test_save_report(self):
        em = EvolutionManager()
        report = {
            "character_card": "test_card",
            "conversations_analyzed": 10,
            "traits_learned": {"summary": "测试学习"},
            "summary": "测试",
        }
        em.save_report("test_card", report)

    def test_get_evolution_history_empty(self):
        em = EvolutionManager()
        history = em.get_evolution_history("nonexistent")
        assert isinstance(history, list)

    def test_reset_card_nonexistent(self):
        em = EvolutionManager()
        result = em.reset_card("nonexistent")
        assert result is not None


class TestLearningScheduler:
    @pytest.mark.asyncio
    async def test_on_conversation_end_triggers_learn(self):
        scheduler = LearningScheduler(trigger_interval=3)
        callback = AsyncMock()
        scheduler.set_learn_callback(callback)
        scheduler.on_conversation_end()
        scheduler.on_conversation_end()
        scheduler.on_conversation_end()
        await asyncio.sleep(0.1)
        assert callback.called

    @pytest.mark.asyncio
    async def test_start_stop(self):
        scheduler = LearningScheduler()
        scheduler.start()
        await asyncio.sleep(0.1)
        scheduler.stop()
        scheduler.stop()
        assert await scheduler.drain() is True

    @pytest.mark.asyncio
    async def test_stop_drains_owned_learning_task_and_rejects_new_trigger(self):
        scheduler = LearningScheduler(trigger_interval=1, shutdown_timeout=0.2)
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def callback():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()

        scheduler.set_learn_callback(callback)
        scheduler.on_conversation_end()
        await entered.wait()
        scheduler.stop()
        scheduler.on_conversation_end()

        draining = asyncio.create_task(scheduler.drain())
        await asyncio.sleep(0)
        assert not draining.done()

        release.set()
        assert await draining is True
        assert calls == 1
        assert not scheduler._tasks

    @pytest.mark.asyncio
    async def test_learning_drain_timeout_cancels_owned_task(self):
        scheduler = LearningScheduler(
            trigger_interval=1,
            shutdown_timeout=0.01,
            cancel_timeout=0.1,
        )
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def callback():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        scheduler.set_learn_callback(callback)
        scheduler.on_conversation_end()
        await entered.wait()
        scheduler.stop()

        assert await asyncio.wait_for(scheduler.drain(), timeout=0.5) is False
        assert cancelled.is_set()
        assert scheduler._drain_timed_out is True
        assert not scheduler._tasks


class TestLearningAnalyzer:
    @pytest.mark.asyncio
    async def test_analyze_empty(self):
        from learning.analyzer import LearningAnalyzer
        analyzer = LearningAnalyzer(llm_client=None)
        result = await analyzer.analyze([])
        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_conversations(self):
        from learning.analyzer import LearningAnalyzer
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = '{"summary": "测试学习", "traits": ["友好"]}'
        analyzer = LearningAnalyzer(llm_client=mock_llm)
        conversations = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你的？"},
        ]
        result = await analyzer.analyze(conversations)
        assert result is not None
