"""Generic OpenAI-compatible provider."""
from openai import AsyncOpenAI
from llm.providers import LLMProvider


class OpenAICompatProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    @property
    def name(self) -> str:
        return f"openai_compat/{self._model}"

    async def chat(self, messages: list, **kwargs) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 2048),
            temperature=kwargs.get("temperature", 0.7),
        )
        return resp.choices[0].message.content or ""

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
