"""Ollama local provider."""
import httpx
from llm.providers import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434"):
        self._model = model
        self._base_url = base_url

    @property
    def name(self) -> str:
        return f"ollama/{self._model}"

    async def chat(self, messages: list, **kwargs) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/api/chat",
                json={"model": self._model, "messages": messages, "stream": False},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self._base_url}/api/tags", timeout=5)
                return resp.status_code == 200
        except Exception:
            return False

    def count_tokens(self, text: str) -> int:
        return len(text) // 4
