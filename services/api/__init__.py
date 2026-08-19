"""Authenticated FastAPI gateway and cancellable voice orchestrator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, make_asgi_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.auth import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from packages.common import configure_logging, get_logger, get_settings
from packages.db import (
    AuditEvent,
    Conversation,
    Document,
    DocumentVersion,
    IngestionJob,
    Message,
    Project,
    ProjectMember,
    RefreshToken,
    Tenant,
    TenantMember,
    User,
    db_session,
    init_db,
    new_id,
    project_for_user,
    session_scope,
    token_hash,
)
from packages.schemas.auth import (
    CreateProjectRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from packages.schemas.chat import ChatRequest, ChatResponse, Citation, ToolCall
from services.api.adapters import ServiceAdapters, chunk_phrases
from services.api.router import route_agent

configure_logging()
log = get_logger("api")
settings = get_settings()
REQUESTS = Counter("jarvis_api_requests_total", "API requests", ["route", "status"])
VOICE_LATENCY = Histogram("jarvis_voice_stage_seconds", "Voice stage latency", ["stage"])


def _bootstrap(session: Session) -> None:
    """Create an explicitly configured development admin and personal project."""
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        return
    if session.scalar(select(User).where(User.email == settings.bootstrap_admin_email)):
        return
    user = User(id=new_id("usr"), email=settings.bootstrap_admin_email.lower(),
                display_name="Administrator", password_hash=hash_password(settings.bootstrap_admin_password),
                scopes_csv="admin,chat,rag,monitoring:read")
    tenant = Tenant(id=new_id("ten"), name="Personal")
    project = Project(id=new_id("prj"), tenant_id=tenant.id, name="Personal")
    session.add_all([user, tenant, TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"),
                     project, ProjectMember(project_id=project.id, user_id=user.id, role="owner")])
    session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime()
    await asyncio.to_thread(init_db)
    with session_scope() as session:
        _bootstrap(session)
    adapters = ServiceAdapters(settings)
    app.state.adapters = adapters
    try:
        yield
    finally:
        await adapters.aclose()


app = FastAPI(title="Jarvis API", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_credentials=True,
                   allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type"])
app.mount("/metrics", make_asgi_app())


@app.middleware("http")
async def metrics_middleware(request, call_next):
    try:
        response = await call_next(request)
        REQUESTS.labels(request.url.path, str(response.status_code)).inc()
        return response
    except Exception:
        REQUESTS.labels(request.url.path, "500").inc()
        raise


def _token_response(user: User, session: Session) -> TokenResponse:
    pair = create_access_token(user.id, scopes=user.scopes)
    refresh = create_refresh_token(user.id)
    claims = decode_token(refresh, expected_type="refresh")
    session.add(RefreshToken(jti_hash=token_hash(claims["jti"]), user_id=user.id,
                             expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc)))
    session.commit()
    return TokenResponse(access_token=pair.access_token, refresh_token=refresh, expires_in=pair.expires_in)


def current_user(authorization: str | None = Header(default=None),
                 session: Session = Depends(db_session)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = decode_token(authorization.split(None, 1)[1], expected_type="access")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired token") from exc
    user = session.get(User, claims.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="unknown subject")
    if not set(claims.get("scopes", [])).issubset(set(user.scopes)):
        raise HTTPException(status_code=401, detail="token scope is no longer authorized")
    return user


def require_scope(scope: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if scope not in user.scopes and "admin" not in user.scopes:
            raise HTTPException(status_code=403, detail=f"missing scope: {scope}")
        return user
    return dependency


def _authorized_project(session: Session, project_id: str, user: User) -> Project:
    project = project_for_user(session, project_id, user.id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "api", "version": app.version}


@app.get("/readyz")
def readyz(session: Session = Depends(db_session)) -> dict[str, bool]:
    session.execute(select(User.id).limit(1))
    return {"ready": True}


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, session: Session = Depends(db_session)) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == req.email.lower()))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    session.add(AuditEvent(id=new_id("aud"), user_id=user.id, action="auth.login"))
    return _token_response(user, session)


@app.post("/auth/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, session: Session = Depends(db_session)) -> TokenResponse:
    user = User(
        id=new_id("usr"),
        email=req.email.lower(),
        display_name=req.display_name,
        password_hash=hash_password(req.password),
        preferred_language=req.preferred_language,
        scopes_csv="chat,rag,monitoring:read",
    )
    tenant = Tenant(id=new_id("ten"), name=req.tenant_name)
    project = Project(id=new_id("prj"), tenant_id=tenant.id, name="Personal")
    session.add_all(
        [
            user,
            tenant,
            TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"),
            project,
            ProjectMember(project_id=project.id, user_id=user.id, role="owner"),
            AuditEvent(id=new_id("aud"), user_id=user.id, action="auth.register"),
        ]
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="email is already registered") from exc
    return _token_response(user, session)


@app.post("/auth/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest, session: Session = Depends(db_session)) -> TokenResponse:
    try:
        claims = decode_token(req.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired refresh token") from exc
    stored = session.get(RefreshToken, token_hash(claims.get("jti", "")))
    if not stored or stored.revoked_at is not None or stored.user_id != claims.get("sub"):
        raise HTTPException(status_code=401, detail="refresh token replay or revocation detected")
    stored.revoked_at = datetime.now(timezone.utc)
    user = session.get(User, stored.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="unknown subject")
    response = _token_response(user, session)
    replacement = decode_token(response.refresh_token, expected_type="refresh")
    stored.replaced_by_hash = token_hash(replacement["jti"])
    session.commit()
    return response


@app.get("/me", response_model=UserPublic)
def me(user: User = Depends(current_user)) -> UserPublic:
    return UserPublic(id=user.id, email=user.email, display_name=user.display_name,
                      preferred_language=user.preferred_language, scopes=user.scopes,
                      created_at=user.created_at)


@app.get("/projects")
def list_projects(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[dict[str, Any]]:
    rows = session.execute(select(Project, ProjectMember.role).join(ProjectMember).where(
        ProjectMember.user_id == user.id)).all()
    return [{"id": project.id, "name": project.name, "role": role} for project, role in rows]


@app.post("/projects", status_code=201)
def create_project(
    req: CreateProjectRequest,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> dict[str, Any]:
    tenant_id = session.scalar(
        select(TenantMember.tenant_id).where(TenantMember.user_id == user.id).limit(1)
    )
    if not tenant_id:
        raise HTTPException(status_code=403, detail="tenant membership required")
    project = Project(
        id=new_id("prj"), tenant_id=tenant_id, name=req.name, root_path=req.root_path
    )
    session.add_all(
        [
            project,
            ProjectMember(project_id=project.id, user_id=user.id, role="owner"),
            AuditEvent(
                id=new_id("aud"), user_id=user.id, action="project.created", target=project.id
            ),
        ]
    )
    session.commit()
    return {"id": project.id, "name": project.name, "role": "owner"}


def _citation(chunk: dict[str, Any]) -> Citation:
    return Citation(source_id=chunk.get("chunk_id", chunk.get("source_id", "")),
                    file_path=chunk.get("file_path"), snippet=chunk.get("snippet"),
                    score=chunk.get("score"), line_start=chunk.get("line_start"),
                    line_end=chunk.get("line_end"))


def _context(chunks: list[dict[str, Any]]) -> str | None:
    if not chunks:
        return None
    return "\n\n".join(f"[{item.get('file_path', 'source')}:{item.get('line_start', '?')}-"
                         f"{item.get('line_end', '?')}]\n{item.get('snippet', '')}" for item in chunks)


def _conversation_id(user_id: str, client_session_id: str) -> str:
    digest = hashlib.sha256(f"{user_id}:{client_session_id}".encode()).hexdigest()[:40]
    return f"con_{digest}"


def _record_message(
    session: Session,
    *,
    user_id: str,
    client_session_id: str,
    project_id: str | None,
    role: str,
    content: str,
) -> None:
    conversation_id = _conversation_id(user_id, client_session_id)
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        conversation = Conversation(
            id=conversation_id, user_id=user_id, project_id=project_id
        )
        session.add(conversation)
    session.add(
        Message(
            id=new_id("msg"),
            conversation_id=conversation_id,
            role=role,
            content=content,
        )
    )
    session.commit()


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, user: User = Depends(require_scope("chat")),
               session: Session = Depends(db_session)) -> ChatResponse:
    chunks: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []
    requested_mode = req.context.get("mode")
    decision = route_agent(
        req.message,
        project_id=req.project_id,
        requested_mode=str(requested_mode) if requested_mode else None,
        monitoring_window=str(req.context.get("window", "15m")),
    )
    adapters: ServiceAdapters = app.state.adapters
    project: Project | None = None
    if req.project_id:
        project = _authorized_project(session, req.project_id, user)
        if decision.mode in {"rag", "coding"}:
            chunks = await adapters.retrieve(req.message, project.id, project.tenant_id, user.id)
    context = _context(chunks)
    if decision.tool_name:
        if "monitoring:read" not in user.scopes and "admin" not in user.scopes:
            raise HTTPException(status_code=403, detail="missing scope: monitoring:read")
        output = await adapters.monitoring_tool(decision.tool_name, decision.tool_args)
        tool_calls.append(
            ToolCall(name=decision.tool_name, args=decision.tool_args, output=output)
        )
        context = (
            (context + "\n\n" if context else "")
            + "Authorized read-only monitoring result:\n"
            + json.dumps(output, ensure_ascii=False)
        )
    _record_message(
        session,
        user_id=user.id,
        client_session_id=req.session_id,
        project_id=req.project_id,
        role="user",
        content=req.message,
    )
    parts: list[str] = []
    async for token in adapters.llm(
        [{"role": "user", "content": req.message}],
        mode=decision.mode,
        context=context,
    ):
        parts.append(token)
    answer = "".join(parts)
    _record_message(
        session,
        user_id=user.id,
        client_session_id=req.session_id,
        project_id=req.project_id,
        role="assistant",
        content=answer,
    )
    return ChatResponse(
        answer=answer,
        language=req.language or user.preferred_language,
        citations=[_citation(chunk) for chunk in chunks],
        tool_calls=tool_calls,
        request_id=secrets.token_urlsafe(8),
        session_id=req.session_id,
    )


@app.post("/rag/index")
async def rag_index(req: dict, user: User = Depends(require_scope("rag")),
                    session: Session = Depends(db_session)) -> dict:
    import httpx
    project = _authorized_project(session, str(req.get("project_id", "")), user)
    job = IngestionJob(id=new_id("ing"), project_id=project.id, status="running")
    session.add(job)
    session.commit()
    try:
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            response = await client.post(
                f"{settings.rag_url.rstrip('/')}/rag/index",
                params={"tenant_id": project.tenant_id, "owner_id": user.id},
                json=req,
            )
            response.raise_for_status()
            result = response.json()
        for indexed in result.get("indexed_files", []):
            path = str(indexed["path"])
            content_hash = str(indexed["content_hash"])
            document = session.scalar(
                select(Document).where(
                    Document.project_id == project.id, Document.path == path
                )
            )
            if document is None:
                document = Document(id=new_id("doc"), project_id=project.id, path=path)
                session.add(document)
                session.flush()
            exists = session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.content_hash == content_hash,
                )
            )
            if exists is None:
                session.add(
                    DocumentVersion(
                        id=new_id("ver"),
                        document_id=document.id,
                        content_hash=content_hash,
                    )
                )
        job.status = "completed"
        job.detail = json.dumps(
            {"files_seen": result.get("files_seen"), "chunks_indexed": result.get("chunks_indexed")}
        )
        session.add(
            AuditEvent(
                id=new_id("aud"), user_id=user.id, action="rag.indexed", target=project.id
            )
        )
        session.commit()
        return {**result, "job_id": job.id}
    except Exception as exc:
        job.status = "failed"
        job.detail = exc.__class__.__name__
        session.commit()
        raise


@app.get("/rag/sources")
async def rag_sources(q: str, project_id: str, user: User = Depends(require_scope("rag")),
                      session: Session = Depends(db_session)) -> list[dict]:
    project = _authorized_project(session, project_id, user)
    return await app.state.adapters.retrieve(q, project.id, project.tenant_id, user.id)


@app.get("/monitoring/summary")
async def monitoring_summary(window: str = "15m", user: User = Depends(require_scope("monitoring:read"))) -> dict:
    import httpx
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{settings.monitoring_url.rstrip('/')}/monitoring/summary",
                                    params={"window": window})
        response.raise_for_status()
        return response.json()


@app.get("/monitoring/projects/{project_id}")
async def project_summary(project_id: str, window: str = "15m",
                          user: User = Depends(require_scope("monitoring:read")),
                          session: Session = Depends(db_session)) -> dict:
    import httpx
    _authorized_project(session, project_id, user)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{settings.monitoring_url.rstrip('/')}/monitoring/projects/{project_id}",
                                    params={"window": window})
        response.raise_for_status()
        return response.json()


@app.post("/feedback")
def feedback(payload: dict, user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, bool]:
    session.add(AuditEvent(id=new_id("aud"), user_id=user.id, action="feedback.created", detail="payload redacted"))
    session.commit()
    return {"ok": True}


async def _ws_user(ws: WebSocket) -> User | None:
    """Authenticate with the first frame so credentials never appear in URLs."""
    try:
        event = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        if event.get("type") != "auth":
            return None
        claims = decode_token(str(event.get("access_token", "")), expected_type="access")
        with session_scope() as session:
            user = session.get(User, claims.get("sub"))
            if not user or "chat" not in user.scopes:
                return None
            session.expunge(user)
            return user
    except (asyncio.TimeoutError, json.JSONDecodeError, TokenError):
        return None


async def _voice_reply(ws: WebSocket, adapters: ServiceAdapters, *, request_id: str,
                       session_id: str, message: str, context: str | None,
                       user_id: str, project_id: str | None) -> None:
    pieces: list[str] = []
    async for token in adapters.llm([{"role": "user", "content": message}],
                                    mode="rag" if context else "personal", context=context):
        pieces.append(token)
        await ws.send_json({"type": "token", "request_id": request_id,
                            "session_id": session_id, "data": {"text": token}})
    answer = "".join(pieces)
    with session_scope() as db:
        _record_message(
            db,
            user_id=user_id,
            client_session_id=session_id,
            project_id=project_id,
            role="assistant",
            content=answer,
        )
    for sequence, phrase in enumerate(chunk_phrases(answer)):
        audio = await adapters.synthesize(phrase)
        await ws.send_json({"type": "audio", "request_id": request_id, "session_id": session_id,
                            "data": {**audio, "sequence": sequence}})
    await ws.send_json({"type": "final", "request_id": request_id, "session_id": session_id, "data": {}})


async def _partial_transcript(ws: WebSocket, adapters: ServiceAdapters, pcm: bytes,
                              sample_rate: int, language: str | None,
                              request_id: str, session_id: str) -> None:
    transcript = await adapters.transcribe(pcm, sample_rate, language)
    if transcript.text:
        await ws.send_json({"type": "partial", "request_id": request_id,
                            "session_id": session_id,
                            "data": {"text": transcript.text, "language": transcript.language}})


@app.websocket("/voice/session")
async def voice_session(ws: WebSocket) -> None:
    await ws.accept()
    user = await _ws_user(ws)
    if not user:
        await ws.close(code=4401, reason="authentication required")
        return
    await ws.send_json({"type": "authenticated", "data": {"language": user.preferred_language}})
    audio = bytearray()
    active: asyncio.Task | None = None
    partial_task: asyncio.Task | None = None
    next_partial_bytes = settings.audio_sample_rate * 2 * 2
    request_id = session_id = ""
    sample_rate = settings.audio_sample_rate
    language: str | None = user.preferred_language
    project: Project | None = None
    adapters: ServiceAdapters = app.state.adapters
    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect()
            if message.get("bytes") is not None:
                frame = message["bytes"]
                if len(audio) + len(frame) > settings.max_audio_bytes:
                    await ws.send_json({"type": "error", "data": {"detail": "audio limit exceeded"}})
                    audio.clear()
                else:
                    audio.extend(frame)
                    if (request_id and len(audio) >= next_partial_bytes and
                            (not partial_task or partial_task.done())):
                        partial_task = asyncio.create_task(_partial_transcript(
                            ws, adapters, bytes(audio), sample_rate, language, request_id, session_id))
                        next_partial_bytes += settings.audio_sample_rate * 2 * 2
                continue
            if message.get("text") is None:
                continue
            event = json.loads(message["text"])
            kind = event.get("type")
            if kind == "start":
                if active and not active.done():
                    active.cancel()
                    await ws.send_json({"type": "barge_in", "data": {"cancelled": True}})
                audio.clear()
                next_partial_bytes = settings.audio_sample_rate * 2 * 2
                request_id = str(event.get("request_id") or secrets.token_urlsafe(8))
                session_id = str(event.get("session_id") or secrets.token_urlsafe(8))
                sample_rate = int(event.get("sample_rate", settings.audio_sample_rate))
                language = event.get("language") or user.preferred_language
                project_id = event.get("project_id")
                project = None
                if project_id:
                    with session_scope() as db:
                        project = project_for_user(db, str(project_id), user.id)
                        if project:
                            db.expunge(project)
                    if not project:
                        await ws.send_json({"type": "error", "data": {"detail": "project not found"}})
                        continue
                await ws.send_json({"type": "ready", "request_id": request_id, "session_id": session_id,
                                    "data": {"language": language}})
            elif kind == "barge_in":
                if active and not active.done():
                    active.cancel()
                if partial_task and not partial_task.done():
                    partial_task.cancel()
                audio.clear()
                await ws.send_json({"type": "barge_in", "data": {"cancelled": True}})
            elif kind == "stop":
                if not request_id or not audio:
                    await ws.send_json({"type": "error", "data": {"detail": "no audio received"}})
                    continue
                if partial_task and not partial_task.done():
                    partial_task.cancel()
                transcript = await adapters.transcribe(bytes(audio), sample_rate, language)
                audio.clear()
                with session_scope() as db:
                    _record_message(
                        db,
                        user_id=user.id,
                        client_session_id=session_id,
                        project_id=project.id if project else None,
                        role="user",
                        content=transcript.text,
                    )
                await ws.send_json({"type": "transcript", "request_id": request_id, "session_id": session_id,
                                    "data": {"text": transcript.text, "language": transcript.language, "final": True}})
                chunks: list[dict[str, Any]] = []
                if project:
                    chunks = await adapters.retrieve(transcript.text, project.id, project.tenant_id, user.id)
                    for chunk in chunks:
                        await ws.send_json({"type": "citation", "request_id": request_id,
                                            "session_id": session_id, "data": _citation(chunk).model_dump()})
                active = asyncio.create_task(_voice_reply(ws, adapters, request_id=request_id,
                                                          session_id=session_id, message=transcript.text,
                                                          context=_context(chunks), user_id=user.id,
                                                          project_id=project.id if project else None))
    except WebSocketDisconnect:
        pass
    finally:
        if active and not active.done():
            active.cancel()
        if partial_task and not partial_task.done():
            partial_task.cancel()
    Message,
