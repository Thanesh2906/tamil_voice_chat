from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
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
    status: str
    input_text: str
    created_at: str
    completed_at: Optional[str] = None
    events: List[RunEventOut] = Field(default_factory=list)


class RunSummary(BaseModel):
    id: str
    status: str
    input_preview: str
    created_at: str
    completed_at: Optional[str] = None
