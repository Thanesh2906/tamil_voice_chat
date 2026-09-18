"""Replaceable backend adapters for deterministic tests and production services."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from prometheus_client import Histogram

from packages.common.config import Settings, get_settings
from services.llm import CompletionResult, RoutingDecision, complete_with_tools, stream_chat

STT_SECONDS = Histogram("jarvis_stt_seconds", "STT request latency")
RAG_SECONDS = Histogram("jarvis_rag_seconds", "RAG retrieval latency")
LLM_FIRST_TOKEN_SECONDS = Histogram(
    "jarvis_llm_first_token_seconds", "Time until the first LLM token"
)
TTS_FIRST_AUDIO_SECONDS = Histogram(
    "jarvis_tts_first_audio_seconds", "Time until a TTS audio response"
)


@dataclass
class Transcript:
    text: str
    language: str


class ServiceAdapters:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = httpx.AsyncClient(
            timeout=self.settings.request_timeout_seconds, trust_env=False
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def transcribe(self, pcm: bytes, sample_rate: int, language: str | None) -> Transcript:
        with STT_SECONDS.time():
            response = await self.client.post(
                f"{self.settings.stt_url.rstrip('/')}/transcribe",
                params={"sample_rate": sample_rate, "language": language or "auto"},
                content=pcm,
                headers={"content-type": "application/octet-stream"},
            )
        response.raise_for_status()
        payload = response.json()
        return Transcript(payload.get("text", ""), payload.get("language", language or "unknown"))

    async def retrieve(
        self, question: str, project_id: str, tenant_id: str, owner_id: str
    ) -> list[dict[str, Any]]:
        with RAG_SECONDS.time():
            response = await self.client.get(
                f"{self.settings.rag_url.rstrip('/')}/rag/sources",
                params={"q": question, "project_id": project_id,
                        "tenant_id": tenant_id, "owner_id": owner_id},
            )
        response.raise_for_status()
        return response.json()

    async def llm(
        self,
        messages: list[dict[str, str]],
        *,
        mode: str,
        context: str | None,
        provider: str | None = None,
        model: str | None = None,
        allow_cloud: bool | None = None,
        on_decision: Callable[[RoutingDecision], None] | None = None,
    ) -> AsyncIterator[str]:
        started = time.monotonic()
        first = True
        try:
            async for token in stream_chat(
                messages,
                mode=mode,
                context=context,
                provider=provider,
                model=model,
                allow_cloud=allow_cloud,
                on_decision=on_decision,
            ):
                if first:
                    LLM_FIRST_TOKEN_SECONDS.observe(time.monotonic() - started)
                    first = False
                yield token
        finally:
            if first:
                LLM_FIRST_TOKEN_SECONDS.observe(time.monotonic() - started)

    async def llm_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        mode: str,
        context: str | None,
        tools: list[dict],
        provider: str | None = None,
        model: str | None = None,
        allow_cloud: bool | None = None,
        on_decision: Callable[[RoutingDecision], None] | None = None,
    ) -> CompletionResult:
        """Non-streaming counterpart to llm(): used for a tool-enabled turn,
        where the caller needs the whole response (text or tool calls) before
        deciding what happens next, not a token stream."""
        started = time.monotonic()
        result = await complete_with_tools(
            messages, tools=tools, mode=mode, context=context,
            provider=provider, model=model, allow_cloud=allow_cloud, on_decision=on_decision,
        )
        LLM_FIRST_TOKEN_SECONDS.observe(time.monotonic() - started)
        return result

    async def synthesize(self, text: str) -> dict[str, Any]:
        with TTS_FIRST_AUDIO_SECONDS.time():
            response = await self.client.post(
                f"{self.settings.tts_url.rstrip('/')}/synthesize", json={"text": text}
            )
        response.raise_for_status()
        return response.json()

    async def monitoring_tool(self, name: str, args: dict[str, str]) -> dict[str, Any]:
        if name == "get_host_summary":
            path = "/monitoring/summary"
        elif name == "get_project_summary":
            project_id = args["project_id"]
            path = f"/monitoring/projects/{project_id}"
        elif name == "get_gpu_summary":
            path = "/monitoring/gpu"
        elif name == "get_service_health":
            project_id = args["project_id"]
            path = f"/monitoring/projects/{project_id}/health"
        else:
            raise ValueError("unsupported monitoring tool")
        response = await self.client.get(
            f"{self.settings.monitoring_url.rstrip('/')}{path}",
            params={"window": args.get("window", "15m")},
        )
        response.raise_for_status()
        return response.json()


def chunk_phrases(text: str, limit: int = 180) -> list[str]:
    """Split complete clauses for progressive TTS without cutting words."""
    chunks: list[str] = []
    current = ""
    for char in text:
        current += char
        if char in ".!?;\n।!?" or len(current) >= limit:
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]
