"""Multi-LLM provider tests."""
import pytest
from llm.providers import LLMProvider, ProviderRegistry


class MockProvider(LLMProvider):
    def __init__(self, name: str, should_fail: bool = False):
        self._name = name
        self._should_fail = should_fail

    @property
    def name(self) -> str:
        return self._name

    async def chat(self, messages: list, **kwargs) -> str:
        if self._should_fail:
            raise ConnectionError("Provider unavailable")
        return f"Response from {self._name}"

    async def health_check(self) -> bool:
        return not self._should_fail

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


@pytest.mark.asyncio
async def test_provider_primary_success():
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)
    result = await registry.chat([{"role": "user", "content": "hello"}])
    assert result == "Response from primary"


@pytest.mark.asyncio
async def test_provider_failover_to_backup():
    registry = ProviderRegistry()
    primary = MockProvider("primary", should_fail=True)
    backup = MockProvider("backup")
    registry.set_primary(primary)
    registry.add_backup(backup)
    result = await registry.chat([{"role": "user", "content": "hello"}])
    assert result == "Response from backup"


@pytest.mark.asyncio
async def test_provider_all_fail():
    registry = ProviderRegistry()
    primary = MockProvider("primary", should_fail=True)
    backup = MockProvider("backup", should_fail=True)
    registry.set_primary(primary)
    registry.add_backup(backup)
    result = await registry.chat([{"role": "user", "content": "hello"}])
    assert "暂时无法" in result or "错误" in result


@pytest.mark.asyncio
async def test_provider_health_check():
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)
    health = await registry.health_check()
    assert health["primary"] is True


@pytest.mark.asyncio
async def test_provider_token_budget():
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)
    registry.set_budget(1000)
    await registry.chat([{"role": "user", "content": "hello"}])
    stats = registry.budget_stats()
    assert stats["used"] > 0
    assert stats["limit"] == 1000
