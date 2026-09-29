"""Provider adapters for the multi-model router (see docs/master-build-audit.md, Phase 3).

Every adapter exposes the same streaming interface so the router can swap between
a local Ollama runtime and a cloud model (Claude, ChatGPT, Gemini, or any
OpenRouter-hosted open-weight model such as a 70B+ class model) without the caller
needing to know which one is answering. Each adapter only ever holds the single
credential it was constructed with; it never sees other providers' keys.

No provider SDKs are used deliberately, to keep the dependency surface identical
to the rest of the codebase (plain httpx, same as the existing Ollama client).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx


class ProviderError(RuntimeError):
    """Raised when a provider cannot answer: missing key, unreachable, bad response."""


@dataclass(slots=True)
class ToolCallRequest:
    """A model asking to call one tool. `id` correlates the eventual result
    back to this specific call in the next turn (synthesized for providers,
    like Ollama, that don't hand back a call id of their own)."""

    id: str
    name: str
    arguments: dict


@dataclass(slots=True)
class CompletionResult:
    """Result of one non-streaming turn: either final text, or one or more
    tool calls the caller must resolve (execute or reject) before continuing."""

    text: str
    tool_calls: list[ToolCallRequest]


class ProviderAdapter(Protocol):
    SUPPORTS_TOOLS: bool

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float,
        top_p: float,
        timeout: float | None,
    ) -> AsyncIterator[str]: ...

    async def complete(
        self,
        messages: list[dict],
        *,
        model: str,
        tools: list[dict],
        temperature: float,
        top_p: float,
        timeout: float | None,
    ) -> CompletionResult: ...

    async def health(self) -> bool: ...


@dataclass(slots=True)
class ProviderSpec:
    """Non-secret metadata about a provider, safe to expose to clients."""

    name: str
    privacy: str  # "local" (never leaves this host) or "cloud" (leaves this host)
    default_model: str
    configured: bool


def _split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Anthropic and Gemini take the system prompt out-of-band; split it off here."""
    if messages and messages[0].get("role") == "system":
        return messages[0].get("content", ""), messages[1:]
    return "", messages


# The manager's tool loop represents a conversation with two extra message
# shapes beyond plain {"role": "user"|"assistant"|"system", "content": str}:
#   {"role": "assistant", "tool_calls": [{"id", "name", "arguments": dict}]}
#   {"role": "tool", "tool_call_id": id, "name": str, "content": str}
# Each provider below converts these into its own wire format.


def _to_openai_style_messages(messages: list[dict]) -> list[dict]:
    converted = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            converted.append({
                "role": "assistant",
                "content": m.get("content"),
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                    for tc in m["tool_calls"]
                ],
            })
        elif role == "tool":
            converted.append({"role": "tool", "tool_call_id": m.get("tool_call_id"),
                              "name": m.get("name"), "content": m.get("content", "")})
        else:
            converted.append({"role": role, "content": m.get("content", "")})
    return converted


def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    """Same shape as Ollama's own /api/chat format, except `arguments` stays a
    dict (Ollama does not want a JSON-encoded string the way OpenAI does)."""
    converted = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            converted.append({
                "role": "assistant",
                "content": m.get("content") or "",
                "tool_calls": [
                    {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in m["tool_calls"]
                ],
            })
        elif role == "tool":
            converted.append({"role": "tool", "content": m.get("content", "")})
        else:
            converted.append({"role": role, "content": m.get("content", "")})
    return converted


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Anthropic has no "tool" role: a tool result is a user-role message
    carrying a tool_result content block instead."""
    converted = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            converted.append({
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]}
                    for tc in m["tool_calls"]
                ],
            })
        elif role == "tool":
            converted.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                            "content": m.get("content", "")}],
            })
        else:
            converted.append({"role": role, "content": m.get("content", "")})
    return converted


