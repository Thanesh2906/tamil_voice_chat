from __future__ import annotations

import asyncio
import json

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel, EmailStr, Field

from .adapters import FakeLlm, FakeStt, FakeTts, load_system_prompt
from .auth_service import AuthError, AuthService
from .config import Settings
from .database import Database
from .rag import MemoryRagStore
from .security import TokenClaims, TokenError, decode_token
from .voice import VoiceSession

REQUESTS = Counter("jarvis_http_requests_total", "HTTP requests", ["route"])
VOICE_LATENCY = Histogram("jarvis_voice_turn_seconds", "Voice turn latency")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    tenant_name: str = Field(min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    project_id: str | None = None


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    root_path: str | None = Field(default=None, max_length=500)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    db = Database(settings.database_path)
    auth = AuthService(db, settings)
    rag = MemoryRagStore()
    stt, llm, tts = FakeStt(), FakeLlm(), FakeTts()
    system_prompt = load_system_prompt()
    app = FastAPI(title="JARVIS Tamil Voice Assistant", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.mount("/metrics", make_asgi_app())

    def current_user(authorization: str = Header(default="")) -> TokenClaims:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer token required")
        try:
            return decode_token(authorization[7:], settings.jwt_secret)
        except TokenError as exc:
            raise HTTPException(401, str(exc)) from exc

    def require_scope(claims: TokenClaims, scope: str) -> None:
        if scope not in claims.scopes:
            raise HTTPException(403, f"scope required: {scope}")

    def authorize_project(claims: TokenClaims, project_id: str) -> None:
        member = db.one(
            "SELECT 1 FROM project_members pm JOIN projects p ON p.id=pm.project_id WHERE pm.project_id=? AND pm.user_id=? AND p.tenant_id=?",
            (project_id, claims.subject, claims.tenant_id),
        )
        if not member:
            raise HTTPException(403, "project membership required")

    @app.get("/health/live")
    async def live() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready() -> dict:
        db.one("SELECT 1")
        return {"status": "ready"}

    @app.post("/auth/register", status_code=201)
    async def register(payload: RegisterRequest) -> dict:
        try:
            return auth.register(payload.email, payload.password, payload.tenant_name)
        except AuthError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/auth/login")
    async def login(payload: LoginRequest) -> dict:
        try:
            return auth.login(payload.email, payload.password)
        except AuthError as exc:
            raise HTTPException(401, str(exc)) from exc

    @app.post("/auth/refresh")
    async def refresh(payload: RefreshRequest) -> dict:
        try:
            return auth.rotate(payload.refresh_token)
        except (AuthError, TokenError) as exc:
            raise HTTPException(401, str(exc)) from exc

    @app.get("/projects")
    async def projects(claims: TokenClaims = Depends(current_user)) -> list[dict]:
        require_scope(claims, "projects:read")
        rows = db.all(
            "SELECT p.id,p.name FROM projects p JOIN project_members pm ON pm.project_id=p.id WHERE pm.user_id=? AND p.tenant_id=?",
            (claims.subject, claims.tenant_id),
        )
        return [dict(row) for row in rows]

    @app.post("/projects", status_code=201)
    async def create_project(
        payload: ProjectRequest, claims: TokenClaims = Depends(current_user)
    ) -> dict:
        require_scope(claims, "projects:read")
        import uuid

        project_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO projects(id,tenant_id,name,root_path) VALUES(?,?,?,?)",
            (project_id, claims.tenant_id, payload.name, payload.root_path),
        )
        db.execute(
            "INSERT INTO project_members(project_id,user_id,role) VALUES(?,?,?)",
            (project_id, claims.subject, "owner"),
        )
        db.audit(claims.tenant_id, claims.subject, "project.created", project_id)
        return {"id": project_id, "name": payload.name}

    @app.post("/chat")
    async def chat(payload: ChatRequest, claims: TokenClaims = Depends(current_user)) -> dict:
        require_scope(claims, "chat")
        chunks = []
        if payload.project_id:
            authorize_project(claims, payload.project_id)
            chunks = rag.retrieve(claims.tenant_id, payload.project_id, payload.message)
        context = "\n\n".join(f"{c.path}:{c.start_line}-{c.end_line}\n{c.text}" for c in chunks)
        content = (
            payload.message
            if not context
            else f"Authorized context:\n{context}\n\nUser request:\n{payload.message}"
        )
        tokens = [
            token
            async for token in llm.stream(
                [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
            )
        ]
        return {
            "answer": "".join(tokens).strip(),
            "citations": [
                {"path": c.path, "start_line": c.start_line, "end_line": c.end_line} for c in chunks
            ],
        }

    @app.websocket("/voice/session")
    async def voice(websocket: WebSocket) -> None:
        protocol = websocket.headers.get("sec-websocket-protocol", "")
        parts = [part.strip() for part in protocol.split(",")]
        token = parts[1] if len(parts) == 2 and parts[0] == "jarvis-bearer" else ""
        try:
            claims = decode_token(token, settings.jwt_secret)
            require_scope(claims, "voice")
        except (TokenError, HTTPException):
            await websocket.close(code=4401)
            return
        await websocket.accept(subprotocol="jarvis-bearer")
        session = VoiceSession(stt, llm, tts, system_prompt, settings.max_audio_bytes)

        async def send(event: dict) -> None:
            await websocket.send_text(json.dumps(event, ensure_ascii=False))

        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    await session.cancel(lambda _: asyncio.sleep(0))
                    return
                if message.get("bytes") is not None:
                    session.add_audio(message["bytes"])
                    continue
                if message.get("text") is None:
                    continue
                payload = json.loads(message["text"])
                kind = payload.get("type")
                if kind == "start":
                    if payload.get("project_id"):
                        authorize_project(claims, str(payload["project_id"]))
                    await send(session.start(payload))
                elif kind == "stop":
                    with VOICE_LATENCY.time():
                        await session.stop(send)
                elif kind in {"cancel", "barge_in"}:
                    await session.cancel(send)
                else:
                    await send({"type": "error", "code": "unsupported_event"})
        except WebSocketDisconnect:
            await session.cancel(lambda _: asyncio.sleep(0))
        except (ValueError, json.JSONDecodeError) as exc:
            await send({"type": "error", "code": "invalid_request", "message": str(exc)})
            await websocket.close(code=4400)

    return app


app = create_app()
