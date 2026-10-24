from __future__ import annotations

"""Generic OpenAI-compatible provider."""
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI
from llm.providers import LLMProvider


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 30,
        max_retries: int = 3,
        client: AsyncOpenAI | None = None,
    ):
        self._owns_client = client is None
        self._client = client or AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
        )
        self._model = model
        self.last_usage: Any = None
        self.last_model = model

    def with_model(self, model: str) -> "OpenAICompatProvider":
        return OpenAICompatProvider(api_key="", base_url="", model=model, client=self._client)

    @property
    def name(self) -> str:
        return f"openai_compat/{self._model}"

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        resp = await self._client.chat.completions.create(
            model=kwargs.get("model") or self._model,
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 2048),
            temperature=kwargs.get("temperature", 0.7),
        )
        self.last_usage = getattr(resp, "usage", None)
        self.last_model = getattr(resp, "model", None) or kwargs.get("model") or self._model
        return resp.choices[0].message.content or ""

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=kwargs.get("model") or self._model,
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 2048),
            temperature=kwargs.get("temperature", 0.7),
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            content = chunk.choices[0].delta.content
            if content:
                yield content

    async def health_check(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return True
        except Exception:
            return False

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()
