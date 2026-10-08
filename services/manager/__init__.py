"""Request-bound execution with durable, fenced continuation checkpoints.

There is no background queue. Approvals resume synchronously from the recorded
result; explicit resume recovers model work, never an uncertain tool effect.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common import get_settings
from packages.db import (
    AgentRun,
    AuditEvent,
    Conversation,
    Message,
    Project,
    ToolInvocation,
    new_id,
    project_for_user,
)
from services.llm import ProviderError, RoutingDecision, get_router
from services.manager import state
from services.manager.agents import agent_messages, execution_agent
from services.manager.policy import authorize_tool, available_tool_names, current_scopes, has_scope
from services.tools.adapters import ToolExecutionError
from services.tools.gateway import ToolPolicyError
from services.tools.gateway import execute as execute_tool
from services.tools.gateway import validate as validate_tool
from services.tools.registry import TOOLS, tool_schema

if TYPE_CHECKING:
    from services.api.adapters import ServiceAdapters
    from services.api.router import AgentDecision

TOOL_ENABLED_MODES = {"personal", "coding"}
MAX_TOOL_ITERATIONS = 6
MAX_CALLS_PER_ITERATION = 16
EventCallback = Optional[Callable[[dict[str, Any]], None]]


def _context(chunks: list[dict[str, Any]]) -> str | None:
    if not chunks:
        return None
    return "\n\n".join(
        f"[{item.get('file_path', 'source')}:{item.get('line_start', '?')}-"
        f"{item.get('line_end', '?')}]\n{item.get('snippet', '')}" for item in chunks
    )


def resolve_history_route(mode: str, provider: str | None, model: str | None) -> tuple[RoutingDecision, str]:
    router = get_router()
    route = router.resolve(mode=mode, requested_provider=provider, requested_model=model)
    privacy = next(spec.privacy for spec in router.available() if spec.name == route.provider)
    return route, privacy


def revalidate_checkpoint(session: Session, run: AgentRun, checkpoint: dict) -> Project | None:
    """Recheck current grants and the pinned privacy domain before any I/O."""
    scopes = current_scopes(session, run.user_id)
    if not has_scope(scopes, "chat"):
        raise ToolPolicyError("missing scope: chat")
    project = None
    if run.project_id:
        project = project_for_user(session, run.project_id, run.user_id)
        if project is None:
            raise ToolPolicyError("project membership is no longer authorized")
        session.refresh(project)
        if project.root_path != checkpoint.get("project_root"):
            raise ToolPolicyError("project root changed; start a new run")
    if checkpoint["mode"] in {"coding", "rag"} and project and not has_scope(scopes, "rag"):
        raise ToolPolicyError("missing scope: rag")
    if checkpoint["mode"] == "monitoring" and not has_scope(scopes, "monitoring:read"):
        raise ToolPolicyError("missing scope: monitoring:read")
    if checkpoint["tool_enabled"] and not has_scope(scopes, "tools"):
        raise ToolPolicyError("missing scope: tools")
    router = get_router()
    # Never override the administrator's current cloud-disable setting.
    route = router.resolve(mode=checkpoint["mode"], requested_provider=checkpoint["provider"],
                           requested_model=checkpoint["model"],
                           allow_cloud=False if checkpoint["privacy"] == "local" else None)
    privacy = next(spec.privacy for spec in router.available() if spec.name == route.provider)
    if privacy != checkpoint["privacy"]:
        raise ToolPolicyError("provider privacy boundary changed; start a new run")
    return project


def run_controls(session: Session, run: AgentRun) -> dict:
    result = state.controls(session, run)
    if result["can_resume"]:
        try:
            revalidate_checkpoint(session, run, state.checkpoint_for(run))
        except (state.RunConflict, ToolPolicyError, ProviderError) as exc:
            result.update(can_resume=False, resume_blocked_reason=str(exc))
    return result


class _EventEmitter:
    def __init__(self, session: Session, run_id: str, token: str, on_event: EventCallback = None) -> None:
        self.session, self.run_id, self.token, self.on_event = session, run_id, token, on_event

    def locked(self) -> AgentRun:
        run = state.lock_run(self.session, self.run_id)
        state.require_claim(run, self.token)
        return run

    def publish(self, event: dict) -> None:
        if self.on_event:
            self.on_event(event)

    def emit(self, event_type: str, data: dict) -> None:
        run = self.locked()
        event = state.append_event(self.session, run, event_type, data)
        self.session.commit()
        self.publish(event)

    def save(self, checkpoint: dict) -> None:
        encoded = state.encode_checkpoint(checkpoint)
        run = self.locked()
        run.checkpoint_json = encoded
        self.session.commit()

    def push_ephemeral(self, event_type: str, data: dict) -> None:
        self.locked()
        self.session.commit()
        self.publish({"sequence": None, "type": event_type, "data": data})


def _stop(session: Session, emitter: _EventEmitter, *, status: str, reason: str) -> None:
    """Only the current fence can change run state after an awaited operation."""
    session.rollback()
    try:
        run = emitter.locked()
    except state.RunStopped:
        session.rollback()
        return
    run.status, run.claim_token, run.lease_expires_at = status, None, None
    if status == "failed":
        run.completed_at = state.now()
    event = state.append_event(session, run, f"run.{status}", {"error" if status == "failed" else "reason": reason})
    session.commit()
    emitter.publish(event)


def _record_tool_result(session: Session, run_id: str, invocation_id: str, *,
                        status: str, output: Any = None, error: str | None = None) -> dict:
    # Even after cancellation, retain the real result of an already-started
    # effect. This does not restore its fence or permit any further execution.
    run = state.lock_run(session, run_id)
    invocation = session.get(ToolInvocation, invocation_id)
    assert invocation is not None
    session.refresh(invocation)
    if invocation.status not in {"executing", "uncertain"}:
        session.rollback()
        raise state.RunConflict("tool already has a terminal result")
    invocation.status, invocation.error = status, error
    invocation.result_json = json.dumps(output, ensure_ascii=False, default=str) if output is not None else None
    invocation.decided_at = state.now()
    session.add(AuditEvent(id=new_id("aud"), user_id=run.user_id, action=f"tool.{status}",
                           target=invocation.id, detail=invocation.tool_name))
    event = state.append_event(session, run, f"tool.{status}", {
        "invocation_id": invocation.id, "tool_name": invocation.tool_name,
        "args": json.loads(invocation.args_json), "output": output, "error": error,
    })
    session.commit()
    return event


async def _execute_recorded(session: Session, emitter: _EventEmitter, invocation: ToolInvocation,
                            checkpoint: dict | None, executor) -> None:
    try:
        run = emitter.locked()
        if checkpoint is not None:
            revalidate_checkpoint(session, run, checkpoint)
        call = validate_tool(invocation.tool_name, json.loads(invocation.args_json))
        roots = authorize_tool(session, run.user_id, run.project_id, call)
        if call.args != json.loads(invocation.args_json):
            raise ToolPolicyError("tool target changed; request a new action")
        session.commit()
    except state.RunStopped:
        session.rollback()
        # Claimed, but definitely not dispatched. Cancellation cannot approve it.
        emitter.publish(_record_tool_result(session, emitter.run_id, invocation.id,
                                             status="denied", error="run stopped before tool dispatch"))
        raise
    except (ToolPolicyError, ProviderError) as exc:
        session.rollback()
        emitter.publish(_record_tool_result(session, emitter.run_id, invocation.id, status="failed", error=str(exc)))
        return
    try:
        output = await executor(call, roots=roots, sensitive_globs=get_settings().rag_sensitive_globs)
    except asyncio.CancelledError:
        emitter.publish(_record_tool_result(session, emitter.run_id, invocation.id, status="uncertain",
                                             error="execution interrupted; effect may have occurred; never automatically retried"))
        raise
    except ToolExecutionError as exc:
        # Adapter errors can occur after a partial write or a remote timeout.
        # Only read tools have a safely known absence of consequential effects.
        uncertain = invocation.risk != "read"
        emitter.publish(_record_tool_result(session, emitter.run_id, invocation.id,
                                             status="uncertain" if uncertain else "failed", error=str(exc)))
        if uncertain:
            raise state.RunConflict("tool outcome is uncertain; inspect the effect before starting a new run") from exc
    except Exception:
        emitter.publish(_record_tool_result(session, emitter.run_id, invocation.id, status="uncertain",
                                             error="unexpected execution interruption; inspect the effect before starting a new run"))
        raise state.RunConflict("tool outcome is uncertain")
    else:
        emitter.publish(_record_tool_result(session, emitter.run_id, invocation.id, status="completed", output=output))


def _result_message(call: dict, invocation: ToolInvocation) -> dict:
    if invocation.status == "completed":
        content = invocation.result_json or "null"
    else:
        content = json.dumps({"status": invocation.status, "error": invocation.error or "The user denied this action. Do not retry it without a new request."})
    # Truncate the model-facing copy only. The exact result is durable in the
    # invocation and is never inferred from freshly generated model text.
    return {"role": "tool", "tool_call_id": call["id"], "name": call["name"], "content": content[:20_000]}


async def _pending_calls(session: Session, run: AgentRun, emitter: _EventEmitter, checkpoint: dict) -> bool:
    while checkpoint["pending_calls"]:
        current = emitter.locked()
        revalidate_checkpoint(session, current, checkpoint)
        call = checkpoint["pending_calls"][0]
        if "error" in call:
            checkpoint["messages"].append({"role": "tool", "tool_call_id": call["id"],
                                          "name": call["name"], "content": json.dumps({"error": call["error"]})})
            checkpoint["pending_calls"].pop(0)
            current.checkpoint_json = state.encode_checkpoint(checkpoint)
            session.commit()
            continue
        invocation = session.get(ToolInvocation, call["invocation_id"]) if call.get("invocation_id") else None
        if invocation:
            session.refresh(invocation)
            if invocation.status in {"completed", "failed", "denied"}:
                checkpoint["messages"].append(_result_message(call, invocation))
                checkpoint["pending_calls"].pop(0)
                current.checkpoint_json = state.encode_checkpoint(checkpoint)
                session.commit()
                continue
            if invocation.status != "pending":
                raise state.RunConflict("tool outcome is uncertain; automatic replay is blocked")
        else:
            validated = validate_tool(call["name"], call["arguments"])
            authorize_tool(session, run.user_id, run.project_id, validated)
            if validated.args != call["arguments"]:
                raise ToolPolicyError("tool target changed; start a new run")
            invocation = ToolInvocation(
                id=new_id("tin"), user_id=run.user_id, project_id=run.project_id, run_id=run.id,
                tool_name=call["name"], risk=validated.spec.risk,
                status="executing" if validated.spec.risk == "read" else "pending",
                args_json=json.dumps(validated.args, ensure_ascii=False),
            )
            session.add(invocation)
            call["invocation_id"] = invocation.id
        current.checkpoint_json = state.encode_checkpoint(checkpoint)
        if invocation.status == "pending":
            current.status, current.claim_token, current.lease_expires_at = "awaiting_approval", None, None
            event = state.append_event(session, current, "tool.pending_approval", {
                "invocation_id": invocation.id, "tool_name": invocation.tool_name,
                "args": json.loads(invocation.args_json),
            })
            session.commit()
            emitter.publish(event)
            return False
        session.commit()  # durable executing marker BEFORE dispatch
        await _execute_recorded(session, emitter, invocation, checkpoint, execute_tool)
    return True


async def _drive(session: Session, run: AgentRun, emitter: _EventEmitter, checkpoint: dict,
                 adapters: ServiceAdapters) -> AgentRun:
    try:
        while True:
            if not await _pending_calls(session, run, emitter, checkpoint):
                return run
            current = emitter.locked()
            project = revalidate_checkpoint(session, current, checkpoint)
            if checkpoint["remaining_iterations"] <= 0:
                session.commit()
                emitter.emit("run.tool_limit_reached", {"limit": MAX_TOOL_ITERATIONS})
                answer = "I could not finish this within the allowed number of tool calls."
                break
            # Charge the attempt before I/O. Recovery cannot reset its budget.
            checkpoint["remaining_iterations"] -= 1
            current.checkpoint_json = state.encode_checkpoint(checkpoint)
            offered = set(checkpoint["offered_names"]) & available_tool_names(session, run.user_id, project)
            session.commit()
            routing: list[RoutingDecision] = []
            kwargs = dict(mode=checkpoint["mode"], context=checkpoint["context"],
                          provider=checkpoint["provider"], model=checkpoint["model"],
                          allow_cloud=False if checkpoint["privacy"] == "local" else None,
                          on_decision=routing.append)
            if checkpoint["tool_enabled"]:
                result = await adapters.llm_with_tools(checkpoint["messages"],
                    tools=[tool_schema(TOOLS[name]) for name in sorted(offered)], **kwargs)
                if len(result.tool_calls) > MAX_CALLS_PER_ITERATION:
                    raise state.RunConflict("model returned too many tool calls")
                # Check the fence before using a response from a slow model.
                current = emitter.locked()
                session.commit()
                if routing and not checkpoint.get("reported_model"):
                    chosen = routing[-1]
                    emitter.emit("model.selected", {"provider": chosen.provider, "model": chosen.model, "reason": chosen.reason})
                    checkpoint["reported_model"] = True
                if not result.tool_calls:
                    answer = result.text
                    break
                batch = []
                seen = set()
                for tc in result.tool_calls:
                    if not tc.id or len(tc.id) > 256 or tc.id in seen:
                        raise state.RunConflict("model returned invalid or duplicate tool call IDs")
                    seen.add(tc.id)
                    item = {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    try:
                        if tc.name not in offered:
                            raise ToolPolicyError("tool was not authorized for this run")
                        validated = validate_tool(tc.name, tc.arguments)
                        authorize_tool(session, run.user_id, run.project_id, validated)
                        item["arguments"] = validated.args
                    except ToolPolicyError as exc:
                        item["error"] = str(exc)
                        emitter.emit("tool.rejected", {"name": tc.name, "error": str(exc)})
                    batch.append(item)
                checkpoint["messages"].append({"role": "assistant", "tool_calls": [
                    {key: item[key] for key in ("id", "name", "arguments")} for item in batch
                ]})
                checkpoint["pending_calls"] = batch
                emitter.save(checkpoint)
            else:
                parts = []
                async for token in adapters.llm(checkpoint["messages"], **kwargs):
                    parts.append(token)
                    emitter.push_ephemeral("token", {"text": token})
                answer = "".join(parts)
                if routing:
                    chosen = routing[-1]
                    emitter.emit("model.selected", {"provider": chosen.provider, "model": chosen.model, "reason": chosen.reason})
                break
        current = emitter.locked()
        if current.conversation_id:
            session.add_all([
                Message(id=new_id("msg"), conversation_id=current.conversation_id, role="user", content=current.input_text),
                Message(id=new_id("msg"), conversation_id=current.conversation_id, role="assistant", content=answer),
            ])
        current.status, current.completed_at = "completed", state.now()
        current.claim_token = current.lease_expires_at = None
        event = state.append_event(session, current, "run.completed", {
            "answer": answer, "citation_count": checkpoint["citation_count"], "agent_id": current.execution_agent_id,
        })
        session.commit()
        emitter.publish(event)
    except state.RunStopped:
        session.rollback()
    except asyncio.CancelledError:
        _stop(session, emitter, status="paused", reason="request interrupted; safe model work may be resumed explicitly")
        raise
    except (ProviderError, ToolPolicyError, state.RunConflict) as exc:
        _stop(session, emitter, status="paused", reason=str(exc))
    except Exception:
        _stop(session, emitter, status="paused", reason="request failed; safe model work may be resumed explicitly")
        raise
    session.refresh(run)
    return run


async def resume_run(session: Session, *, run_id: str, user_id: str, adapters: ServiceAdapters,
                     on_event: EventCallback = None) -> AgentRun:
    run = state.lock_run(session, run_id)
    if run.user_id != user_id:
        session.rollback()
        raise state.RunConflict("run not found")
    try:
        checkpoint = state.checkpoint_for(run)
        revalidate_checkpoint(session, run, checkpoint)
    except Exception:
        session.rollback()
        raise
    session.rollback()
    run, token, checkpoint = state.claim_resume(session, run_id, user_id)
    return await _drive(session, run, _EventEmitter(session, run.id, token, on_event), checkpoint, adapters)


async def decide_run_tool(session: Session, *, invocation_id: str, user_id: str, approve: bool,
                          adapters: ServiceAdapters, executor=execute_tool) -> ToolInvocation:
    invocation = session.get(ToolInvocation, invocation_id)
    if not invocation or invocation.user_id != user_id or not invocation.run_id:
        raise state.RunConflict("invocation not found")
    run = state.lock_run(session, invocation.run_id)
    if run.user_id != user_id or run.project_id != invocation.project_id:
        session.rollback()
        raise state.RunConflict("invocation does not belong to this run owner and project")
    session.refresh(invocation)
    if invocation.status != "pending" or run.status in state.TERMINAL or state.live_claim(run):
        session.rollback()
        raise state.RunConflict("invocation was already decided or run is not awaiting approval")
    checkpoint = state.checkpoint_for(run) if run.checkpoint_json else None
    if checkpoint is not None and (not checkpoint["pending_calls"] or checkpoint["pending_calls"][0].get("invocation_id") != invocation.id):
        session.rollback()
        raise state.RunConflict("invocation does not match the durable pending action")
    if approve:
        if checkpoint is not None:
            revalidate_checkpoint(session, run, checkpoint)
        call = validate_tool(invocation.tool_name, json.loads(invocation.args_json))
        authorize_tool(session, user_id, invocation.project_id, call)
        if call.args != json.loads(invocation.args_json):
            session.rollback()
            raise ToolPolicyError("tool target changed; request a new action")
    token = new_id("claim")
    run.claim_token, run.lease_expires_at = token, state.now() + timedelta(seconds=state.LEASE_SECONDS)
    run.status = "running"
    invocation.status = "executing" if approve else "denied"
    invocation.decided_by, invocation.decided_at = user_id, state.now()
    if not approve:
        invocation.error = "The user denied this action. Do not retry it without a new request."
    session.add(AuditEvent(id=new_id("aud"), user_id=user_id,
                           action="tool.approved" if approve else "tool.denied",
                           target=invocation.id, detail=invocation.tool_name))
    state.append_event(session, run, "tool.approved" if approve else "tool.denied", {
        "invocation_id": invocation.id, "tool_name": invocation.tool_name,
    })
    session.commit()
    emitter = _EventEmitter(session, run.id, token)
    if approve:
        try:
            await _execute_recorded(session, emitter, invocation, checkpoint, executor)
        except asyncio.CancelledError:
            _stop(session, emitter, status="paused", reason="approved action interrupted; inspect its outcome before continuing")
            raise
        except (state.RunStopped, state.RunConflict) as exc:
            _stop(session, emitter, status="paused", reason=str(exc))
            session.refresh(invocation)
            return invocation
    if checkpoint is None:
        _stop(session, emitter, status="paused", reason="legacy action resolved; no resumable checkpoint exists")
        session.refresh(invocation)
        return invocation
    # The terminal action result has committed. Any failure below cannot erase
    # it or put it back into pending. A crash here is recovered by explicit resume.
    try:
        emitter.emit("run.resumed", {"execution": "request_bound", "after_invocation_id": invocation.id})
        await _drive(session, run, emitter, checkpoint, adapters)
    except state.RunStopped:
        session.rollback()
    except Exception:
        _stop(session, emitter, status="paused", reason="continuation interrupted after the tool decision was saved")
    session.refresh(invocation)
    return invocation


async def execute_run(
    session: Session, *, user_id: str, project: Project | None, message: str,
    decision: AgentDecision, adapters: ServiceAdapters, provider: str | None = None,
    model: str | None = None, agent_id: str = "manager", session_id: str | None = None,
    on_event: EventCallback = None,
) -> AgentRun:
    agent = execution_agent(agent_id, decision.mode)
    token = new_id("claim")
    run = AgentRun(id=new_id("run"), user_id=user_id, project_id=project.id if project else None,
                   input_text=message, status="running", agent_id=agent_id, execution_agent_id=agent.id,
                   claim_token=token, lease_expires_at=state.now() + timedelta(seconds=state.LEASE_SECONDS))
    session.add(run)
    session.commit()
    emitter = _EventEmitter(session, run.id, token, on_event)
    try:
        emitter.emit("run.started", {"run_id": run.id, "input": message, "mode_requested": decision.mode,
                                     "agent_id": agent_id, "execution_agent_id": agent.id})
        emitter.emit("run.classified", {"mode": decision.mode, "tool_name": decision.tool_name, "tool_args": decision.tool_args})
        if agent.id != agent_id:
            emitter.emit("agent.delegated", {"from_agent_id": agent_id, "to_agent_id": agent.id,
                                            "execution": "serial", "reason": f"specialist for {decision.mode}"})
        requested_provider, requested_model = provider, model
        route, privacy = resolve_history_route(decision.mode, provider, model)
        provider, model = route.provider, route.model
        history = []
        if session_id:
            scope = json.dumps(["route-v2", user_id, session_id, agent_id,
                                project.id if project else None, decision.mode,
                                provider, model, privacy, requested_provider, requested_model])
            conversation_id = "con_" + hashlib.sha256(scope.encode()).hexdigest()[:40]
            current = emitter.locked()
            if session.get(Conversation, conversation_id) is None:
                session.add(Conversation(id=conversation_id, user_id=user_id,
                                         project_id=project.id if project else None))
                session.flush()
            previous = session.scalars(select(Message).where(Message.conversation_id == conversation_id)
                                       .order_by(Message.created_at.desc(), Message.id.desc()).limit(20)).all()
            history = [{"role": item.role, "content": item.content} for item in reversed(previous)]
            current.conversation_id = conversation_id
            session.commit()
        history.append({"role": "user", "content": message})
        chunks: list[dict[str, Any]] = []
        context = None
        scopes = current_scopes(session, user_id)
        session.commit()
        if project and decision.mode in {"rag", "coding"}:
            if not has_scope(scopes, "rag"):
                raise ToolPolicyError("missing scope: rag")
            chunks = await adapters.retrieve(message, project.id, project.tenant_id, user_id)
            if chunks:
                context = _context(chunks)
                emitter.emit("retrieval.completed", {"chunk_count": len(chunks)})
        if decision.tool_name:
            if not has_scope(scopes, "monitoring:read"):
                raise ToolPolicyError("missing scope: monitoring:read")
            emitter.locked()
            session.commit()
            output = await adapters.monitoring_tool(decision.tool_name, decision.tool_args)
            emitter.emit("tool.completed", {"tool_name": decision.tool_name, "args": decision.tool_args, "output": output})
            context = ((context + "\n\n") if context else "") + (
                "Authorized read-only monitoring result:\n" + json.dumps(output, ensure_ascii=False))
        offered = available_tool_names(session, user_id, project) if agent.id in {"manager", "coder"} else set()
        checkpoint = {
            "version": 1, "messages": agent_messages(agent.id, history), "pending_calls": [],
            "mode": decision.mode, "provider": provider, "model": model, "privacy": privacy,
            "context": context, "remaining_iterations": MAX_TOOL_ITERATIONS, "citation_count": len(chunks),
            "tool_enabled": decision.mode in TOOL_ENABLED_MODES and bool(offered),
            "offered_names": sorted(offered), "project_root": project.root_path if project else None,
        }
        emitter.save(checkpoint)
        return await _drive(session, run, emitter, checkpoint, adapters)
    except state.RunStopped:
        session.rollback()
    except asyncio.CancelledError:
        _stop(session, emitter, status="paused" if run.checkpoint_json else "failed", reason="request execution interrupted")
        raise
    except (ProviderError, ToolPolicyError, state.RunConflict) as exc:
        _stop(session, emitter, status="failed", reason=str(exc))
    except Exception as exc:
        _stop(session, emitter, status="paused" if run.checkpoint_json else "failed", reason="internal error")
        raise RuntimeError(f"run {run.id} failed: {exc}") from exc
    session.refresh(run)
    return run
