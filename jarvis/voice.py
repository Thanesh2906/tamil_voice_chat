from __future__ import annotations

import asyncio
import base64
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .adapters import LlmAdapter, SttAdapter, TtsAdapter, normalize_pcm16_mono

SendEvent = Callable[[dict], Awaitable[None]]


@dataclass(slots=True)
class VoiceSession:
    stt: SttAdapter
    llm: LlmAdapter
    tts: TtsAdapter
    system_prompt: str
    max_audio_bytes: int
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    sample_rate: int = 16_000
    locale: str = "ta-IN"
    project_id: str | None = None
    _audio: bytearray = field(default_factory=bytearray)
    _active_task: asyncio.Task | None = None

    def start(self, payload: dict) -> dict:
        self.request_id = str(payload.get("request_id") or uuid.uuid4().hex)
        self.sample_rate = int(payload.get("sample_rate", 16_000))
        self.locale = str(payload.get("locale", "ta-IN"))
        self.project_id = payload.get("project_id")
        self._audio.clear()
        return {"type": "ready", "request_id": self.request_id, "sample_rate": 16_000}

    def add_audio(self, frame: bytes) -> None:
        if len(frame) % 2:
            raise ValueError("PCM16 frame must contain an even number of bytes")
        if len(self._audio) + len(frame) > self.max_audio_bytes:
            raise ValueError("utterance exceeds audio size limit")
        self._audio.extend(frame)

    async def cancel(self, send: SendEvent) -> None:
        if self._active_task and not self._active_task.done():
            self._active_task.cancel()
            try:
                await self._active_task
            except asyncio.CancelledError:
                pass
        await send({"type": "cancelled", "request_id": self.request_id})

    async def stop(
        self, send: SendEvent, context: str = "", citations: list[dict] | None = None
    ) -> None:
        if not self._audio:
            raise ValueError("no audio was received")
        self._active_task = asyncio.create_task(self._run(send, context, citations or []))
        await self._active_task

    async def _run(self, send: SendEvent, context: str, citations: list[dict]) -> None:
        normalized_audio = normalize_pcm16_mono(bytes(self._audio), self.sample_rate)
        transcript = await self.stt.transcribe(normalized_audio, 16_000, self.locale)
        await send(
            {"type": "transcript", "request_id": self.request_id, "text": transcript, "final": True}
        )
        user_content = transcript
        if context:
            user_content = f"Authorized project context:\n{context}\n\nUser request:\n{transcript}"
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]
        parts: list[str] = []
        async for token in self.llm.stream(messages):
            parts.append(token)
            await send({"type": "token", "request_id": self.request_id, "text": token})
        answer = "".join(parts).strip()
        if citations:
            await send({"type": "citation", "request_id": self.request_id, "items": citations})
        audio, media_type = await self.tts.synthesize(answer, self.locale)
        await send(
            {
                "type": "audio",
                "request_id": self.request_id,
                "media_type": media_type,
                "data": base64.b64encode(audio).decode(),
            }
        )
        await send({"type": "final", "request_id": self.request_id, "text": answer})