def _to_gemini_contents(messages: list[dict]) -> list[dict]:
    """Gemini has three roles: "user", "model", and "function" (for a tool
    result). A functionResponse's `response` field must be an object, so a
    JSON-string tool result is parsed back into one; non-JSON content is
    wrapped rather than dropped."""
    converted = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            converted.append({
                "role": "model",
                "parts": [
                    {"functionCall": {"name": tc["name"], "args": tc["arguments"]}} for tc in m["tool_calls"]
                ],
            })
        elif role == "tool":
            parsed = _parse_json_arguments(m.get("content", ""))
            if not parsed and m.get("content"):
                parsed = {"text": m["content"]}
            converted.append({
                "role": "function",
                "parts": [{"functionResponse": {"name": m.get("name", ""), "response": parsed}}],
            })
        else:
            converted.append({"role": "model" if role == "assistant" else "user",
                              "parts": [{"text": m.get("content", "")}]})
    return converted


def _parse_json_arguments(raw) -> dict:
    """Tool-call arguments arrive as a JSON string from OpenAI-shaped APIs and
    already-parsed from others. A model can hand back malformed JSON; treat
    that as empty arguments rather than crashing the run -- the gateway's own
    schema validation will reject it with a clear error either way."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        return {}


class OllamaProvider:
    """Local, private inference. Never leaves the host running Ollama/vLLM."""

    SUPPORTS_TOOLS = True

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def stream(
        self, messages, *, model, temperature=0.2, top_p=0.9, timeout=120.0
    ) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "top_p": top_p},
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/chat", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        token = obj.get("message", {}).get("content")
                        if token:
                            yield token
                        if obj.get("done"):
                            return
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc

    async def complete(
        self, messages, *, model, tools=(), temperature=0.2, top_p=0.9, timeout=120.0
    ) -> CompletionResult:
        payload = {
            "model": model,
            "messages": _to_ollama_messages(messages),
            "stream": False,
            "options": {"temperature": temperature, "top_p": top_p},
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["parameters"]}}
                for t in tools
            ]
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc
        message = response.json().get("message", {})
        calls = [
            ToolCallRequest(id=f"call_{i}", name=tc["function"]["name"],
                            arguments=_parse_json_arguments(tc["function"].get("arguments", {})))
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]
        return CompletionResult(text=message.get("content", "") or "", tool_calls=calls)

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except httpx.HTTPError:
            return False


class AnthropicProvider:
    """Claude, via the Messages API. Cloud: data leaves this host."""

    SUPPORTS_TOOLS = True

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def stream(
        self, messages, *, model, temperature=0.2, top_p=0.9, timeout=120.0
    ) -> AsyncIterator[str]:
        system, rest = _split_system(messages)
        payload = {
            "model": model,
            "system": system,
            "messages": [{"role": m["role"], "content": m["content"]} for m in rest],
            "max_tokens": 4096,
            "temperature": temperature,
            "top_p": top_p,
            "stream": True,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/v1/messages", json=payload, headers=headers
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ProviderError(f"anthropic error {response.status_code}: {body[:300]!r}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if not data:
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("type") == "content_block_delta":
                            delta = obj.get("delta", {})
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield delta["text"]
                        elif obj.get("type") == "message_stop":
                            return
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic request failed: {exc}") from exc

    async def complete(
        self, messages, *, model, tools=(), temperature=0.2, top_p=0.9, timeout=120.0
    ) -> CompletionResult:
        system, rest = _split_system(messages)
        payload = {
            "model": model,
            "system": system,
            "messages": _to_anthropic_messages(rest),
            "max_tokens": 4096,
            "temperature": temperature,
            "top_p": top_p,
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                for t in tools
            ]
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/v1/messages", json=payload, headers=headers)
                if response.status_code >= 400:
                    raise ProviderError(f"anthropic error {response.status_code}: {response.text[:300]!r}")
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic request failed: {exc}") from exc
        blocks = response.json().get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            ToolCallRequest(id=b["id"], name=b["name"], arguments=b.get("input", {}))
            for b in blocks if b.get("type") == "tool_use"
        ]
        return CompletionResult(text=text, tool_calls=calls)

    async def health(self) -> bool:
        return bool(self.api_key)


class OpenAIProvider:
    """ChatGPT-family models via the Chat Completions API. Cloud."""

    SUPPORTS_TOOLS = True

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.extra_headers = extra_headers or {}

    async def stream(
        self, messages, *, model, temperature=0.2, top_p=0.9, timeout=120.0
    ) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=payload, headers=headers
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ProviderError(f"openai-compatible error {response.status_code}: {body[:300]!r}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        token = choices[0].get("delta", {}).get("content")
                        if token:
                            yield token
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai-compatible request failed: {exc}") from exc

    async def complete(
        self, messages, *, model, tools=(), temperature=0.2, top_p=0.9, timeout=120.0
    ) -> CompletionResult:
        payload = {
            "model": model,
            "messages": _to_openai_style_messages(messages),
            "temperature": temperature,
            "top_p": top_p,
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["parameters"]}}
                for t in tools
            ]
        headers = {"Authorization": f"Bearer {self.api_key}", **self.extra_headers}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
                if response.status_code >= 400:
                    raise ProviderError(
                        f"openai-compatible error {response.status_code}: {response.text[:300]!r}"
                    )
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai-compatible request failed: {exc}") from exc
        choices = response.json().get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        calls = [
            ToolCallRequest(id=tc.get("id", f"call_{i}"), name=tc["function"]["name"],
                            arguments=_parse_json_arguments(tc["function"].get("arguments")))
            for i, tc in enumerate(message.get("tool_calls") or [])
        ]
        return CompletionResult(text=message.get("content") or "", tool_calls=calls)

    async def health(self) -> bool:
        return bool(self.api_key)


class OpenRouterProvider(OpenAIProvider):
    """Gateway to open-weight and third-party models (e.g. Llama 3.1 405B, Qwen2.5-72B,
    DeepSeek). Uses the same OpenAI-compatible wire format. Cloud unless you point it
    at a self-hosted OpenRouter-compatible gateway."""

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1") -> None:
        super().__init__(
            api_key,
            base_url,
            extra_headers={
                "HTTP-Referer": "https://github.com/jarvis-assistant",
                "X-Title": "Jarvis Assistant",
            },
        )


class GroqProvider(OpenAIProvider):
    """Groq's inference API: same OpenAI-compatible wire format, running
    open-weight models (Llama, and others) on hardware built specifically for
    very low-latency inference. Cloud. This is the fast-answer provider --
    see MODE_PREFERENCE in services/llm/router.py."""

    def __init__(self, api_key: str, base_url: str = "https://api.groq.com/openai/v1") -> None:
        super().__init__(api_key, base_url)


class GeminiProvider:
    """Gemini via the Generative Language API. Cloud: data leaves this host."""

    SUPPORTS_TOOLS = True

    def __init__(
        self, api_key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def stream(
        self, messages, *, model, temperature=0.2, top_p=0.9, timeout=120.0
    ) -> AsyncIterator[str]:
        system, rest = _split_system(messages)
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in rest
        ]
        payload: dict = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "topP": top_p},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.base_url}/models/{model}:streamGenerateContent"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    url,
                    params={"alt": "sse", "key": self.api_key},
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise ProviderError(f"gemini error {response.status_code}: {body[:300]!r}")
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if not data:
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        candidates = obj.get("candidates") or []
                        if not candidates:
                            continue
                        parts = candidates[0].get("content", {}).get("parts") or []
                        for part in parts:
                            text = part.get("text")
                            if text:
                                yield text
        except httpx.HTTPError as exc:
            raise ProviderError(f"gemini request failed: {exc}") from exc

    async def complete(
        self, messages, *, model, tools=(), temperature=0.2, top_p=0.9, timeout=120.0
    ) -> CompletionResult:
        system, rest = _split_system(messages)
        payload: dict = {
            "contents": _to_gemini_contents(rest),
            "generationConfig": {"temperature": temperature, "topP": top_p},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}
                for t in tools
            ]}]
        url = f"{self.base_url}/models/{model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, params={"key": self.api_key}, json=payload)
                if response.status_code >= 400:
                    raise ProviderError(f"gemini error {response.status_code}: {response.text[:300]!r}")
        except httpx.HTTPError as exc:
            raise ProviderError(f"gemini request failed: {exc}") from exc
        candidates = response.json().get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(part.get("text", "") for part in parts if "text" in part)
        calls = [
            ToolCallRequest(id=f"call_{i}", name=part["functionCall"]["name"],
                            arguments=part["functionCall"].get("args", {}))
            for i, part in enumerate(parts) if "functionCall" in part
        ]
        return CompletionResult(text=text, tool_calls=calls)

    async def health(self) -> bool:
        return bool(self.api_key)
