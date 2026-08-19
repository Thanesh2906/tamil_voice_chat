"""FastAPI gateway: auth + agent orchestrator + voice WebSocket.

This is the only service that the mobile/web clients talk to. It
forwards STT frames to the STT service, runs retrieval against RAG,
queries the monitoring service, streams tokens from the LLM, and
synthesizes audio with the TTS service — all gated by an authenticated
session and authorization filters (blueprint §4 + §7).
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

import jwt as pyjwt

from packages.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
    TokenError,
)
from packages.common import configure_logging, get_logger, get_settings
from packages.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserPublic,
)
from packages.schemas.chat import ChatRequest, ChatResponse, Citation, ToolCall

configure_logging()
log = get_logger("api")

app = FastAPI(title="Jarvis API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- user store (placeholder; swap for real DB) ---------------------

@dataclass
class _User:
    id: str
    email: str
    display_name: str
    password_hash: str
    preferred_language: str
    scopes: List[str]


class _UserStore:
    def __init__(self) -> None:
        self._by_email: Dict[str, _User] = {}
        self._by_id: Dict[str, _User] = {}
        self._refresh: Dict[str, str] = {}  # jti -> user_id

    def create(self, *, email: str, password: str, display_name: str, preferred_language: str = "ta") -> _User:
        if email in self._by_email:
            raise ValueError("user exists")
        u = _User(
            id=secrets.token_urlsafe(12),
            email=email,
            display_name=display_name,
            password_hash=hash_password(password),
            preferred_language=preferred_language,
            scopes=["chat", "rag", "monitoring:read"],
        )
        self._by_email[email] = u
        self._by_id[u.id] = u
        return u

    def get(self, user_id: str) -> Optional[_User]:
        return self._by_id.get(user_id)

    def find(self, email: str) -> Optional[_User]:
        return self._by_email.get(email)

    def remember_refresh(self, jti: str, user_id: str) -> None:
        self._refresh[jti] = user_id

    def take_refresh(self, jti: str) -> Optional[str]:
        return self._refresh.pop(jti, None)


users = _UserStore()
# Seed a dev account so the demo works out of the box.
try:
    users.create(email="dev@jarvis.local", password="changeme123", display_name="Dev", preferred_language="ta")
except ValueError:
    pass


# ---------- dependencies ----------------------------------------------------

async def current_user(authorization: Optional[str] = Header(default=None)) -> _User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(None, 1)[1]
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    user = users.get(payload["sub"])
    if user is None:
        raise HTTPException(status_code=401, detail="unknown subject")
    return user


# ---------- HTTP routes ----------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "service": "api"}


@app.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest) -> TokenResponse:
    user = users.find(req.email)
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    pair = create_access_token(user.id, scopes=user.scopes)
    refresh = create_refresh_token(user.id)
    try:
        claims = pyjwt.decode(refresh, get_settings().jwt_secret, algorithms=["HS256"])
        users.remember_refresh(claims["jti"], user.id)
    except pyjwt.PyJWTError:
        pass
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=refresh,
        expires_in=pair.expires_in,
    )


@app.post("/auth/refresh", response_model=TokenResponse)
async def refresh(req: RefreshRequest) -> TokenResponse:
    try:
        claims = pyjwt.decode(req.refresh_token, get_settings().jwt_secret, algorithms=["HS256"])
    except pyjwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="wrong token type")
    user_id = users.take_refresh(claims.get("jti", ""))
    if user_id != claims["sub"]:
        raise HTTPException(status_code=401, detail="refresh not recognized")
    user = users.get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unknown subject")
    pair = create_access_token(user.id, scopes=user.scopes)
    new_refresh = create_refresh_token(user.id)
    new_claims = pyjwt.decode(new_refresh, get_settings().jwt_secret, algorithms=["HS256"])
    users.remember_refresh(new_claims["jti"], user.id)
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=new_refresh,
        expires_in=pair.expires_in,
    )


@app.get("/me", response_model=UserPublic)
async def me(user: _User = Depends(current_user)) -> UserPublic:
    return UserPublic(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        preferred_language=user.preferred_language,
        scopes=user.scopes,
    )


@app.get("/projects")
async def list_projects(user: _User = Depends(current_user)) -> List[Dict[str, Any]]:
    # TODO: read from Postgres. Phase 1 ships empty list + scaffold.
    return [{"id": f"project-{user.id[:6]}", "name": "Personal", "scope": user.id}]


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: _User = Depends(current_user)) -> ChatResponse:
    request_id = secrets.token_urlsafe(8)
    context_chunks: List[Dict[str, Any]] = []
    tool_calls: List[ToolCall] = []

    # 1) Optional RAG retrieval.
    if req.project_id:
        try:
            r = await httpx.AsyncClient(timeout=15.0).get(
                f"{get_settings().qdrant_url}/collections/{get_settings().qdrant_collection}/points/search",
                params=None,
                json={
                    "vector": [0.0] * 384,  # placeholder; orchestrator will swap for real embedder
                    "limit": 0,
                },
            )
            # Real implementation goes through services/rag.retrieve(...).
        except httpx.HTTPError:
            pass

    # 2) Compose final answer by streaming the LLM.
    messages = [
        {"role": "user", "content": req.message},
    ]
    pieces: List[str] = []
    async for tok in _stream_llm(messages):
        pieces.append(tok)
    answer = "".join(pieces)

    citations = [
        Citation(source_id=c.get("source_id", ""), file_path=c.get("file_path"), snippet=c.get("snippet"))
        for c in context_chunks
    ]
    return ChatResponse(
        answer=answer,
        language=req.language or user.preferred_language,
        citations=citations,
        tool_calls=tool_calls,
        request_id=request_id,
        session_id=req.session_id,
    )


@app.post("/rag/index")
async def rag_index(req: dict, user: _User = Depends(current_user)) -> dict:
    """Forward to the RAG service with the current user's authorization scope."""
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(
            "http://rag:8000/rag/index",
            params={"tenant_id": user.id, "owner_id": user.id},
            json=req,
        )
        r.raise_for_status()
        return r.json()


