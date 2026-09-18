from __future__ import annotations

import json

import httpx
import pytest

from services.llm import providers

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client_factory(handler):
    def factory(*, timeout=None, **_ignored):
        return _RealAsyncClient(transport=httpx.MockTransport(handler), timeout=timeout)

    return factory


TOOLS = [{"name": "list_dir", "description": "List a directory.",
         "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}]

SYSTEM_AND_USER = [
    {"role": "system", "content": "You are JARVIS."},
    {"role": "user", "content": "what files are here?"},
]


@pytest.mark.asyncio
async def test_ollama_complete_parses_tool_call_with_dict_arguments(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is False
        assert body["tools"][0]["function"]["name"] == "list_dir"
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": "",
                       "tool_calls": [{"function": {"name": "list_dir", "arguments": {"path": "."}}}]},
            "done": True,
        })

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.OllamaProvider("http://llm:11434")
    result = await provider.complete(SYSTEM_AND_USER, model="llama3.1", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.text == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "list_dir"
    assert result.tool_calls[0].arguments == {"path": "."}


@pytest.mark.asyncio
async def test_ollama_complete_returns_plain_text_with_no_tool_calls(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "hello!"}, "done": True})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.OllamaProvider("http://llm:11434")
    result = await provider.complete(SYSTEM_AND_USER, model="llama3.1", tools=[],
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.text == "hello!"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_anthropic_complete_parses_tool_use_block(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["input_schema"]["properties"]["path"]["type"] == "string"
        assert body["messages"] == [{"role": "user", "content": "what files are here?"}]
        return httpx.Response(200, json={
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "list_dir", "input": {"path": "."}}],
            "stop_reason": "tool_use",
        })

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.AnthropicProvider("sk-test")
    result = await provider.complete(SYSTEM_AND_USER, model="claude-sonnet-5", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.tool_calls[0].id == "toolu_1"
    assert result.tool_calls[0].name == "list_dir"
    assert result.tool_calls[0].arguments == {"path": "."}


@pytest.mark.asyncio
async def test_anthropic_complete_converts_tool_result_to_user_message(monkeypatch) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "done"}]})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.AnthropicProvider("sk-test")
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "list files"},
        {"role": "assistant", "tool_calls": [{"id": "toolu_1", "name": "list_dir", "arguments": {"path": "."}}]},
        {"role": "tool", "tool_call_id": "toolu_1", "name": "list_dir", "content": '{"entries": []}'},
    ]
    result = await provider.complete(history, model="claude-sonnet-5", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.text == "done"
    sent = captured["body"]["messages"]
    assert sent[0] == {"role": "user", "content": "list files"}
    assert sent[1]["role"] == "assistant"
    assert sent[1]["content"][0]["type"] == "tool_use"
    assert sent[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"entries": []}'}
    ]}


@pytest.mark.asyncio
async def test_openai_complete_parses_tool_call_with_json_string_arguments(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "list_dir"
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                           "function": {"name": "list_dir", "arguments": '{"path": "."}'}}],
        }}]})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.OpenAIProvider("sk-test")
    result = await provider.complete(SYSTEM_AND_USER, model="gpt-4o", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.tool_calls[0].id == "call_1"
    assert result.tool_calls[0].arguments == {"path": "."}


@pytest.mark.asyncio
async def test_openai_complete_handles_malformed_tool_arguments_gracefully(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                           "function": {"name": "list_dir", "arguments": "{not json"}}],
        }}]})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.OpenAIProvider("sk-test")
    result = await provider.complete(SYSTEM_AND_USER, model="gpt-4o", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.tool_calls[0].arguments == {}


@pytest.mark.asyncio
async def test_gemini_complete_returns_plain_text_with_no_function_call(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "plain answer"}]}}]})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.GeminiProvider("sk-test")
    assert provider.SUPPORTS_TOOLS is True
    result = await provider.complete(SYSTEM_AND_USER, model="gemini-2.0-flash", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.text == "plain answer"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_gemini_complete_parses_function_call(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "list_dir"
        assert body["contents"] == [{"role": "user", "parts": [{"text": "what files are here?"}]}]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "list_dir", "args": {"path": "."}}}
        ]}}]})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.GeminiProvider("sk-test")
    result = await provider.complete(SYSTEM_AND_USER, model="gemini-2.0-flash", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.text == ""
    assert result.tool_calls[0].name == "list_dir"
    assert result.tool_calls[0].arguments == {"path": "."}


@pytest.mark.asyncio
async def test_gemini_complete_converts_tool_result_to_function_role(monkeypatch) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "done"}]}}]})

    monkeypatch.setattr(providers.httpx, "AsyncClient", _mock_client_factory(handler))
    provider = providers.GeminiProvider("sk-test")
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "list files"},
        {"role": "assistant", "tool_calls": [{"id": "call_1", "name": "list_dir", "arguments": {"path": "."}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "list_dir", "content": '{"entries": []}'},
    ]
    result = await provider.complete(history, model="gemini-2.0-flash", tools=TOOLS,
                                     temperature=0.2, top_p=0.9, timeout=5)
    assert result.text == "done"
    sent = captured["body"]["contents"]
    assert sent[1]["role"] == "model"
    assert sent[1]["parts"][0]["functionCall"]["name"] == "list_dir"
    assert sent[2] == {"role": "function", "parts": [{"functionResponse": {"name": "list_dir", "response": {"entries": []}}}]}
