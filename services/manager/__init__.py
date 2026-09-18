"""Durable agent runs (docs/master-build-audit.md, Phase 2).

Today /chat and the voice WebSocket compute an answer and return it, but the
only thing persisted is the final message text (packages/db.py Message) --
which model answered, what was retrieved, and which tool ran are all lost the
moment the response is sent. A run fixes that: it is a database row plus an
ordered, replayable event history (RunEvent, strictly increasing `sequence`
per run) covering classify -> retrieve/tool -> model call -> result.

Scope, honestly: execution here is synchronous, exactly like /chat -- a run is
fully computed before `execute_run` returns, so `GET /runs/{id}/events` is a
replay of a finished run, not yet a live feed of one still in progress. Making
an in-flight run resumable across a process restart, or pushing events to a
client while a run executes, needs a durable job queue and is not built yet
(tracked as open work in docs/master-build-audit.md Phase 2/6). What this does
give: a real, restart-durable record of every run and a monotonic-sequence
event contract that a future live-streaming executor can reuse unchanged.

`execute_run` never raises for an ordinary failure (a provider being
unavailable, a tool refusing) -- it records the run as failed and returns it,
the same way a ToolInvocation can end up "failed" without the HTTP call itself
failing. It does re-raise for a genuinely unexpected bug, after still marking
the run failed, so the error is visible instead of a run stuck at "running".

Tool-calling: for "personal"/"coding" modes, the model is offered every tool
in services/tools/registry.py and may call one mid-answer -- this is what lets
the assistant read a file or check `git status` on its own instead of only
through a human calling /tools/invoke directly. A "read" tool call executes
immediately, same as it would through the gateway. A "write"/"exec" call is
never auto-executed here either: it is persisted as the same kind of pending
ToolInvocation the gateway already uses (with `run_id` set so it is traceable
to this run), the run's status becomes "awaiting_approval", and the loop stops
-- approving it via POST /tools/{id}/approve runs it, but does not resume this
run's conversation with the result. Resuming a paused run automatically is not
built yet (same durable-job-queue gap noted above); today you would ask again
in a new turn once you have approved the action.

Every configured provider supports tool-calling here (ProviderAdapter.SUPPORTS_TOOLS
is True for all five: Ollama, Anthropic, OpenAI, OpenRouter, Gemini) -- each
converts the manager's provider-agnostic tool-call/tool-result history into its
own wire format. A provider that somehow lacked tool support would still get a
normal tool-free answer rather than an error, since the router only offers
`tools` to an adapter whose SUPPORTS_TOOLS is True (services/llm/__init__.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from packages.common import get_settings
from packages.db import AgentRun, Project, RunEvent, ToolInvocation, new_id
from services.llm import ProviderError, RoutingDecision
from services.tools.gateway import ToolPolicyError
from services.tools.gateway import execute as execute_tool
from services.tools.gateway import validate as validate_tool
from services.tools.registry import AGENT_OFFERED_TOOLS, TOOLS, tool_schema

if TYPE_CHECKING:
    # services.api imports services.manager (to expose /runs), so importing
    # anything from services.api at module load time here would be circular.
    # Both of these are only ever used as type hints, and `from __future__
    # import annotations` above makes annotations lazy strings, so this import
    # only runs for a type checker, never at runtime.
    from services.api.adapters import ServiceAdapters
    from services.api.router import AgentDecision

TOOL_ENABLED_MODES = {"personal", "coding"}
MAX_TOOL_ITERATIONS = 6


def _context(chunks: list[dict[str, Any]]) -> str | None:
    if not chunks:
        return None
    return "\n\n".join(
        f"[{item.get('file_path', 'source')}:{item.get('line_start', '?')}-"
        f"{item.get('line_end', '?')}]\n{item.get('snippet', '')}"
        for item in chunks
    )


class _EventEmitter:
    """Appends RunEvents in strict sequence order for one run."""

    def __init__(self, session: Session, run_id: str) -> None:
        self._session = session
        self._run_id = run_id
        self._next = 1

    def emit(self, event_type: str, data: dict[str, Any]) -> None:
        self._session.add(
            RunEvent(
                id=new_id("evt"),
                run_id=self._run_id,
                sequence=self._next,
                type=event_type,
                data_json=json.dumps(data, ensure_ascii=False, default=str),
            )
        )
        self._next += 1


class _RunPaused(Exception):
    """Internal control-flow signal: a write/exec tool call needs a human's
    approval before this run can continue. Never crosses execute_run's boundary
    as an exception -- caught there and turned into a normal "awaiting_approval"
    return, the same as any other terminal run status."""


async def _run_conversational_turn(
    session: Session,
    run: AgentRun,
    emit,
    *,
    user_id: str,
    project: Project | None,
    message: str,
    context: str | None,
    mode: str,
    provider: str | None,
    model: str | None,
    adapters: "ServiceAdapters",
) -> str:
    """One tool-enabled conversational turn. Returns the final answer text, or
    raises _RunPaused if a write/exec tool call is awaiting approval.

    Goes through ServiceAdapters.llm_with_tools (services/api/adapters.py),
    the same seam the plain streaming path uses -- not the router directly --
    so this stays mockable in tests exactly like retrieve()/llm() already are.
    """
    offered = [tool_schema(TOOLS[name]) for name in AGENT_OFFERED_TOOLS]
    settings = get_settings()
    history: list[dict[str, Any]] = [{"role": "user", "content": message}]
    routing: list[RoutingDecision] = []

    for _ in range(MAX_TOOL_ITERATIONS):
        result = await adapters.llm_with_tools(
            history, mode=mode, context=context, tools=offered,
            provider=provider, model=model, on_decision=routing.append,
        )
        if len(routing) == 1:
            chosen = routing[0]
            emit("model.selected", {"provider": chosen.provider, "model": chosen.model, "reason": chosen.reason})
        if not result.tool_calls:
            return result.text

        history.append({
            "role": "assistant",
            "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in result.tool_calls],
        })
        for call in result.tool_calls:
            try:
                validated = validate_tool(call.name, call.arguments)
            except ToolPolicyError as exc:
                emit("tool.rejected", {"name": call.name, "error": str(exc)})
                history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                               "content": json.dumps({"error": str(exc)})})
                continue

            if validated.spec.risk != "read":
                invocation = ToolInvocation(
                    id=new_id("tin"), user_id=user_id, project_id=project.id if project else None,
                    run_id=run.id, tool_name=call.name, risk=validated.spec.risk, status="pending",
                    args_json=json.dumps(validated.args, ensure_ascii=False),
                )
                session.add(invocation)
                session.flush()
                emit("tool.pending_approval",
                    {"invocation_id": invocation.id, "tool_name": call.name, "args": validated.args})
                raise _RunPaused()

            try:
                output = await execute_tool(
                    validated, roots=settings.rag_allowed_roots, sensitive_globs=settings.rag_sensitive_globs
                )
                emit("tool.completed", {"tool_name": call.name, "args": validated.args, "output": output})
                history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                               "content": json.dumps(output, ensure_ascii=False, default=str)[:20_000]})
            except Exception as exc:  # tool adapters raise ToolExecutionError; be defensive either way
                emit("tool.failed", {"tool_name": call.name, "error": str(exc)})
                history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                               "content": json.dumps({"error": str(exc)})})

    emit("run.tool_limit_reached", {"limit": MAX_TOOL_ITERATIONS})
    return "I could not finish this within the allowed number of tool calls."


async def execute_run(
    session: Session,
    *,
    user_id: str,
    project: Project | None,
    message: str,
    decision: AgentDecision,
    adapters: ServiceAdapters,
    provider: str | None = None,
    model: str | None = None,
) -> AgentRun:
    run = AgentRun(
        id=new_id("run"),
        user_id=user_id,
        project_id=project.id if project else None,
        input_text=message,
        status="running",
    )
    session.add(run)
    session.flush()
    emit = _EventEmitter(session, run.id).emit
    emit("run.started", {"input": message, "mode_requested": decision.mode})
    emit(
        "run.classified",
        {"mode": decision.mode, "tool_name": decision.tool_name, "tool_args": decision.tool_args},
    )

    try:
        chunks: list[dict[str, Any]] = []
        context: str | None = None
        if project and decision.mode in {"rag", "coding"}:
            chunks = await adapters.retrieve(message, project.id, project.tenant_id, user_id)
            if chunks:
                context = _context(chunks)
                emit("retrieval.completed", {"chunk_count": len(chunks)})

        if decision.tool_name:
            output = await adapters.monitoring_tool(decision.tool_name, decision.tool_args)
            emit(
                "tool.completed",
                {"tool_name": decision.tool_name, "args": decision.tool_args, "output": output},
            )
            context = ((context + "\n\n") if context else "") + (
                "Authorized read-only monitoring result:\n" + json.dumps(output, ensure_ascii=False)
            )

        if decision.mode in TOOL_ENABLED_MODES:
            # Agentic path: the model may read files, run an allowlisted dev
            # command, etc. mid-answer. _run_conversational_turn emits its own
            # "model.selected" event since it drives llm_with_tools itself.
            answer = await _run_conversational_turn(
                session, run, emit, user_id=user_id, project=project, message=message,
                context=context, mode=decision.mode, provider=provider, model=model, adapters=adapters,
            )
        else:
            # rag/monitoring: a plain answer over already-authorized context,
            # no filesystem/command access offered to the model.
            routing: list[RoutingDecision] = []
            parts: list[str] = []
            async for token in adapters.llm(
                [{"role": "user", "content": message}],
                mode=decision.mode,
                context=context,
                provider=provider,
                model=model,
                on_decision=routing.append,
            ):
                parts.append(token)
            answer = "".join(parts)
            if routing:
                chosen = routing[0]
                emit("model.selected",
                    {"provider": chosen.provider, "model": chosen.model, "reason": chosen.reason})

        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        emit("run.completed", {"answer": answer, "citation_count": len(chunks)})
        session.commit()
        return run
    except _RunPaused:
        run.status = "awaiting_approval"
        session.commit()
        return run
    except ProviderError as exc:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        emit("run.failed", {"error": str(exc)})
        session.commit()
        return run
    except Exception as exc:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
        emit("run.failed", {"error": "internal error"})
        session.commit()
        raise RuntimeError(f"run {run.id} failed: {exc}") from exc
