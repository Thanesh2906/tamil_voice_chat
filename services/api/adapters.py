"""Replaceable backend adapters for deterministic tests and production services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from packages.common.config import Settings, get_settings
from services.llm import stream_chat


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
        response = await self.client.get(
            f"{self.settings.rag_url.rstrip('/')}/rag/sources",
            params={"q": question, "project_id": project_id,
                    "tenant_id": tenant_id, "owner_id": owner_id},
        )
        response.raise_for_status()
        return response.json()

    async def llm(
        self, messages: list[dict[str, str]], *, mode: str, context: str | None
    ) -> AsyncIterator[str]:
        async for token in stream_chat(messages, mode=mode, context=context):
            yield token

    async def synthesize(self, text: str) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.settings.tts_url.rstrip('/')}/synthesize", json={"text": text}
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
