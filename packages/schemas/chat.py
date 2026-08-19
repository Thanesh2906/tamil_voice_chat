from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str
    message: str
    language: Optional[str] = None
    project_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    source_id: str
    file_path: Optional[str] = None
    snippet: Optional[str] = None
    score: Optional[float] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None


class ToolCall(BaseModel):
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)
    status: str = "completed"
    output: Optional[Any] = None


class ChatResponse(BaseModel):
    answer: str
    language: str
    citations: List[Citation] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    request_id: str
    session_id: str
