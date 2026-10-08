"""Short, serialized DB transactions for request-bound, fenced run execution.

No transaction spans external I/O. A lease only permits recovery of model work;
a tool found executing after interruption is uncertain and never replayed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from packages.db import AgentRun, AuditEvent, RunEvent, ToolInvocation, new_id

LEASE_SECONDS = 900
MAX_CHECKPOINT_BYTES = 256_000
TERMINAL = {"completed", "failed", "cancelled"}


class RunConflict(Exception):
    pass


class RunStopped(Exception):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def live_claim(run: AgentRun) -> bool:
    expiry = run.lease_expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return bool(run.claim_token and expiry and expiry > now())


def lock_run(session: Session, run_id: str) -> AgentRun:
    """An UPDATE locks on PostgreSQL and SQLite, unlike SQLite FOR UPDATE.

    All code that changes a linked invocation takes this lock first. Refresh
    prevents a request's pre-await ORM copy from overwriting a cancellation.
    """
    found = session.execute(update(AgentRun).where(AgentRun.id == run_id)
                            .values(revision=AgentRun.revision + 1)
                            .returning(AgentRun.id)
                            .execution_options(synchronize_session=False)).scalar_one_or_none()
    if found is None:
        session.rollback()
        raise RunConflict("run not found")
    run = session.get(AgentRun, run_id)
    assert run is not None
    session.refresh(run)
    return run


def require_claim(run: AgentRun, token: str) -> None:
    if run.cancellation_requested_at or run.status == "cancelled" or run.claim_token != token:
        raise RunStopped("run execution was stopped or superseded")
    run.lease_expires_at = now() + timedelta(seconds=LEASE_SECONDS)


def append_event(session: Session, run: AgentRun, event_type: str, data: dict) -> dict:
    sequence = (session.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)) or 0) + 1
    session.add(RunEvent(id=new_id("evt"), run_id=run.id, sequence=sequence, type=event_type,
                         data_json=json.dumps(data, ensure_ascii=False, default=str)))
    session.flush()
    return {"sequence": sequence, "type": event_type, "data": data}


def encode_checkpoint(checkpoint: dict) -> str:
    encoded = json.dumps(checkpoint, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
        raise RunConflict("run checkpoint exceeded the safe size limit")
    return encoded


def checkpoint_for(run: AgentRun) -> dict:
    if not run.checkpoint_json:
        raise RunConflict("this run has no resumable checkpoint")
    if len(run.checkpoint_json.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
        raise RunConflict("run checkpoint exceeds the safe size limit")
    try:
        checkpoint = json.loads(run.checkpoint_json)
        if not isinstance(checkpoint, dict) or checkpoint.get("version") != 1:
            raise ValueError("unsupported version")
        if not isinstance(checkpoint.get("messages"), list) or not isinstance(checkpoint.get("pending_calls"), list):
            raise ValueError("invalid messages or pending calls")
        if len(checkpoint["messages"]) > 256 or len(checkpoint["pending_calls"]) > 16:
            raise ValueError("checkpoint collection limit")
        if not all(isinstance(checkpoint.get(key), str) for key in ("mode", "provider", "model")):
            raise ValueError("missing routing identity")
        if checkpoint.get("privacy") not in {"local", "cloud"}:
            raise ValueError("invalid privacy boundary")
        budget = checkpoint.get("remaining_iterations")
        if type(budget) is not int or not 0 <= budget <= 6:
            raise ValueError("invalid execution budget")
        if type(checkpoint.get("tool_enabled")) is not bool or not isinstance(checkpoint.get("offered_names"), list):
            raise ValueError("invalid tool authorization")
        if checkpoint.get("context") is not None and not isinstance(checkpoint["context"], str):
            raise ValueError("invalid context")
        if type(checkpoint.get("citation_count")) is not int:
            raise ValueError("invalid citation count")
        for message in checkpoint["messages"]:
            if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
                raise ValueError("invalid message")
        for call in checkpoint["pending_calls"]:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict):
                raise ValueError("invalid pending tool call")
    except (ValueError, TypeError) as exc:
        raise RunConflict("invalid or unsupported run checkpoint") from exc
    return checkpoint


def controls(session: Session, run: AgentRun) -> dict[str, Any]:
    effects = session.execute(select(ToolInvocation.id, ToolInvocation.status).where(
        ToolInvocation.run_id == run.id, ToolInvocation.status.in_(["executing", "uncertain"]),
    )).all()
    executing = [row.id for row in effects if row.status == "executing"]
    uncertain = [row.id for row in effects if row.status == "uncertain"]
    if executing and not live_claim(run):
        uncertain = sorted(set(uncertain + executing))
    reason = None
    if run.status in TERMINAL:
        reason = f"run is {run.status}"
    elif uncertain:
        reason = "a tool outcome is uncertain; inspect its effect before starting a new run"
    elif executing or live_claim(run):
        reason = "execution is already in progress"
    elif not run.checkpoint_json:
        reason = "this run has no resumable checkpoint"
    elif run.status == "awaiting_approval":
        reason = "a tool decision is required"
    elif run.cancellation_requested_at:
        reason = "cancellation was requested"
    return {
        "can_resume": reason is None,
        "can_cancel": run.status not in TERMINAL,
        "resume_blocked_reason": reason,
        "cancellation_requested": run.cancellation_requested_at is not None,
        "executing_tool_ids": executing,
        "uncertain_tool_ids": uncertain,
    }


def cancel(session: Session, run_id: str, user_id: str) -> AgentRun:
    run = lock_run(session, run_id)
    if run.user_id != user_id:
        session.rollback()
        raise RunConflict("run not found")
    if run.status in TERMINAL:
        session.commit()
        return run
    run.cancellation_requested_at = now()
    run.status = "cancelled"
    run.completed_at = now()
    run.claim_token = None
    run.lease_expires_at = None
    pending = session.scalars(select(ToolInvocation).where(
        ToolInvocation.run_id == run.id, ToolInvocation.status == "pending",
    )).all()
    for inv in pending:
        inv.status = "denied"
        inv.error = "run cancelled before execution"
        inv.decided_by = user_id
        inv.decided_at = now()
        append_event(session, run, "tool.denied", {"invocation_id": inv.id, "reason": inv.error})
    active = list(session.scalars(select(ToolInvocation.id).where(
        ToolInvocation.run_id == run.id, ToolInvocation.status.in_(["executing", "uncertain"]),
    )).all())
    session.add(AuditEvent(id=new_id("aud"), user_id=user_id, action="run.cancelled", target=run.id))
    append_event(session, run, "run.cancelled", {
        "executing_or_uncertain_tool_ids": active,
        "message": "Further work stopped. Already-started effects may still finish; cancellation does not undo them.",
    })
    session.commit()
    return run


def claim_resume(session: Session, run_id: str, user_id: str) -> tuple[AgentRun, str, dict]:
    run = lock_run(session, run_id)
    if run.user_id != user_id:
        session.rollback()
        raise RunConflict("run not found")
    state = controls(session, run)
    if state["uncertain_tool_ids"] and run.status not in TERMINAL and not live_claim(run):
        for invocation_id in state["uncertain_tool_ids"]:
            inv = session.get(ToolInvocation, invocation_id)
            if inv and inv.status == "executing":
                inv.status = "uncertain"
                inv.error = "execution was interrupted or its lease expired; effect may have occurred; never automatically retried"
        run.status = "paused"
        run.claim_token = None
        run.lease_expires_at = None
        session.commit()
        raise RunConflict(state["resume_blocked_reason"])
    if not state["can_resume"]:
        session.rollback()
        raise RunConflict(state["resume_blocked_reason"])
    checkpoint = checkpoint_for(run)
    token = new_id("claim")
    run.claim_token = token
    run.lease_expires_at = now() + timedelta(seconds=LEASE_SECONDS)
    run.status = "running"
    append_event(session, run, "run.resumed", {"execution": "request_bound"})
    session.commit()
    return run, token, checkpoint
