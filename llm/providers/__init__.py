"""统一的 LLM Provider 契约与故障转移路由。"""
from __future__ import annotations

import asyncio
import contextvars
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from utils.logger import setup_logger

logger = setup_logger("llm.providers")

UNAVAILABLE_MESSAGE = "抱歉，我暂时无法回复，请稍后再试。"
INTERRUPTED_MESSAGE = "\n\n抱歉，回复生成过程中断，请稍后重试。"
_UNSET = object()


class LLMProvider(ABC):
    """单个模型服务的最小契约。

    Provider 只封装上游协议；重试链、冷却和统计由 ``ProviderRegistry`` 负责。
    未原生支持流式的 Provider 可以继承默认实现，自动降级为单块输出。
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str: ...

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        yield await self.chat(messages, **kwargs)

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    def count_tokens(self, text: str) -> int: ...

    async def aclose(self) -> None:
        """释放连接池；无外部资源的 Provider 无需覆盖。"""


@dataclass
class _ProviderState:
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0


class ProviderRegistry:
    """按优先级调用 Provider，并隔离连续失败的上游。"""

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold 必须大于 0")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds 不能为负数")
        self._primary: LLMProvider | None = None
        self._backups: list[LLMProvider] = []
        self._states: dict[int, _ProviderState] = {}
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._daily_tokens_used = 0
        self._daily_token_limit = 500000
        self._call_outcome: contextvars.ContextVar[bool | None | object] = contextvars.ContextVar(
            f"llm_call_outcome_{id(self)}", default=_UNSET
        )
        self._call_provider: contextvars.ContextVar[str | None | object] = contextvars.ContextVar(
            f"llm_call_provider_{id(self)}", default=_UNSET
        )
        self._last_call_succeeded: bool | None = None
        self._last_provider: str | None = None
        self.last_usage: Any = None
        self.last_model: str = ""
        self.stream_interruptions = 0
        self._closed = False

    def set_primary(self, provider: LLMProvider) -> None:
        self._ensure_open()
        self._primary = provider
        self._states.setdefault(id(provider), _ProviderState())

    @property
    def primary(self) -> LLMProvider | None:
        return self._primary

    @property
    def last_call_succeeded(self) -> bool | None:
        outcome = self._call_outcome.get()
        return self._last_call_succeeded if outcome is _UNSET else outcome

    @property
    def last_provider(self) -> str | None:
        provider = self._call_provider.get()
        return self._last_provider if provider is _UNSET else provider

    @property
    def providers(self) -> tuple[LLMProvider, ...]:
        ordered = ([self._primary] if self._primary else []) + self._backups
        return tuple(provider for provider in ordered if provider is not None)

    def add_backup(self, provider: LLMProvider) -> None:
        self._ensure_open()
        self._backups.append(provider)
        self._states.setdefault(id(provider), _ProviderState())

    def set_budget(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("daily token limit 必须大于 0")
        self._daily_token_limit = limit

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ProviderRegistry 已关闭")

    def _eligible(self) -> list[LLMProvider]:
        now = self._clock()
        return [
            provider
            for provider in self.providers
            if self._states[id(provider)].cooldown_until <= now
        ]

    def _record_success(self, provider: LLMProvider) -> None:
        state = self._states[id(provider)]
        state.successes += 1
        state.consecutive_failures = 0
        state.cooldown_until = 0.0
        self._set_call_result(True, provider.name)

    def _set_call_result(self, succeeded: bool | None, provider: str | None) -> None:
        self._call_outcome.set(succeeded)
        self._call_provider.set(provider)
        self._last_call_succeeded = succeeded
        self._last_provider = provider

    def _record_failure(self, provider: LLMProvider, exc: Exception) -> None:
        state = self._states[id(provider)]
        state.failures += 1
        state.consecutive_failures += 1
        if state.consecutive_failures >= self._failure_threshold:
            state.cooldown_until = self._clock() + self._cooldown_seconds
        logger.warning("Provider %s 失败: %s", provider.name, exc)

    def _record_usage(self, provider: LLMProvider, messages: list[dict[str, Any]], result: str) -> None:
        try:
            self._daily_tokens_used += provider.count_tokens(str(messages)) + provider.count_tokens(result)
        except Exception as exc:
            # 计量属于可观测性，不能反向让已经成功的模型调用失败。
            logger.warning("Provider %s token 计量失败: %s", provider.name, exc)

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        self._ensure_open()
        self._call_provider.set(None)
        for provider in self._eligible():
            try:
                result = await provider.chat(messages, **kwargs)
            except Exception as exc:
                self._record_failure(provider, exc)
                continue
            self._record_usage(provider, messages, result)
            self._record_success(provider)
            self.last_usage = getattr(provider, "last_usage", None)
            self.last_model = getattr(provider, "last_model", "") or kwargs.get("model", "")
            return result

        logger.error("所有 LLM Provider 均不可用或处于冷却状态")
        self._set_call_result(False, None)
        return UNAVAILABLE_MESSAGE

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式故障转移只允许发生在首个输出块之前。

        已向用户发送内容后切换 Provider 会产生重复或语义断裂，因此中途失败时
        返回明确的中断后缀，并把本次调用记为失败。
        """

        self._ensure_open()
        self._call_provider.set(None)
        for provider in self._eligible():
            emitted = False
            result_parts: list[str] = []
            try:
                async for chunk in provider.chat_stream(messages, **kwargs):
                    if not chunk:
                        continue
                    emitted = True
                    result_parts.append(chunk)
                    yield chunk
            except Exception as exc:
                self._record_failure(provider, exc)
                if emitted:
                    self._record_usage(provider, messages, "".join(result_parts))
                    self.stream_interruptions += 1
                    self._set_call_result(False, provider.name)
                    yield INTERRUPTED_MESSAGE
                    return
                continue

            result = "".join(result_parts)
            self._record_usage(provider, messages, result)
            self._record_success(provider)
            return

        logger.error("所有 LLM Provider 的流式调用均不可用或处于冷却状态")
        self._set_call_result(False, None)
        yield UNAVAILABLE_MESSAGE

    async def health_check(self) -> dict[str, bool]:
        """并发检查所有上游，单个 Provider 的异常不影响完整结果。"""

        providers = self.providers
        checks = await asyncio.gather(
            *(provider.health_check() for provider in providers),
            return_exceptions=True,
        )
        result: dict[str, bool] = {}
        for index, (provider, check) in enumerate(zip(providers, checks, strict=True)):
            backup_index = index - 1 if self._primary else index
            key = "primary" if index == 0 and provider is self._primary else f"backup_{backup_index}"
            result[key] = bool(check) if not isinstance(check, BaseException) else False
        return result

    def budget_stats(self) -> dict[str, float | int]:
        return {
            "used": self._daily_tokens_used,
            "limit": self._daily_token_limit,
            "percent": self._daily_tokens_used / max(self._daily_token_limit, 1) * 100,
        }

    def provider_stats(self) -> dict[str, Any]:
        now = self._clock()
        providers: dict[str, Any] = {}
        for provider in self.providers:
            state = self._states[id(provider)]
            providers[provider.name] = {
                "successes": state.successes,
                "failures": state.failures,
                "consecutive_failures": state.consecutive_failures,
                "cooldown_seconds": round(max(0.0, state.cooldown_until - now), 3),
            }
        return {
            "providers": providers,
            "last_provider": self._last_provider,
            "last_call_succeeded": self._last_call_succeeded,
            "stream_interruptions": self.stream_interruptions,
            "budget": self.budget_stats(),
        }

    def reset_daily_budget(self) -> None:
        self._daily_tokens_used = 0

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        unique: list[LLMProvider] = []
        seen: set[int] = set()
        for provider in self.providers:
            if id(provider) not in seen:
                seen.add(id(provider))
                unique.append(provider)
        results = await asyncio.gather(*(provider.aclose() for provider in unique), return_exceptions=True)
        for provider, result in zip(unique, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("关闭 Provider %s 失败: %s", provider.name, result)
