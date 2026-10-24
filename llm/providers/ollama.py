"""Ollama local provider — reuses one httpx connection pool."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from llm.providers import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout: float = 120,
        client: httpx.AsyncClient | None = None,
    ):
        self._model = model
        self.last_usage: Any = None
        self.last_model = model
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)))

    def with_model(self, model: str) -> "OllamaProvider":
        return OllamaProvider(model=model, base_url=self._base_url, client=self._client)

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    def _payload(self, messages: list[dict[str, Any]], *, stream: bool, **kwargs: Any) -> dict[str, Any]:
        return {
            "model": kwargs.get("model") or self._model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_predict": kwargs.get("max_tokens", 2048),
                "temperature": kwargs.get("temperature", 0.7),
            },
        }

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        response = await self._client.post(
            f"{self._base_url}/api/chat",
            json=self._payload(messages, stream=False, **kwargs),
        )
        response.raise_for_status()
        payload = response.json()
        self.last_usage = {
            "prompt_tokens": payload.get("prompt_eval_count", 0),
            "completion_tokens": payload.get("eval_count", 0),
        }
        self.last_model = payload.get("model") or self._model
        return payload["message"]["content"]

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/chat",
            json=self._payload(messages, stream=True, **kwargs),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                content = payload.get("message", {}).get("content", "")
                if content:
                    yield content

    async def health_check(self) -> bool:
        try:
            response = await self._client.get(f"{self._base_url}/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    def count_tokens(self, text: str) -> int:
        return len(text) // 4

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
