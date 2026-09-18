from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    tenant_name: str = Field(min_length=1, max_length=160)
    preferred_language: str = Field(default="ta", min_length=2, max_length=16)


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    root_path: Optional[str] = Field(default=None, max_length=2048)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserPublic(BaseModel):
    id: str
    email: str
    display_name: str
    preferred_language: str = "ta"
    scopes: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
