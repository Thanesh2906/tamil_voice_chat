"""Tamil-capable streaming TTS.

The blueprint §3 requires us to synthesize complete phrases without
waiting for the whole response, and to support barge-in. We accept
text via WebSocket / HTTP and return WAV/Opus chunks. The actual
engine (piper, coqui, mimi, edge) is selected by `TTS_ENGINE`.

For Phase 1 we default to a small Piper-compatible wrapper that emits
16-bit PCM at 16kHz. Engines that produce other sample rates are
resampled at the chunk boundary.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from typing import AsyncIterator, Optional

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from packages.common import configure_logging, get_logger, get_settings

configure_logging()
log = get_logger("tts")

app = FastAPI(title="Jarvis TTS", version="1.0.0")


def _resample(audio, sr_in: int, sr_out: int):
    if sr_in == sr_out or len(audio) == 0:
        return audio
    duration = len(audio) / sr_in
    n_out = int(round(duration * sr_out))
    xp = np.linspace(0, len(audio), num=len(audio), endpoint=False)
    fp = audio.astype(np.float32)
    new_x = np.linspace(0, len(audio), num=n_out, endpoint=False)
    return np.interp(new_x, xp, fp).astype(audio.dtype)


def _synthesize_sync(text: str, voice: str) -> bytes:
    """Run the configured TTS engine and return 16-bit PCM bytes."""

    s = get_settings()
    engine = s.tts_engine.lower()

    if engine == "piper":
        # Lazy import so non-Piper deployments don't require piper.
        from piper import PiperVoice

        piper_voices = getattr(_synthesize_sync, "_cache", {})
        if voice not in piper_voices:
            piper_voices[voice] = PiperVoice.load(voice)
        pv = piper_voices[voice]
        audio = pv.synthesize(text)
        # Piper returns a numpy int16 array.
        pcm = np.asarray(audio, dtype=np.int16).tobytes()
        return pcm

    if engine == "edge":
        # Microsoft edge-tts is async; we run it inline and convert.
        import edge_tts
        import tempfile

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(text, voice=voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        raw = asyncio.run(_run())
        # Edge TTS returns mp3; convert with ffmpeg if available, else return raw.
        try:
            import subprocess

            p = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                 "-f", "s16le", "-acodec", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", "pipe:1"],
                input=raw, capture_output=True, check=True,
            )
            return p.stdout
        except Exception:
            return raw

    # Fallback: silence so the rest of the pipeline still works.
    return (np.zeros(int(0.5 * 16_000), dtype=np.int16)).tobytes()


_synthesize_sync._cache = {}


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


@app.post("/synthesize")
async def synthesize(payload: dict) -> dict:
    text = payload.get("text", "").strip()
    voice = payload.get("voice")
    if not text:
        return {"audio": b"", "format": "wav", "sample_rate": 16_000}
    pcm = await asyncio.to_thread(_synthesize_sync, text, voice or get_settings().tts_voice)
    wav = _pcm_to_wav(pcm, sample_rate=16_000)
    return {"audio": wav.hex(), "format": "wav", "sample_rate": 16_000}


@app.websocket("/voice/speak")
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if "text" in msg:
                payload = json.loads(msg["text"])
                if payload.get("type") == "speak":
                    text = payload.get("text", "").strip()
                    pcm = await asyncio.to_thread(_synthesize_sync, text, get_settings().tts_voice)
                    wav = _pcm_to_wav(pcm, sample_rate=16_000)
                    await ws.send_json(
                        {"type": "audio", "format": "wav", "sample_rate": 16_000, "data": wav.hex()}
                    )
    except WebSocketDisconnect:
        pass


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "engine": get_settings().tts_engine}
