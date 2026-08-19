from __future__ import annotations

import asyncio
import struct
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol


class SttAdapter(Protocol):
    async def transcribe(self, pcm16: bytes, sample_rate: int, locale: str) -> str: ...


class LlmAdapter(Protocol):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...


class TtsAdapter(Protocol):
    async def synthesize(self, text: str, locale: str) -> tuple[bytes, str]: ...


@dataclass(slots=True)
class FakeStt:
    transcript: str = "வணக்கம், ஜார்விஸ்"

    async def transcribe(self, pcm16: bytes, sample_rate: int, locale: str) -> str:
        if sample_rate <= 0 or not pcm16:
            raise ValueError("audio and a valid sample rate are required")
        await asyncio.sleep(0)
        return self.transcript


def normalize_pcm16_mono(pcm16: bytes, sample_rate: int, target_rate: int = 16_000) -> bytes:
    """Linearly resample signed little-endian mono PCM16 without model dependencies."""
    if len(pcm16) % 2 or sample_rate <= 0 or target_rate <= 0:
        raise ValueError("valid PCM16 mono audio and sample rates are required")
    if sample_rate == target_rate or not pcm16:
        return pcm16
    samples = struct.unpack(f"<{len(pcm16) // 2}h", pcm16)
    output_length = max(1, round(len(samples) * target_rate / sample_rate))
    output = []
    for index in range(output_length):
        source = index * sample_rate / target_rate
        left = min(int(source), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = source - left
        output.append(round(samples[left] * (1 - fraction) + samples[right] * fraction))
    return struct.pack(f"<{len(output)}h", *output)


@dataclass(slots=True)
class FakeLlm:
    response: str = "வணக்கம்! நான் எப்படி உதவலாம்?"

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        if not messages or messages[0].get("role") != "system":
            raise ValueError("system prompt is required")
        for token in self.response.split(" "):
            await asyncio.sleep(0)
            yield token + " "


class FakeTts:
    async def synthesize(self, text: str, locale: str) -> tuple[bytes, str]:
        frames = b"\x00\x00" * max(320, len(text) * 80)
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(frames)
        return output.getvalue(), "audio/wav"


def load_system_prompt(path: Path = Path("prompts/system.txt")) -> str:
    return path.read_text(encoding="utf-8").strip()
