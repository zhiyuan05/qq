"""Multi-provider LLM client with failover."""
import asyncio
from typing import AsyncIterator, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("llm.client")


class DeepSeekClient:
    """Legacy client - wraps provider registry for backward compatibility."""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", fallback_model: str = "deepseek-reasoner",
                 max_tokens: int = 2048, temperature: float = 0.7,
                 timeout: int = 30, max_retries: int = 3):
        from llm.providers import ProviderRegistry
        from llm.providers.deepseek import DeepSeekProvider

        self.model = model
        self.fallback_model = fallback_model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries

        self.registry = ProviderRegistry()
        primary = DeepSeekProvider(api_key=api_key, model=model, base_url=base_url)
        self.registry.set_primary(primary)

        if fallback_model and fallback_model != model:
            fallback = DeepSeekProvider(api_key=api_key, model=fallback_model, base_url=base_url)
            self.registry.add_backup(fallback)

    async def chat(self, messages: List[Dict[str, str]]) -> str:
        return await self.registry.chat(
            messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

    async def chat_stream(self, messages: List[Dict[str, str]]) -> AsyncIterator[str]:
        """Streaming chat - falls back to non-streaming on error."""
        try:
            from llm.providers.deepseek import DeepSeekProvider
            primary = self.registry._primary
            if isinstance(primary, DeepSeekProvider):
                from openai import AsyncOpenAI
                client = primary._client
                stream = await client.chat.completions.create(
                    model=primary._model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield delta.content
                return
        except Exception as e:
            logger.warning(f"流式调用失败，降级到非流式: {e}")

        result = await self.chat(messages)
        yield result

    def budget_stats(self) -> dict:
        return self.registry.budget_stats()


class LLMClient:
    """High-level LLM client that delegates to provider registry."""

    def __init__(self, config: dict):
        from llm.providers import ProviderRegistry
        self.registry = ProviderRegistry()
        self._setup_providers(config)

    def _setup_providers(self, config: dict):
        from llm.providers.deepseek import DeepSeekProvider

        primary_cfg = config.get("primary", {})
        if primary_cfg.get("provider") == "deepseek":
            provider = DeepSeekProvider(
                api_key=primary_cfg.get("api_key", ""),
                model=primary_cfg.get("model", "deepseek-chat"),
            )
            self.registry.set_primary(provider)

        backup_cfg = config.get("backup", {})
        if backup_cfg.get("api_key"):
            from llm.providers.openai_compat import OpenAICompatProvider
            backup = OpenAICompatProvider(
                api_key=backup_cfg["api_key"],
                base_url=backup_cfg.get("base_url", "https://api.openai.com/v1"),
                model=backup_cfg.get("model", "gpt-3.5-turbo"),
            )
            self.registry.add_backup(backup)

        ollama_cfg = config.get("ollama", {})
        if ollama_cfg.get("enabled"):
            from llm.providers.ollama import OllamaProvider
            ollama = OllamaProvider(model=ollama_cfg.get("model", "qwen2.5:7b"))
            self.registry.add_backup(ollama)

        budget_cfg = config.get("budget", {})
        self.registry.set_budget(budget_cfg.get("daily_token_limit", 500000))

    async def chat(self, messages: list, **kwargs) -> str:
        return await self.registry.chat(messages, **kwargs)

    async def health_check(self) -> dict:
        return await self.registry.health_check()

    def budget_stats(self) -> dict:
        return self.registry.budget_stats()
