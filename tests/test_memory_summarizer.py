"""对话摘要器测试"""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_summarize_without_llm():
    from memory.summarizer import ConversationSummarizer
    summarizer = ConversationSummarizer(llm_client=None)
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么需要帮忙的吗？"},
    ]
    result = await summarizer.summarize(messages)
    assert result is not None
    assert len(result) > 0


@pytest.mark.asyncio
async def test_summarize_with_llm():
    from memory.summarizer import ConversationSummarizer
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = "用户问候了助手，助手友好回应。"
    summarizer = ConversationSummarizer(llm_client=mock_llm)
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么需要帮忙的吗？"},
    ]
    result = await summarizer.summarize(messages)
    assert "问候" in result or "友好" in result


def test_format_messages():
    from memory.summarizer import ConversationSummarizer
    summarizer = ConversationSummarizer()
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    formatted = summarizer._format_messages(messages)
    assert "用户" in formatted
    assert "助手" in formatted
