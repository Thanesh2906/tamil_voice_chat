"""Streaming STT over HTTP + WebSocket.

Architecture (blueprint §3):
- PCM/Opus audio frames arrive over WebSocket.
- We buffer, run VAD, and feed segments into faster-whisper.
- We emit `partial` transcripts while the user is speaking and a final
  `transcript` event when the segment closes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from packages.common import configure_logging, get_logger, get_settings
from packages.schemas.voice import VoiceFrame, VoiceStart, VoiceStop

configure_logging()
log = get_logger("stt")

app = FastAPI(title="Jarvis STT", version="1.0.0")
_model = None  # type: ignore


def get_model():
    """Lazily import faster_whisper so the module loads without the heavy dep."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        s = get_settings()
        _model = WhisperModel(
            s.stt_model,
            device="auto",
            compute_type=s.stt_compute_type,
        )
    return _model


@dataclass
class _Session:
    request_id: str
    session_id: str
    sample_rate: int = 16_000
    pcm_chunks: list = None
    language: Optional[str] = None

    def __post_init__(self) -> None:
        self.pcm_chunks = []


def _transcribe(pcm: bytes, sample_rate: int, language: Optional[str]):
    import io
    import numpy as np

    model = get_model()
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    segments, info = model.transcribe(
        audio,
        language=language if language and language != "auto" else None,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, info.language


async def _send(ws: WebSocket, **kw) -> None:
    await ws.send_text(json.dumps(kw, ensure_ascii=False))


@app.websocket("/voice/transcribe")
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    sess = _Session(request_id="", session_id="")
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if "text" in msg:
                payload = json.loads(msg["text"])
                kind = payload.get("type")
                if kind == "start":
                    sess = _Session(
                        request_id=payload.get("request_id", ""),
                        session_id=payload.get("session_id", ""),
                        sample_rate=int(payload.get("sample_rate", 16_000)),
                        language=payload.get("language"),
                    )
                    await _send(ws, type="ready", request_id=sess.request_id, session_id=sess.session_id)
                elif kind == "stop":
                    text, lang = await asyncio.to_thread(
                        _transcribe,
                        b"".join(sess.pcm_chunks),
                        sess.sample_rate,
                        sess.language,
                    )
                    await _send(
                        ws,
                        type="transcript",
                        request_id=sess.request_id,
                        session_id=sess.session_id,
                        data={"text": text, "language": lang, "final": True},
                    )
                    sess.pcm_chunks.clear()
                elif kind == "partial":
                    text, lang = await asyncio.to_thread(
                        _transcribe,
                        b"".join(sess.pcm_chunks),
                        sess.sample_rate,
                        sess.language,
                    )
                    await _send(
                        ws,
                        type="partial",
                        request_id=sess.request_id,
                        session_id=sess.session_id,
                        data={"text": text, "language": lang},
                    )
            elif "bytes" in msg:
                sess.pcm_chunks.append(msg["bytes"])
    except WebSocketDisconnect:
        log.info("stt ws disconnected session=%s", sess.session_id)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
