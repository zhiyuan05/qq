"""统一的高层 LLM 客户端与 Engine 可观测装饰器。"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from llm.providers import ProviderRegistry
from llm.providers.deepseek import DeepSeekProvider
from llm.providers.ollama import OllamaProvider
from llm.providers.openai_compat import OpenAICompatProvider


class ChatClient(Protocol):
    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str: ...

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]: ...


def _provider_from_config(config: Any, model: str):
    provider_name = config.provider
    if provider_name == "deepseek":
        return DeepSeekProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=model,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
    if provider_name == "openai_compatible":
        return OpenAICompatProvider(
            api_key=config.api_key,
            base_url=config.base_url,
            model=model,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
    if provider_name == "ollama":
        return OllamaProvider(
            base_url=config.base_url,
            model=model,
            timeout=config.timeout,
        )
    raise ValueError(f"不支持的 LLM Provider: {provider_name}")


class LLMClient:
    """Engine 使用的唯一高层客户端。

    默认参数在这里统一注入，ProviderRegistry 负责普通与流式调用的相同故障
    转移顺序。Provider 不再被上层通过私有字段直接访问。
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> None:
        self.registry = registry
        self.max_tokens = max_tokens
        self.temperature = temperature

    @classmethod
    def from_config(cls, config: Any) -> "LLMClient":
        registry = ProviderRegistry(
            failure_threshold=config.provider_failure_threshold,
            cooldown_seconds=config.provider_cooldown_seconds,
        )
        primary = _provider_from_config(config, config.model)
        registry.set_primary(primary)
        if config.fallback_model and config.fallback_model != config.model:
            registry.add_backup(primary.with_model(config.fallback_model))
        registry.set_budget(config.daily_token_limit)
        return cls(
            registry,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )

    @property
    def last_call_succeeded(self) -> bool | None:
        return self.registry.last_call_succeeded

    @property
    def last_usage(self) -> Any:
        return self.registry.last_usage

    @property
    def last_model(self) -> str:
        return self.registry.last_model

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        kwargs.setdefault("max_tokens", self.max_tokens)
        kwargs.setdefault("temperature", self.temperature)
        return await self.registry.chat(messages, **kwargs)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        kwargs.setdefault("max_tokens", self.max_tokens)
        kwargs.setdefault("temperature", self.temperature)
        async for chunk in self.registry.chat_stream(messages, **kwargs):
            yield chunk

    async def health_check(self) -> dict[str, bool]:
        return await self.registry.health_check()

    def budget_stats(self) -> dict[str, float | int]:
        return self.registry.budget_stats()

    def provider_stats(self) -> dict[str, Any]:
        return self.registry.provider_stats()

    async def aclose(self) -> None:
        await self.registry.aclose()


class DeepSeekClient(LLMClient):
    """保留旧构造参数的薄兼容入口；所有行为均由 ``LLMClient`` 实现。"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        fallback_model: str = "deepseek-reasoner",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        registry = ProviderRegistry()
        primary = DeepSeekProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )
        registry.set_primary(primary)
        if fallback_model and fallback_model != model:
            registry.add_backup(primary.with_model(fallback_model))
        super().__init__(registry, max_tokens=max_tokens, temperature=temperature)


class InstrumentedLLMClient:
    """不改写被包装对象的方法，为 Engine 统一记录调用生命周期。"""

    def __init__(
        self,
        delegate: ChatClient,
        *,
        on_started: Callable[[], None],
        on_finished: Callable[[bool | None], None],
    ) -> None:
        self.delegate = delegate
        self._on_started = on_started
        self._on_finished = on_finished

    @property
    def last_call_succeeded(self) -> bool | None:
        outcome = getattr(self.delegate, "last_call_succeeded", None)
        return outcome if isinstance(outcome, bool) or outcome is None else None

    @property
    def last_usage(self) -> Any:
        return getattr(self.delegate, "last_usage", None)

    @property
    def last_model(self) -> str:
        return getattr(self.delegate, "last_model", "") or ""

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        self._on_started()
        try:
            result = await self.delegate.chat(messages, **kwargs)
        except asyncio.CancelledError:
            self._on_finished(None)
            raise
        except Exception:
            self._on_finished(False)
            raise
        self._on_finished(self.last_call_succeeded is not False)
        return result

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        self._on_started()
        finished = False
        try:
            async for chunk in self.delegate.chat_stream(messages, **kwargs):
                yield chunk
        except asyncio.CancelledError:
            self._on_finished(None)
            finished = True
            raise
        except Exception:
            self._on_finished(False)
            finished = True
            raise
        else:
            self._on_finished(self.last_call_succeeded is not False)
            finished = True
        finally:
            if not finished:
                # 消费者提前停止迭代不是上游故障，但必须释放 pending 计数。
                self._on_finished(None)

    async def health_check(self) -> dict[str, bool]:
        method = getattr(self.delegate, "health_check", None)
        return await method() if method else {}

    def budget_stats(self) -> dict[str, Any]:
        method = getattr(self.delegate, "budget_stats", None)
        return method() if method else {}

    def provider_stats(self) -> dict[str, Any]:
        method = getattr(self.delegate, "provider_stats", None)
        return method() if method else {}

    async def aclose(self) -> None:
        method = getattr(self.delegate, "aclose", None) or getattr(self.delegate, "close", None)
        if not method:
            return
        result = method()
        if inspect.isawaitable(result):
            await result
