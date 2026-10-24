"""真实 Provider 协议边界测试，不发出外部网络请求。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from llm.providers.deepseek import DeepSeekProvider
from llm.providers.ollama import OllamaProvider
from llm.providers.openai_compat import OpenAICompatProvider


def _sdk_client(*, response=None, stream=None):
    client = SimpleNamespace()
    client.chat = SimpleNamespace(completions=SimpleNamespace(create=AsyncMock()))
    client.close = AsyncMock()
    if stream is not None:
        client.chat.completions.create.return_value = stream
    else:
        client.chat.completions.create.return_value = response
    return client


async def _chunks(*values):
    for value in values:
        yield value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda client: DeepSeekProvider(api_key="", model="model-a", client=client),
        lambda client: OpenAICompatProvider(api_key="", base_url="", model="model-a", client=client),
    ],
)
async def test_openai_compatible_provider_maps_chat_parameters(provider_factory):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))])
    client = _sdk_client(response=response)
    provider = provider_factory(client)

    result = await provider.chat(
        [{"role": "user", "content": "hello"}],
        max_tokens=123,
        temperature=0.25,
    )

    assert result == "answer"
    client.chat.completions.create.assert_awaited_once_with(
        model="model-a",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=123,
        temperature=0.25,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda client: DeepSeekProvider(api_key="", model="model-a", client=client),
        lambda client: OpenAICompatProvider(api_key="", base_url="", model="model-a", client=client),
    ],
)
async def test_openai_compatible_provider_stream_skips_empty_choices(provider_factory):
    empty = SimpleNamespace(choices=[])
    first = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))])
    second = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))])
    client = _sdk_client(stream=_chunks(empty, first, second))
    provider = provider_factory(client)

    chunks = [chunk async for chunk in provider.chat_stream([])]

    assert chunks == ["你", "好"]
    assert client.chat.completions.create.await_args.kwargs["stream"] is True


@pytest.mark.asyncio
async def test_deepseek_model_variants_share_and_close_one_sdk_client():
    client = _sdk_client(response=None)
    with patch("llm.providers.deepseek.AsyncOpenAI", return_value=client):
        primary = DeepSeekProvider(api_key="key", model="primary")
        backup = primary.with_model("backup")

    assert primary._client is backup._client
    await backup.aclose()
    client.close.assert_not_awaited()
    await primary.aclose()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_ollama_provider_maps_options_and_streams_ndjson():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["stream"]:
            body = b'{"message":{"content":"A"}}\n{"message":{"content":"B"},"done":true}\n'
            return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})
        return httpx.Response(200, json={"message": {"content": "answer"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider(model="qwen", base_url="http://ollama.local/", client=client)
    try:
        result = await provider.chat([], max_tokens=42, temperature=0.1)
        chunks = [chunk async for chunk in provider.chat_stream([], max_tokens=43, temperature=0.2)]
        assert await provider.health_check() is True
    finally:
        await client.aclose()

    assert result == "answer"
    assert chunks == ["A", "B"]
    assert requests[0]["options"] == {"num_predict": 42, "temperature": 0.1}
    assert requests[1]["options"] == {"num_predict": 43, "temperature": 0.2}


@pytest.mark.asyncio
async def test_ollama_stream_surfaces_protocol_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"error":"model crashed"}\n')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaProvider(client=client)
    try:
        with pytest.raises(RuntimeError, match="model crashed"):
            _ = [chunk async for chunk in provider.chat_stream([])]
    finally:
        await client.aclose()
