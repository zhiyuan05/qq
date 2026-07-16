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
