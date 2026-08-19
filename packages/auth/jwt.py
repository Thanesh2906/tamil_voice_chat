"""Short-lived access tokens + rotating refresh tokens."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import jwt

from packages.common.config import get_settings


class TokenError(Exception):
    pass


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def create_access_token(
    subject: str,
    *,
    scopes: Optional[list] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> TokenPair:
    s = get_settings()
    now = int(time.time())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + s.jwt_access_ttl_seconds,
        "type": "access",
        "scopes": scopes or [],
    }
    if extra:
        payload.update(extra)
    access = jwt.encode(payload, s.jwt_secret, algorithm="HS256")
    refresh = secrets.token_urlsafe(48)
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=s.jwt_access_ttl_seconds)


def create_refresh_token(subject: str) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + s.jwt_refresh_ttl_seconds,
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str, *, expected_type: Optional[str] = None) -> Dict[str, Any]:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise TokenError(str(e)) from e
    if expected_type and payload.get("type") != expected_type:
        raise TokenError("unexpected token type")
    return payload
