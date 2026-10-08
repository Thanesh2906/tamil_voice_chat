from __future__ import annotations

import json

import httpx
import pytest

from services.llm import providers

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client_factory(handler):
    """Return a factory swapping httpx.AsyncClient's real transport for a mock one,
    so provider adapters can be tested without real network access or API keys."""

    def factory(*, timeout=None, **_ignored):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    return factory


MESSAGES = [
    {"role": "system", "content": "You are JARVIS."},
    {"role": "user", "content": "வணக்கம்"},
]


async def _collect(agen) -> str:
    return "".join([token async for token in agen])


@pytest.mark.asyncio
async def test_ollama_provider_streams_ndjson_tokens(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["messages"] == MESSAGES
        lines = [
            json.dumps({"message": {"content": "வணக்கம்"}}),
            json.dumps({"message": {"content": "!"}}),
            json.dumps({"done": True}),
        ]
        return httpx.Response(200, content="\n".join(lines))

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.OllamaProvider("http://llm:11434")
    text = await _collect(provider.stream(MESSAGES, model="llama3.1", temperature=0.2, top_p=0.9, timeout=5))
    assert text == "வணக்கம்!"


@pytest.mark.asyncio
async def test_anthropic_provider_splits_system_and_parses_sse(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "sk-test"
        body = json.loads(request.content)
        assert body["system"] == "You are JARVIS."
        assert body["messages"] == [{"role": "user", "content": "வணக்கம்"}]
        events = [
            'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"!"}}\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n',
        ]
        return httpx.Response(200, content="\n".join(events))

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.AnthropicProvider("sk-test")
    text = await _collect(
        provider.stream(MESSAGES, model="claude-sonnet-5", temperature=0.2, top_p=0.9, timeout=5)
    )
    assert text == "Hi!"


@pytest.mark.asyncio
async def test_openai_provider_parses_sse_and_stops_on_done(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["messages"] == MESSAGES
        chunks = [
            'data: {"choices":[{"delta":{"content":"Hi"}}]}',
            'data: {"choices":[{"delta":{"content":"!"}}]}',
            "data: [DONE]",
        ]
        return httpx.Response(200, content="\n".join(chunks))

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.OpenAIProvider("sk-test")
    text = await _collect(provider.stream(MESSAGES, model="gpt-4o", temperature=0.2, top_p=0.9, timeout=5))
    assert text == "Hi!"


@pytest.mark.asyncio
async def test_gemini_provider_maps_roles_and_uses_system_instruction(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "sk-test"
        assert "key=" not in str(request.url)
        body = json.loads(request.content)
        assert body["systemInstruction"] == {"parts": [{"text": "You are JARVIS."}]}
        assert body["contents"] == [{"role": "user", "parts": [{"text": "வணக்கம்"}]}]
        chunks = [
            'data: {"candidates":[{"content":{"parts":[{"text":"Hi"}]}}]}',
            'data: {"candidates":[{"content":{"parts":[{"text":"!"}]}}]}',
        ]
        return httpx.Response(200, content="\n".join(chunks))

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.GeminiProvider("sk-test")
    text = await _collect(
        provider.stream(MESSAGES, model="gemini-2.0-flash", temperature=0.2, top_p=0.9, timeout=5)
    )
    assert text == "Hi!"


@pytest.mark.asyncio
async def test_groq_provider_uses_groq_base_url_and_openai_wire_format(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api.groq.com/openai/v1/")
        assert request.headers["authorization"] == "Bearer gsk-test"
        body = json.loads(request.content)
        assert body["messages"] == MESSAGES
        chunks = [
            'data: {"choices":[{"delta":{"content":"Hi"}}]}',
            'data: {"choices":[{"delta":{"content":"!"}}]}',
            "data: [DONE]",
        ]
        return httpx.Response(200, content="\n".join(chunks))

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.GroqProvider("gsk-test")
    text = await _collect(
        provider.stream(MESSAGES, model="llama-3.3-70b-versatile", temperature=0.2, top_p=0.9, timeout=5)
    )
    assert text == "Hi!"


@pytest.mark.asyncio
async def test_provider_error_raised_on_http_error(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content='{"error":"bad key"}')

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.OpenAIProvider("sk-bad")
    with pytest.raises(providers.ProviderError):
        await _collect(provider.stream(MESSAGES, model="gpt-4o", temperature=0.2, top_p=0.9, timeout=5))


@pytest.mark.asyncio
async def test_xai_stream_uses_distinct_vendor_and_stops_on_done(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.x.ai"
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-xai"
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"Hello"}}]}\ndata: [DONE]\ndata: {"choices":[{"delta":{"content":"ignored"}}]}')

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    text = await _collect(providers.XAIProvider("unit-xai").stream(MESSAGES, model="model-under-test"))
    assert text == "Hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key", [None, "unit-runtime"])
async def test_self_hosted_complete_supports_optional_auth_and_tools(monkeypatch, api_key) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://runtime:8000/v1/chat/completions"
        assert request.headers.get("authorization") == (f"Bearer {api_key}" if api_key else None)
        body = json.loads(request.content)
        assert body["model"] == "local-model"
        assert body["tools"][0]["function"]["name"] == "list_dir"
        return httpx.Response(200, json={"choices": [{"message": {
            "content": None, "tool_calls": [{"id": "call_local", "function": {
                "name": "list_dir", "arguments": '{"path":"."}',
            }}],
        }}]})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    adapter = providers.SelfHostedProvider("http://runtime:8000/v1/", api_key)
    result = await adapter.complete(MESSAGES, model="local-model", tools=[{
        "name": "list_dir", "description": "List approved directory", "parameters": {"type": "object"},
    }])
    assert result.tool_calls[0].name == "list_dir"
    assert result.tool_calls[0].arguments == {"path": "."}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", [providers.OpenAIProvider, providers.XAIProvider, providers.GroqProvider, providers.AnthropicProvider, providers.GeminiProvider])
@pytest.mark.parametrize("status", [200, 401, 500])
async def test_provider_health_really_checks_api_instead_of_key_presence(monkeypatch, provider_type, status) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path.endswith("/models")
        assert "key=" not in str(request.url)
        return httpx.Response(status, json={})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    assert await provider_type("unit-fixture").health() is (status == 200)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_provider_error_never_echoes_remote_body(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="echoed private credential and conversation")

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    with pytest.raises(providers.ProviderError, match="401") as error:
        await _collect(providers.OpenAIProvider("unit-fixture").stream(MESSAGES, model="model-under-test"))
    assert "private credential" not in str(error.value)
