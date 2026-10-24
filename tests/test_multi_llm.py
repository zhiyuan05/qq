"""统一 LLM Client/Provider 契约与故障注入测试。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from llm.client import InstrumentedLLMClient, LLMClient
from llm.providers import INTERRUPTED_MESSAGE, LLMProvider, ProviderRegistry
from utils.config import LLMConfig


class MockProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        *,
        should_fail: bool = False,
        stream_chunks: list[str] | None = None,
        fail_stream_before: bool = False,
        fail_stream_after: int | None = None,
        health_error: bool = False,
        count_error: bool = False,
    ) -> None:
        self._name = name
        self.should_fail = should_fail
        self.stream_chunks = stream_chunks or [f"Stream from {name}"]
        self.fail_stream_before = fail_stream_before
        self.fail_stream_after = fail_stream_after
        self.health_error = health_error
        self.count_error = count_error
        self.chat_calls = 0
        self.stream_calls = 0
        self.close_calls = 0
        self.last_kwargs: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._name

    async def chat(self, messages: list, **kwargs: Any) -> str:
        self.chat_calls += 1
        self.last_kwargs = kwargs
        if self.should_fail:
            raise ConnectionError("Provider unavailable")
        return f"Response from {self._name}"

    async def chat_stream(self, messages: list, **kwargs: Any):
        self.stream_calls += 1
        self.last_kwargs = kwargs
        if self.fail_stream_before:
            raise ConnectionError("Stream unavailable")
        for index, chunk in enumerate(self.stream_chunks, start=1):
            yield chunk
            if self.fail_stream_after == index:
                raise ConnectionError("Stream interrupted")

    async def health_check(self) -> bool:
        if self.health_error:
            raise RuntimeError("health endpoint failed")
        return not self.should_fail

    def count_tokens(self, text: str) -> int:
        if self.count_error:
            raise RuntimeError("tokenizer unavailable")
        return len(text) // 4

    async def aclose(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_provider_primary_success():
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)

    result = await registry.chat([{"role": "user", "content": "hello"}])

    assert result == "Response from primary"
    assert registry.last_provider == "primary"
    assert registry.last_call_succeeded is True


@pytest.mark.asyncio
async def test_provider_failover_to_backup():
    registry = ProviderRegistry()
    primary = MockProvider("primary", should_fail=True)
    backup = MockProvider("backup")
    registry.set_primary(primary)
    registry.add_backup(backup)

    result = await registry.chat([{"role": "user", "content": "hello"}])

    assert result == "Response from backup"
    assert primary.chat_calls == backup.chat_calls == 1
    assert registry.last_provider == "backup"


@pytest.mark.asyncio
async def test_provider_all_fail_returns_stable_friendly_response():
    registry = ProviderRegistry()
    registry.set_primary(MockProvider("primary", should_fail=True))
    registry.add_backup(MockProvider("backup", should_fail=True))

    result = await registry.chat([{"role": "user", "content": "hello"}])

    assert "暂时无法" in result
    assert registry.last_call_succeeded is False
    assert registry.last_provider is None


@pytest.mark.asyncio
async def test_stream_fails_over_only_before_first_chunk():
    registry = ProviderRegistry()
    primary = MockProvider("primary", fail_stream_before=True)
    backup = MockProvider("backup", stream_chunks=["back", "up"])
    registry.set_primary(primary)
    registry.add_backup(backup)

    chunks = [chunk async for chunk in registry.chat_stream([{"role": "user", "content": "hello"}])]

    assert chunks == ["back", "up"]
    assert primary.stream_calls == backup.stream_calls == 1
    assert registry.last_provider == "backup"
    assert registry.last_call_succeeded is True


@pytest.mark.asyncio
async def test_stream_interruption_never_splices_a_backup_response():
    registry = ProviderRegistry()
    primary = MockProvider("primary", stream_chunks=["partial"], fail_stream_after=1)
    backup = MockProvider("backup", stream_chunks=["duplicate answer"])
    registry.set_primary(primary)
    registry.add_backup(backup)

    chunks = [chunk async for chunk in registry.chat_stream([{"role": "user", "content": "hello"}])]

    assert chunks == ["partial", INTERRUPTED_MESSAGE]
    assert backup.stream_calls == 0
    assert registry.last_call_succeeded is False
    assert registry.provider_stats()["stream_interruptions"] == 1


@pytest.mark.asyncio
async def test_repeated_failures_put_provider_in_cooldown():
    now = [100.0]
    registry = ProviderRegistry(failure_threshold=2, cooldown_seconds=30, clock=lambda: now[0])
    primary = MockProvider("primary", should_fail=True)
    backup = MockProvider("backup")
    registry.set_primary(primary)
    registry.add_backup(backup)
    messages = [{"role": "user", "content": "hello"}]

    await registry.chat(messages)
    await registry.chat(messages)
    await registry.chat(messages)

    assert primary.chat_calls == 2
    assert backup.chat_calls == 3
    assert registry.provider_stats()["providers"]["primary"]["cooldown_seconds"] == 30

    now[0] += 31
    await registry.chat(messages)
    assert primary.chat_calls == 3


@pytest.mark.asyncio
async def test_provider_health_check_is_concurrent_and_exception_safe():
    registry = ProviderRegistry()
    registry.set_primary(MockProvider("primary", health_error=True))
    registry.add_backup(MockProvider("backup"))

    health = await registry.health_check()

    assert health == {"primary": False, "backup_0": True}


@pytest.mark.asyncio
async def test_provider_token_budget_and_stats():
    registry = ProviderRegistry()
    primary = MockProvider("primary")
    registry.set_primary(primary)
    registry.set_budget(1000)

    await registry.chat([{"role": "user", "content": "hello"}])
    stats = registry.provider_stats()

    assert stats["budget"]["used"] > 0
    assert stats["budget"]["limit"] == 1000
    assert stats["providers"]["primary"]["successes"] == 1


@pytest.mark.asyncio
async def test_token_meter_failure_never_drops_a_successful_response():
    registry = ProviderRegistry()
    registry.set_primary(MockProvider("primary", count_error=True))

    result = await registry.chat([{"role": "user", "content": "hello"}])

    assert result == "Response from primary"
    assert registry.last_call_succeeded is True


@pytest.mark.asyncio
async def test_registry_closes_each_provider_once_and_rejects_new_calls():
    registry = ProviderRegistry()
    provider = MockProvider("shared")
    registry.set_primary(provider)
    registry.add_backup(provider)

    await registry.aclose()
    await registry.aclose()

    assert provider.close_calls == 1
    with pytest.raises(RuntimeError, match="已关闭"):
        await registry.chat([])


@pytest.mark.asyncio
async def test_llm_client_applies_defaults_to_normal_and_streaming_calls():
    registry = ProviderRegistry()
    provider = MockProvider("primary", stream_chunks=["ok"])
    registry.set_primary(provider)
    client = LLMClient(registry, max_tokens=123, temperature=0.2)

    await client.chat([])
    assert provider.last_kwargs == {"max_tokens": 123, "temperature": 0.2}
    assert [chunk async for chunk in client.chat_stream([])] == ["ok"]
    assert provider.last_kwargs == {"max_tokens": 123, "temperature": 0.2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "expected_prefix"),
    [
        ("deepseek", "deepseek/"),
        ("openai_compatible", "openai_compat/"),
        ("ollama", "ollama/"),
    ],
)
async def test_llm_client_factory_honors_configured_provider(provider_name, expected_prefix):
    base_url = "http://localhost:11434" if provider_name == "ollama" else "https://example.com/v1"
    config = LLMConfig(
        provider=provider_name,
        api_key="test-key",
        base_url=base_url,
        model="primary-model",
        fallback_model="backup-model",
    )
    client = LLMClient.from_config(config)
    try:
        assert len(client.registry.providers) == 2
        assert all(provider.name.startswith(expected_prefix) for provider in client.registry.providers)
        primary, backup = client.registry.providers
        assert primary._client is backup._client
        assert primary._owns_client is True
        assert backup._owns_client is False
    finally:
        await client.aclose()


def test_llm_provider_alias_and_unknown_provider_validation():
    assert LLMConfig(provider="openai").provider == "openai_compatible"
    with pytest.raises(ValueError, match="llm.provider"):
        LLMConfig(provider="unknown")


@pytest.mark.asyncio
async def test_instrumented_client_balances_early_closed_stream():
    provider = MockProvider("primary", stream_chunks=["one", "two"])
    outcomes: list[bool | None] = []
    active = 0

    def started():
        nonlocal active
        active += 1

    def finished(outcome):
        nonlocal active
        active -= 1
        outcomes.append(outcome)

    client = InstrumentedLLMClient(provider, on_started=started, on_finished=finished)
    stream = client.chat_stream([])
    assert await anext(stream) == "one"
    await stream.aclose()

    assert active == 0
    assert outcomes == [None]


@pytest.mark.asyncio
async def test_concurrent_calls_keep_task_local_outcomes():
    successful_call_done = asyncio.Event()
    failed_call_done = asyncio.Event()

    class ConcurrentProvider(MockProvider):
        async def chat(self, messages: list, **kwargs: Any) -> str:
            content = messages[0]["content"]
            if content == "success":
                successful_call_done.set()
                return "ok"
            await successful_call_done.wait()
            raise ConnectionError("failed")

    registry = ProviderRegistry()
    registry.set_primary(ConcurrentProvider("shared"))

    async def successful_call():
        await registry.chat([{"role": "user", "content": "success"}])
        await failed_call_done.wait()
        return registry.last_call_succeeded, registry.last_provider

    async def failed_call():
        await successful_call_done.wait()
        await registry.chat([{"role": "user", "content": "fail"}])
        failed_call_done.set()
        return registry.last_call_succeeded, registry.last_provider

    success_result, failure_result = await asyncio.gather(successful_call(), failed_call())

    assert success_result == (True, "shared")
    assert failure_result == (False, None)
