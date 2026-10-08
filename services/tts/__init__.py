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
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from prometheus_client import make_asgi_app

from packages.common import configure_logging, get_logger, get_settings

configure_logging()
log = get_logger("tts")

app = FastAPI(title="Jarvis TTS", version="1.0.0")
app.mount("/metrics", make_asgi_app())
_PIPER_VOICES: dict[str, Any] = {}
_PIPER_LOCK = Lock()
_OUTPUT_SAMPLE_RATE = 16_000


class TTSUnavailableError(RuntimeError):
    """A sanitized, actionable TTS error that is safe to return to clients."""



def _resample(audio, sr_in: int, sr_out: int):
    if sr_in == sr_out or len(audio) == 0:
        return audio
    duration = len(audio) / sr_in
    n_out = max(1, int(round(duration * sr_out)))
    xp = np.linspace(0, len(audio), num=len(audio), endpoint=False)
    fp = audio.astype(np.float32)
    new_x = np.linspace(0, len(audio), num=n_out, endpoint=False)
    return np.interp(new_x, xp, fp).astype(audio.dtype)


def _normalize_piper_pcm(raw: bytes, sample_rate: int, sample_width: int = 2, channels: int = 1) -> bytes:
    """Validate Piper's actual format and normalize mono PCM16 to protocol rate."""
    if (not isinstance(sample_rate, int) or not 1 <= sample_rate <= 192_000
            or sample_width != 2 or channels != 1):
        raise TTSUnavailableError("Piper returned an unsupported audio format; expected mono PCM16")
    if not isinstance(raw, (bytes, bytearray, memoryview)) or len(raw) % 2:
        raise TTSUnavailableError("Piper returned invalid PCM16 audio")
    audio = np.frombuffer(raw, dtype="<i2")
    return _resample(audio, sample_rate, _OUTPUT_SAMPLE_RATE).astype("<i2").tobytes()


def _synthesize_piper(text: str, voice: str) -> bytes:
    # PiperVoice.load expects the ONNX model and its adjacent JSON config, not
    # a locale label. Validate before importing/loading, without exposing paths.
    try:
        model_path = Path(voice)
        configured = (model_path.suffix.lower() == ".onnx" and model_path.is_file()
                      and Path(f"{voice}.json").is_file())
    except (OSError, TypeError, ValueError):
        configured = False
    if not configured:
        raise TTSUnavailableError(
            "Piper voice is not configured. Set TTS_VOICE to an existing .onnx model "
            "with its adjacent .onnx.json configuration"
        )
    try:
        from piper import PiperVoice
    except ImportError:
        raise TTSUnavailableError("Piper is not installed. Install the server's tts dependencies") from None

    try:
        # Serializes loading and inference for the cached native voice instance.
        with _PIPER_LOCK:
            if voice not in _PIPER_VOICES:
                _PIPER_VOICES[voice] = PiperVoice.load(voice)
            pv = _PIPER_VOICES[voice]
            chunks: list[bytes] = []
            if callable(getattr(pv, "synthesize_stream_raw", None)):
                # piper-tts 1.2 API: raw mono PCM16 at voice.config.sample_rate.
                rate = pv.config.sample_rate
                for raw in pv.synthesize_stream_raw(text):
                    chunks.append(_normalize_piper_pcm(raw, rate))
            else:
                # Current API: synthesize is a generator of AudioChunk objects.
                # See OHF-Voice/piper1-gpl docs/API_PYTHON.md.
                for chunk in pv.synthesize(text):
                    chunks.append(_normalize_piper_pcm(
                        chunk.audio_int16_bytes, chunk.sample_rate,
                        chunk.sample_width, chunk.sample_channels,
                    ))
            pcm = b"".join(chunks)
            if not pcm:
                raise TTSUnavailableError("Piper produced no audio; verify the selected voice model")
            return pcm
    except TTSUnavailableError:
        raise
    except Exception:
        raise TTSUnavailableError(
            "Piper synthesis failed; verify the installed voice model and Piper version"
        ) from None


def _synthesize_sync(text: str, voice: str) -> bytes:
    """Run the configured TTS engine and return 16-bit PCM bytes."""

    s = get_settings()
    engine = s.tts_engine.lower()

    if engine == "fake":
        return np.zeros(
            max(1, int(0.02 * 16_000 * max(1, len(text)))), dtype=np.int16
        ).tobytes()

    if engine == "piper":
        return _synthesize_piper(text, voice)

    if engine == "edge":
        # Microsoft edge-tts is async; we run it inline and convert.
        import edge_tts

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(text, voice=voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        raw = asyncio.run(_run())
        # Edge TTS returns MP3 and must be converted before WAV wrapping.
        try:
            import subprocess

            p = subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                 "-f", "s16le", "-acodec", "pcm_s16le",
                 "-ar", "16000", "-ac", "1", "pipe:1"],
                input=raw, capture_output=True, check=True,
            )
            return p.stdout
        except Exception as exc:
            raise RuntimeError("Edge TTS requires ffmpeg for PCM conversion") from exc

    raise ValueError(f"unsupported TTS engine: {engine}")


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
        return {"audio": "", "format": "wav", "media_type": "audio/wav", "sample_rate": 16_000}
    try:
        pcm = await asyncio.to_thread(_synthesize_sync, text, voice or get_settings().tts_voice)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    wav = _pcm_to_wav(pcm, sample_rate=16_000)
    return {
        "audio": wav.hex(), "format": "wav", "media_type": "audio/wav", "sample_rate": 16_000
    }


@app.websocket("/voice/speak")
async def voice_ws(ws: WebSocket) -> None:
    await ws.accept()
    active: asyncio.Task | None = None

    async def speak(text: str) -> None:
        try:
            pcm = await asyncio.wait_for(
                asyncio.to_thread(_synthesize_sync, text, get_settings().tts_voice),
                timeout=get_settings().request_timeout_seconds,
            )
        except (RuntimeError, ValueError) as exc:
            await ws.send_json({"type": "error", "code": "tts_unavailable", "message": str(exc)})
            return
        except TimeoutError:
            await ws.send_json({"type": "error", "code": "tts_timeout", "message": "Speech synthesis timed out"})
            return
        wav = _pcm_to_wav(pcm, sample_rate=16_000)
        await ws.send_json(
            {"type": "audio", "format": "wav", "media_type": "audio/wav",
             "sample_rate": 16_000, "data": wav.hex()}
        )

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if "text" in msg:
                payload = json.loads(msg["text"])
                if payload.get("type") == "speak":
                    text = payload.get("text", "").strip()
                    if active and not active.done():
                        active.cancel()
                    active = asyncio.create_task(speak(text))
                elif payload.get("type") == "barge_in":
                    if active and not active.done():
                        active.cancel()
                    await ws.send_json({"type": "cancelled"})
    except WebSocketDisconnect:
        if active and not active.done():
            active.cancel()


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "engine": get_settings().tts_engine}
