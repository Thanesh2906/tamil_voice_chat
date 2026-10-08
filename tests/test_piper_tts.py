from __future__ import annotations

import io
import sys
import wave
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from services import tts


@dataclass
class AudioChunk:
    """The public Piper AudioChunk fields used by the adapter (no native model)."""
    sample_rate: int
    audio_float_array: np.ndarray
    sample_width: int = 2
    sample_channels: int = 1

    @property
    def audio_int16_bytes(self) -> bytes:
        return np.clip(self.audio_float_array * 32767, -32767, 32767).astype("<i2").tobytes()


@pytest.fixture
def voice_setup(tmp_path, monkeypatch):
    model = tmp_path / "test-voice.onnx"
    model.write_bytes(b"unit-test-placeholder-not-a-model")
    model.with_suffix(".onnx.json").write_text('{"audio":{"sample_rate":22050}}')
    settings = SimpleNamespace(tts_engine="piper", tts_voice=str(model), request_timeout_seconds=5)
    monkeypatch.setattr(tts, "get_settings", lambda: settings)
    tts._PIPER_VOICES.clear()
    loads = []

    def install(runtime):
        def load(path):
            loads.append(path)
            return runtime
        monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=SimpleNamespace(load=load)))

    yield settings, install, loads
    tts._PIPER_VOICES.clear()


def test_current_piper_chunks_resample_and_emit_honest_wav_metadata(voice_setup) -> None:
    settings, install, loads = voice_setup

    def synthesize(text):
        assert text == "வணக்கம்"
        yield AudioChunk(22_050, np.full(2205, 0.25, dtype=np.float32))
        yield AudioChunk(22_050, np.full(2205, -0.25, dtype=np.float32))

    install(SimpleNamespace(synthesize=synthesize))
    client = TestClient(tts.app)
    for _ in range(2):
        response = client.post("/synthesize", json={"text": "வணக்கம்"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["sample_rate"] == 16_000
        assert payload["media_type"] == "audio/wav"
        with wave.open(io.BytesIO(bytes.fromhex(payload["audio"])), "rb") as wav:
            assert wav.getframerate() == 16_000
            assert wav.getsampwidth() == 2
            assert wav.getnchannels() == 1
            assert wav.getnframes() == 3200
            samples = np.frombuffer(wav.readframes(3200), dtype="<i2")
            assert (samples[:1600] > 0).all()
            assert (samples[1600:] < 0).all()
    assert loads == [settings.tts_voice]


def test_legacy_piper_raw_generator_uses_configured_sample_rate(voice_setup) -> None:
    settings, install, _ = voice_setup
    raw = np.arange(3200, dtype="<i2").tobytes()

    def synthesize_stream_raw(_text):
        yield raw
        yield raw

    install(SimpleNamespace(config=SimpleNamespace(sample_rate=32_000), synthesize_stream_raw=synthesize_stream_raw))
    pcm = tts._synthesize_sync("legacy", settings.tts_voice)
    assert len(pcm) == 3200 * 2  # Two 0.1-second chunks at 16k mono PCM16.


def test_native_16k_audio_is_preserved(voice_setup) -> None:
    settings, install, _ = voice_setup
    chunk = AudioChunk(16_000, np.array([0.0, 0.5, -0.5], dtype=np.float32))
    install(SimpleNamespace(synthesize=lambda _text: iter([chunk])))
    assert tts._synthesize_sync("short", settings.tts_voice) == chunk.audio_int16_bytes


def test_placeholder_voice_returns_actionable_sanitized_setup_error(voice_setup) -> None:
    settings, _, _ = voice_setup
    settings.tts_voice = "ta-IN-default"
    response = TestClient(tts.app).post("/synthesize", json={"text": "hello"})
    assert response.status_code == 503
    assert "Set TTS_VOICE" in response.json()["detail"]
    assert ".onnx.json" in response.json()["detail"]
    assert "audio" not in response.json()


def test_missing_voice_configuration_does_not_echo_private_path(voice_setup) -> None:
    from pathlib import Path

    settings, _, _ = voice_setup
    Path(f"{settings.tts_voice}.json").unlink()
    response = TestClient(tts.app).post("/synthesize", json={"text": "hello"})
    assert response.status_code == 503
    assert settings.tts_voice not in response.text


def test_native_load_failure_does_not_expose_runtime_details(voice_setup, monkeypatch) -> None:
    settings, _, _ = voice_setup

    def load(_path):
        raise RuntimeError("private runtime path /home/user/secret-model.onnx")

    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=SimpleNamespace(load=load)))
    with pytest.raises(tts.TTSUnavailableError, match="verify the installed voice model") as error:
        tts._synthesize_sync("hello", settings.tts_voice)
    assert "private runtime" not in str(error.value)
    assert "secret-model" not in str(error.value)


@pytest.mark.parametrize("metadata", [
    {"sample_rate": 0}, {"sample_rate": -1}, {"sample_width": 4}, {"sample_channels": 2},
])
def test_invalid_chunk_metadata_is_rejected(voice_setup, metadata) -> None:
    settings, install, _ = voice_setup
    chunk = AudioChunk(16_000, np.ones(10, dtype=np.float32))
    for field, value in metadata.items():
        setattr(chunk, field, value)
    install(SimpleNamespace(synthesize=lambda _text: iter([chunk])))
    with pytest.raises(tts.TTSUnavailableError, match="unsupported audio format"):
        tts._synthesize_sync("hello", settings.tts_voice)


def test_empty_generator_does_not_pretend_to_speak(voice_setup) -> None:
    settings, install, _ = voice_setup
    install(SimpleNamespace(synthesize=lambda _text: iter([])))
    with pytest.raises(tts.TTSUnavailableError, match="produced no audio"):
        tts._synthesize_sync("hello", settings.tts_voice)


def test_websocket_reports_missing_voice_instead_of_hanging(voice_setup) -> None:
    settings, _, _ = voice_setup
    settings.tts_voice = "ta-IN-default"
    with TestClient(tts.app).websocket_connect("/voice/speak") as websocket:
        websocket.send_json({"type": "speak", "text": "hello"})
        message = websocket.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "tts_unavailable"
        assert "Set TTS_VOICE" in message["message"]