@app.get("/rag/sources")
async def rag_sources(q: str, project_id: str, user: _User = Depends(current_user)) -> List[dict]:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(
            "http://rag:8000/rag/sources",
            params={"q": q, "project_id": project_id, "tenant_id": user.id, "owner_id": user.id},
        )
        r.raise_for_status()
        return r.json()


@app.get("/monitoring/summary")
async def monitoring_summary(window: str = "15m", user: _User = Depends(current_user)) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get("http://monitoring:8000/monitoring/summary", params={"window": window})
        r.raise_for_status()
        return r.json()


@app.get("/monitoring/projects/{project_id}")
async def project_summary(project_id: str, window: str = "15m", user: _User = Depends(current_user)) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(
            f"http://monitoring:8000/monitoring/projects/{project_id}", params={"window": window}
        )
        r.raise_for_status()
        return r.json()


@app.post("/feedback")
async def feedback(payload: dict, user: _User = Depends(current_user)) -> dict:
    # Persist via Postgres in phase 2. Phase 1 logs only.
    log.info("feedback user=%s payload=%s", user.id, payload)
    return {"ok": True}


# ---------- voice WebSocket -------------------------------------------------

@app.websocket("/voice/session")
async def voice_session(ws: WebSocket, token: Optional[str] = None) -> None:
    await ws.accept()
    user: Optional[_User] = None
    if token:
        try:
            payload = decode_token(token, expected_type="access")
            user = users.get(payload["sub"])
        except TokenError:
            pass
    if user is None:
        # try ?token=... header alternative
        await ws.close(code=4401)
        return

    session_id = secrets.token_urlsafe(8)
    request_id = secrets.token_urlsafe(8)

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if "text" not in msg:
                continue
            payload = json.loads(msg["text"])
            kind = payload.get("type")
            if kind == "start":
                await ws.send_json(
                    {
                        "type": "ready",
                        "request_id": request_id,
                        "session_id": session_id,
                        "language": user.preferred_language,
                    }
                )
            elif kind == "stop":
                # Hand off to the LLM service for final answer, then stream tokens.
                transcript = payload.get("transcript", "")
                if not transcript:
                    continue
                await ws.send_json({"type": "transcript", "request_id": request_id, "session_id": session_id,
                                     "data": {"text": transcript, "final": True}})
                await _voice_reply(ws, request_id=request_id, session_id=session_id, message=transcript)
            elif kind == "barge_in":
                # TTS service listens for these on its own WS; here we just stop our local state.
                pass
    except WebSocketDisconnect:
        pass


async def _voice_reply(ws: WebSocket, *, request_id: str, session_id: str, message: str) -> None:
    """Stream tokens + a final audio chunk back to the client."""
    pieces: List[str] = []
    async for tok in _stream_llm([{"role": "user", "content": message}]):
        pieces.append(tok)
        await ws.send_json(
            {"type": "token", "request_id": request_id, "session_id": session_id, "data": {"text": tok}}
        )
    answer = "".join(pieces)
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post("http://tts:8000/synthesize", json={"text": answer})
            r.raise_for_status()
            audio = r.json().get("audio", "")
            await ws.send_json(
                {
                    "type": "audio",
                    "request_id": request_id,
                    "session_id": session_id,
                    "data": {"format": "wav", "sample_rate": 16_000, "hex": audio},
                }
            )
    except httpx.HTTPError:
        await ws.send_json(
            {"type": "error", "request_id": request_id, "session_id": session_id,
             "data": {"detail": "tts unavailable"}}
        )
    await ws.send_json({"type": "final", "request_id": request_id, "session_id": session_id, "data": {}})


async def _stream_llm(messages: List[Dict[str, str]]) -> AsyncIterator[str]:
    s = get_settings()
    payload = {
        "model": s.llm_model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.2},
    }
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", f"{s.llm_base_url.rstrip('/')}/api/chat", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tok = obj.get("message", {}).get("content")
                if tok:
                    yield tok
                if obj.get("done"):
                    return
