from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    project_id: str
    paths: List[str]
    branch: Optional[str] = None
    recursive: bool = True


class IngestResponse(BaseModel):
    project_id: str
    files_seen: int
    chunks_indexed: int
    skipped: List[str] = Field(default_factory=list)
    indexed_files: List[dict[str, Any]] = Field(default_factory=list)


class RetrievedChunk(BaseModel):
    chunk_id: str
    file_path: Optional[str] = None
    language: Optional[str] = None
    snippet: str
    score: float
    project_id: str
    tenant_id: str
    owner_id: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    content_hash: Optional[str] = None
