"""Serial manager/specialist execution with persisted identity and event history.

Runs are executed inside the request, not by a durable background queue. Manager
routing is deterministic and specialists run serially. A pending write stops the
turn; approval executes that recorded action but does not auto-resume the model.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common import get_settings
from packages.db import AgentRun, Conversation, Message, Project, RunEvent, ToolInvocation, new_id
from services.llm import ProviderError, RoutingDecision, get_router
from services.manager.agents import agent_messages, execution_agent
from services.manager.policy import authorize_tool, available_tool_names, current_scopes, has_scope
from services.tools.gateway import ToolPolicyError
from services.tools.gateway import execute as execute_tool
from services.tools.gateway import validate as validate_tool
from services.tools.registry import TOOLS, tool_schema

if TYPE_CHECKING:
    from services.api.adapters import ServiceAdapters
    from services.api.router import AgentDecision

TOOL_ENABLED_MODES = {"personal", "coding"}
MAX_TOOL_ITERATIONS = 6
EventCallback = Optional[Callable[[dict[str, Any]], None]]


def _context(chunks: list[dict[str, Any]]) -> str | None:
    if not chunks:
        return None
    return "\n\n".join(
        f"[{item.get('file_path', 'source')}:{item.get('line_start', '?')}-"
        f"{item.get('line_end', '?')}]\n{item.get('snippet', '')}" for item in chunks
    )


def resolve_history_route(mode: str, provider: str | None, model: str | None) -> tuple[RoutingDecision, str]:
    """Choose the real privacy boundary before loading any persisted messages.

    Requested provider=None is not a privacy domain: automatic routing can pick
    local for monitoring and cloud for the next personal turn. Resolve first and
    pin the result, including a fail-closed local-only execution override.
    """
    router = get_router()
    route = router.resolve(mode=mode, requested_provider=provider, requested_model=model)
    privacy = next(spec.privacy for spec in router.available() if spec.name == route.provider)
    return route, privacy


class _EventEmitter:
    def __init__(self, session: Session, run_id: str, on_event: EventCallback = None) -> None:
        self._session = session
        self._run_id = run_id
        self._next = 1
        self._on_event = on_event

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        sequence = self._next
        self._session.add(RunEvent(
            id=new_id("evt"), run_id=self._run_id, sequence=sequence, type=event_type,
            data_json=json.dumps(data, ensure_ascii=False, default=str),
        ))
        self._next += 1
        # Office polling and replay see actual progress during slow network work.
        # No SQL transaction stays open across a model/tool await.
        self._session.commit()
        if self._on_event:
            self._on_event({"sequence": sequence, "type": event_type, "data": data})

    def push_ephemeral(self, event_type: str, data: dict[str, Any]) -> None:
        if self._on_event:
            self._on_event({"sequence": None, "type": event_type, "data": data})


class _RunPaused(Exception):
    pass


async def _run_conversational_turn(
    session: Session, run: AgentRun, emit, *, user_id: str, project: Project | None,
    message: str, context: str | None, mode: str, provider: str | None, model: str | None,
    adapters: ServiceAdapters, offered_names: set[str], messages: list[dict[str, Any]],
    allow_cloud: bool | None,
) -> str:
    offered = [tool_schema(TOOLS[name]) for name in sorted(offered_names)]
    settings = get_settings()
    history: list[dict[str, Any]] = agent_messages(run.execution_agent_id, messages)
    routing: list[RoutingDecision] = []
    reported_model = False
    for _ in range(MAX_TOOL_ITERATIONS):
        result = await adapters.llm_with_tools(
            history, mode=mode, context=context, tools=offered,
            provider=provider, model=model, allow_cloud=allow_cloud, on_decision=routing.append,
        )
        if routing and not reported_model:
            chosen = routing[0]
            emit("model.selected", {"provider": chosen.provider, "model": chosen.model, "reason": chosen.reason})
            reported_model = True
        if not result.tool_calls:
            return result.text
        history.append({"role": "assistant", "tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in result.tool_calls
        ]})
        for call in result.tool_calls:
            try:
                if call.name not in offered_names:
                    raise ToolPolicyError("tool was not authorized for this run")
                validated = validate_tool(call.name, call.arguments)
                roots = authorize_tool(session, user_id, project.id if project else None, validated)
            except ToolPolicyError as exc:
                emit("tool.rejected", {"name": call.name, "error": str(exc)})
                history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                "content": json.dumps({"error": str(exc)})})
                continue
            invocation = ToolInvocation(
                id=new_id("tin"), user_id=user_id, project_id=project.id if project else None,
                run_id=run.id, tool_name=call.name, risk=validated.spec.risk,
                status="pending" if validated.spec.risk != "read" else "executing",
                args_json=json.dumps(validated.args, ensure_ascii=False),
            )
            session.add(invocation)
            session.commit()
            if validated.spec.risk != "read":
                emit("tool.pending_approval", {"invocation_id": invocation.id, "tool_name": call.name, "args": validated.args})
                raise _RunPaused()
            try:
                output = await execute_tool(validated, roots=roots, sensitive_globs=settings.rag_sensitive_globs)
                invocation.status = "completed"
                invocation.result_json = json.dumps(output, ensure_ascii=False, default=str)
                invocation.decided_at = datetime.now(timezone.utc)
                emit("tool.completed", {"invocation_id": invocation.id, "tool_name": call.name, "args": validated.args, "output": output})
                history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                "content": json.dumps(output, ensure_ascii=False, default=str)[:20_000]})
            except Exception as exc:
                invocation.status = "failed"
                invocation.error = str(exc)
                emit("tool.failed", {"invocation_id": invocation.id, "tool_name": call.name, "error": str(exc)})
                history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                "content": json.dumps({"error": str(exc)})})
    emit("run.tool_limit_reached", {"limit": MAX_TOOL_ITERATIONS})
    return "I could not finish this within the allowed number of tool calls."


async def execute_run(
    session: Session, *, user_id: str, project: Project | None, message: str,
    decision: AgentDecision, adapters: ServiceAdapters, provider: str | None = None,
    model: str | None = None, agent_id: str = "manager", session_id: str | None = None,
    on_event: EventCallback = None,
) -> AgentRun:
    agent = execution_agent(agent_id, decision.mode)
    conversation_id = None
    history: list[dict[str, Any]] = []
    run = AgentRun(
        id=new_id("run"), user_id=user_id, project_id=project.id if project else None,
        input_text=message, status="running", agent_id=agent_id, execution_agent_id=agent.id,
        conversation_id=conversation_id,
    )
    session.add(run)
    session.flush()
    emitter = _EventEmitter(session, run.id, on_event=on_event)
    emit = emitter.emit
    emit("run.started", {"run_id": run.id, "input": message, "mode_requested": decision.mode,
                         "agent_id": agent_id, "execution_agent_id": agent.id})
    emit("run.classified", {"mode": decision.mode, "tool_name": decision.tool_name, "tool_args": decision.tool_args})
    if agent.id != agent_id:
        emit("agent.delegated", {"from_agent_id": agent_id, "to_agent_id": agent.id,
                                 "execution": "serial", "reason": f"specialist for {decision.mode}"})
    try:
        requested_provider, requested_model = provider, model
        route, privacy = resolve_history_route(decision.mode, provider, model)
        provider, model = route.provider, route.model
        allow_cloud = False if privacy == "local" else None
        if session_id:
            # Versioned effective-route isolation also prevents previously stored
            # auto-mode history from entering this newly hardened context domain.
            scope = json.dumps(["route-v2", user_id, session_id, agent_id,
                                project.id if project else None, decision.mode,
                                provider, model, privacy, requested_provider, requested_model])
            conversation_id = "con_" + hashlib.sha256(scope.encode()).hexdigest()[:40]
            if session.get(Conversation, conversation_id) is None:
                session.add(Conversation(id=conversation_id, user_id=user_id,
                                         project_id=project.id if project else None))
                session.flush()
            previous = session.scalars(select(Message).where(Message.conversation_id == conversation_id)
                                       .order_by(Message.created_at.desc(), Message.id.desc()).limit(20)).all()
            history = [{"role": item.role, "content": item.content} for item in reversed(previous)]
            run.conversation_id = conversation_id
        history.append({"role": "user", "content": message})
        chunks: list[dict[str, Any]] = []
        context: str | None = None
        scopes = current_scopes(session, user_id)
        if project and decision.mode in {"rag", "coding"}:
            if not has_scope(scopes, "rag"):
                raise ToolPolicyError("missing scope: rag")
            chunks = await adapters.retrieve(message, project.id, project.tenant_id, user_id)
            if chunks:
                context = _context(chunks)
                emit("retrieval.completed", {"chunk_count": len(chunks)})
        if decision.tool_name:
            if not has_scope(scopes, "monitoring:read"):
                raise ToolPolicyError("missing scope: monitoring:read")
            output = await adapters.monitoring_tool(decision.tool_name, decision.tool_args)
            emit("tool.completed", {"tool_name": decision.tool_name, "args": decision.tool_args, "output": output})
            context = ((context + "\n\n") if context else "") + (
                "Authorized read-only monitoring result:\n" + json.dumps(output, ensure_ascii=False)
            )
        offered = available_tool_names(session, user_id, project) if agent.id in {"manager", "coder"} else set()
        if decision.mode in TOOL_ENABLED_MODES and offered:
            answer = await _run_conversational_turn(
                session, run, emit, user_id=user_id, project=project, message=message,
                context=context, mode=decision.mode, provider=provider, model=model,
                adapters=adapters, offered_names=offered, messages=history, allow_cloud=allow_cloud,
            )
        else:
            routing: list[RoutingDecision] = []
            parts: list[str] = []
            async for token in adapters.llm(
                agent_messages(agent.id, history),
                mode=decision.mode, context=context, provider=provider, model=model,
                allow_cloud=allow_cloud, on_decision=routing.append,
            ):
                parts.append(token)
                emitter.push_ephemeral("token", {"text": token})
            answer = "".join(parts)
            if routing:
                chosen = routing[0]
                emit("model.selected", {"provider": chosen.provider, "model": chosen.model, "reason": chosen.reason})
        if conversation_id:
            session.add_all([
                Message(id=new_id("msg"), conversation_id=conversation_id, role="user", content=message),
                Message(id=new_id("msg"), conversation_id=conversation_id, role="assistant", content=answer),
            ])
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        emit("run.completed", {"answer": answer, "citation_count": len(chunks), "agent_id": agent.id})
    except _RunPaused:
        run.status = "awaiting_approval"
        session.commit()
    except (ProviderError, ToolPolicyError) as exc:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        emit("run.failed", {"error": str(exc)})
    except asyncio.CancelledError:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        emit("run.failed", {"error": "request execution was cancelled"})
        raise
    except Exception as exc:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        emit("run.failed", {"error": "internal error"})
        raise RuntimeError(f"run {run.id} failed: {exc}") from exc
    return run
