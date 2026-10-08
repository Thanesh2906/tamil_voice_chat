from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    agent_id: str = Field(default="manager", max_length=32)
    message: str = Field(min_length=1, max_length=20_000)
    project_id: Optional[str] = None
    mode: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    monitoring_window: str = "15m"


class RunEventOut(BaseModel):
    sequence: int
    type: str
    data: Dict[str, Any]
    created_at: str


class RunOut(BaseModel):
    id: str
    agent_id: str = "manager"
    execution_agent_id: str = "manager"
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None
    status: str
    input_text: str
    created_at: str
    completed_at: Optional[str] = None
    events: List[RunEventOut] = Field(default_factory=list)


class RunSummary(BaseModel):
    id: str
    agent_id: str = "manager"
    execution_agent_id: str = "manager"
    project_id: Optional[str] = None
    conversation_id: Optional[str] = None
    status: str
    input_preview: str
    created_at: str
    completed_at: Optional[str] = None
