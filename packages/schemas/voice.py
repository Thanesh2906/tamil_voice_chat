from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class VoiceFrame(BaseModel):
    type: Literal["frame"] = "frame"
    request_id: str
    session_id: str
    sample_rate: int = 16_000
    pcm: bytes
    seq: int = 0


class VoiceStart(BaseModel):
    type: Literal["start"] = "start"
    request_id: str
    session_id: str
    language: Optional[str] = None


class VoiceStop(BaseModel):
    type: Literal["stop"] = "stop"
    request_id: str
    session_id: str


class VoiceEvent(BaseModel):
    """Server -> client events on the /voice/session WebSocket."""

    type: Literal[
        "partial",
        "transcript",
        "token",
        "tool",
        "audio",
        "citation",
        "final",
        "error",
        "barge_in",
    ]
    request_id: str
    session_id: str
    data: Dict[str, Any] = Field(default_factory=dict)
