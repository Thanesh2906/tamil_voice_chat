"""Small async HTTP helpers with retry and timeout."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx


async def post_json(
    url: str,
    payload: Dict[str, Any],
    *,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload, headers=headers or {})
        r.raise_for_status()
        return r.json()


async def get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params, headers=headers or {})
        r.raise_for_status()
        return r.json()


async def stream_post(
    url: str,
    payload: Dict[str, Any],
    *,
    timeout: float = 60.0,
) -> Any:
    """Yield decoded JSON lines from an NDJSON / SSE-style stream."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line:
                    yield line
