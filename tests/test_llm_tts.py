from __future__ import annotations

import io
import wave

from services.llm import compose_system_prompt
from services.tts import _pcm_to_wav


def test_system_prompt_applies_mode_and_marks_context_untrusted() -> None:
    prompt = compose_system_prompt("rag", "reference data")
    assert "JARVIS" in prompt.upper()
    assert "<authorized_context>" in prompt
    assert "untrusted reference data" in prompt


def test_wav_media_is_really_pcm_wav() -> None:
    encoded = _pcm_to_wav(b"\x00\x00" * 10)
    with wave.open(io.BytesIO(encoded), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16_000
