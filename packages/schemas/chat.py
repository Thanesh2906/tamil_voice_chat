from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    agent_id: str = Field(default="manager", max_length=32)
    session_id: str
    message: str
    language: Optional[str] = None
    project_id: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    # Pin an exact model ("use Claude for this"); omit to let the router pick a
    # default for the resolved mode. See services/llm/router.py.
    provider: Optional[str] = None
    model: Optional[str] = None


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
    agent_id: str = "manager"
    execution_agent_id: str = "manager"
    answer: str
    language: str
    citations: List[Citation] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    request_id: str
    session_id: str
    # Which provider/model actually answered, and why the router picked it.
    # Absent when a test double bypasses the real router.
    provider: Optional[str] = None
    model: Optional[str] = None
    routing_reason: Optional[str] = None
