from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ToolInvokeRequest(BaseModel):
    tool_name: str = Field(min_length=1, max_length=64)
    project_id: str
    args: Dict[str, Any] = Field(default_factory=dict)


class ToolInvocationOut(BaseModel):
    id: str
    tool_name: str
    risk: str
    status: str
    args: Dict[str, Any]
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: str
    decided_at: Optional[str] = None
    run_id: Optional[str] = None


class ToolListEntry(BaseModel):
    name: str
    risk: str
    description: str


class ToolListResponse(BaseModel):
    tools: List[ToolListEntry]
