"""LLM provider abstraction with failover support."""
from abc import ABC, abstractmethod
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger("llm_providers")


class LLMProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def chat(self, messages: list, **kwargs) -> str: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    def count_tokens(self, text: str) -> int: ...


class ProviderRegistry:
    def __init__(self):
        self._primary: Optional[LLMProvider] = None
        self._backups: list[LLMProvider] = []
        self._daily_tokens_used: int = 0
        self._daily_token_limit: int = 500000

    def set_primary(self, provider: LLMProvider):
        self._primary = provider

    def add_backup(self, provider: LLMProvider):
        self._backups.append(provider)

    def set_budget(self, limit: int):
        self._daily_token_limit = limit

    async def chat(self, messages: list, **kwargs) -> str:
        providers = [self._primary] + self._backups if self._primary else list(self._backups)

        for provider in providers:
            if provider is None:
                continue
            try:
                result = await provider.chat(messages, **kwargs)
                tokens = provider.count_tokens(str(messages)) + provider.count_tokens(result)
                self._daily_tokens_used += tokens
                return result
            except Exception as e:
                logger.warning(f"Provider {provider.name} 失败: {e}")
                continue

        logger.error("所有 LLM 提供者均不可用")
        return "抱歉，我暂时无法回复，请稍后再试。"

    async def health_check(self) -> dict:
        result = {}
        if self._primary:
            result["primary"] = await self._primary.health_check()
        for i, backup in enumerate(self._backups):
            result[f"backup_{i}"] = await backup.health_check()
        return result

    def budget_stats(self) -> dict:
        return {
            "used": self._daily_tokens_used,
            "limit": self._daily_token_limit,
            "percent": self._daily_tokens_used / max(self._daily_token_limit, 1) * 100,
        }

    def reset_daily_budget(self):
        self._daily_tokens_used = 0
