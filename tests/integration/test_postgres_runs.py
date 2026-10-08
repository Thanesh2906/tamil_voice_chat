"""Request-bound run continuation against migrated, isolated real PostgreSQL.

Accounts and filesystem grants use normal lifespan bootstrap/login/project APIs.
Only model responses are scripted. File writes use the production executor inside
pytest temporary roots. Explicit row changes simulate revocation or a lost worker,
never create identities or substitute SQLite for PostgreSQL transaction behavior.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from packages.common import get_settings
from packages.db import AgentRun, Message, ProjectMember, RunEvent, ToolInvocation, User
from services import api
from services.llm import CompletionResult, ProviderError, RoutingDecision
from services.llm.providers import ToolCallRequest
from services.tools.adapters import EXECUTORS

pytestmark = pytest.mark.integration

if os.getenv("RUN_INTEGRATION") != "1":
    pytest.skip("set RUN_INTEGRATION=1 with disposable PostgreSQL", allow_module_level=True)

ADMIN_EMAIL = "runs-admin@example.com"
PASSWORD = "postgres-run-integration-password"
FINAL_ANSWER = "The approved note is saved."
CONTENTS = "வணக்கம்: approved PostgreSQL run\n"


class ScriptedModel:
    """Deterministic model I/O, with no replacement of handlers or persistence."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.histories = []
        self.offered = []

    async def retrieve(self, *args):
        return []

    async def _answer(self, messages, **kwargs):
        self.histories.append(copy.deepcopy(messages))
        self.offered.append(copy.deepcopy(kwargs.get("tools", [])))
        callback = kwargs.get("on_decision")
        if callback:
            callback(RoutingDecision(provider="ollama", model="postgres-model", reason="integration test"))
        assert self.steps, "unexpected extra model call (possible duplicate continuation)"
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step()
        return step

    async def llm_with_tools(self, messages, **kwargs):
        return await self._answer(messages, **kwargs)

    async def llm(self, messages, **kwargs):
        result = await self._answer(messages, **kwargs)
        assert not result.tool_calls
        yield result.text


def answer(text=FINAL_ANSWER):
    return CompletionResult(text=text, tool_calls=[])


def write_request(target: Path, *, content=CONTENTS, call_id="write-note"):
    return CompletionResult(text="", tool_calls=[ToolCallRequest(
        id=call_id, name="write_file", arguments={"path": str(target), "content": content},
    )])


@dataclass
class Identity:
    headers: dict[str, str]
    user_id: str
    project_id: str


@pytest.fixture
def run_config(postgres_db, tmp_path, monkeypatch):
    # postgres_db intentionally copies api.settings; policy/executors use the
    # cached configuration, so configure both to the same disposable root.
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    monkeypatch.setattr(get_settings(), "rag_allowed_roots", [str(tmp_path)])
    monkeypatch.setattr(api.settings, "bootstrap_admin_email", ADMIN_EMAIL)
    monkeypatch.setattr(api.settings, "bootstrap_admin_password", PASSWORD)
    return tmp_path


def bootstrap_identity(client, root):
    logged_in = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    me = client.get("/me", headers=headers)
    assert me.status_code == 200, me.text
    assert "admin" in me.json()["scopes"]
    created = client.post("/projects", headers=headers, json={
        "name": "Temporary run workspace", "root_path": str(root),
    })
    assert created.status_code == 201, created.text
    return Identity(headers=headers, user_id=me.json()["id"], project_id=created.json()["id"])


def renew_reduced_scope_token(client, identity):
    logged_in = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    identity.headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    me = client.get("/me", headers=identity.headers)
    assert me.status_code == 200, me.text
    assert me.json()["id"] == identity.user_id
    assert set(me.json()["scopes"]) == {"chat", "rag"}


@pytest.fixture
def run_client(run_config):
    with TestClient(api.app) as client:
        yield client, bootstrap_identity(client, run_config)


def start(client, identity, model, *, session_id=None):
    api.app.state.adapters = model
    body = {
        "message": "Save the note after I approve the exact write.",
        "project_id": identity.project_id, "agent_id": "coder",
        "provider": "ollama", "model": "postgres-model",
    }
    if session_id is not None:
        body["session_id"] = session_id
    response = client.post("/runs", headers=identity.headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def fetch(client, identity, run_id):
    response = client.get(f"/runs/{run_id}", headers=identity.headers)
    assert response.status_code == 200, response.text
    return response.json()


def pending_id(run):
    assert run["status"] == "awaiting_approval"
    pending = [event for event in run["events"] if event["type"] == "tool.pending_approval"]
    assert len(pending) == 1
    return pending[0]["data"]["invocation_id"]


def assert_timeline(postgres_db, run):
    sequence = [event["sequence"] for event in run["events"]]
    assert sequence == list(range(1, len(sequence) + 1))
    with postgres_db.session() as session:
        rows = session.scalars(select(RunEvent).where(RunEvent.run_id == run["id"])
                               .order_by(RunEvent.sequence)).all()
        assert [row.sequence for row in rows] == sequence
        assert [row.type for row in rows] == [event["type"] for event in run["events"]]
        assert len({row.id for row in rows}) == len(rows)


def counted_writes(monkeypatch, *, entered=None, release=None):
    real_write = EXECUTORS["write_file"]
    calls = []

    async def write(args, **kwargs):
        calls.append(args.model_dump())
        if entered is not None:
            entered.set()
            assert await asyncio.to_thread(release.wait, 10), "test did not release file executor"
        return await real_write(args, **kwargs)

    monkeypatch.setitem(EXECUTORS, "write_file", write)
    return calls


def test_migrated_status_width_and_approved_write_automatically_continue(
    postgres_db, run_client, tmp_path, monkeypatch,
):
    columns = {column["name"]: column for column in inspect(postgres_db.engine)
               .get_columns("agent_runs", schema=postgres_db.schema)}
    assert columns["status"]["type"].length == 32
    assert {"checkpoint_json", "claim_token", "lease_expires_at", "revision"} <= columns.keys()
    client, identity = run_client
    target = tmp_path / "approved.txt"
    writes = counted_writes(monkeypatch)
    model = ScriptedModel(write_request(target), answer())
    run = start(client, identity, model, session_id="postgres-continuation")
    invocation_id = pending_id(run)
    assert len(run["status"]) == 17  # The old VARCHAR(16) must fail on PostgreSQL.
    assert run["can_resume"] is False
    assert run["can_cancel"] is True
    assert not target.exists()
    assert writes == []
    with postgres_db.session() as session:
        stored = session.get(AgentRun, run["id"])
        assert stored.status == "awaiting_approval"
        assert json.loads(stored.checkpoint_json)["version"] == 1
        invocation = session.get(ToolInvocation, invocation_id)
        assert invocation.status == "pending"
        assert json.loads(invocation.args_json)["path"] == str(target.resolve())
        assert json.loads(invocation.args_json)["content"] == CONTENTS
        assert (invocation.user_id, invocation.project_id) == (identity.user_id, identity.project_id)
    approved = client.post(f"/tools/{invocation_id}/approve", headers=identity.headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert approved.json()["run_status"] == "completed"
    assert approved.json()["run_id"] == run["id"]
    assert target.read_text(encoding="utf-8") == CONTENTS
    assert len(writes) == 1
    assert writes[0]["path"] == str(target.resolve())
    assert writes[0]["content"] == CONTENTS
    completed = fetch(client, identity, run["id"])
    assert completed["status"] == "completed"
    assert completed["can_resume"] is completed["can_cancel"] is False
    assert [event["data"]["answer"] for event in completed["events"]
            if event["type"] == "run.completed"] == [FINAL_ANSWER]
    assert len(model.histories) == 2
    tool_results = [message for message in model.histories[-1] if message["role"] == "tool"]
    assert len(tool_results) == 1
    assert tool_results[0]["tool_call_id"] == "write-note"
    assert json.loads(tool_results[0]["content"])["path"] == str(target.resolve())
    with postgres_db.session() as session:
        messages = session.scalars(select(Message).where(
            Message.conversation_id == completed["conversation_id"],
        )).all()
        assert Counter(message.role for message in messages) == {"user": 1, "assistant": 1}
        assert next(message.content for message in messages if message.role == "assistant") == FINAL_ANSWER
    assert_timeline(postgres_db, completed)
    for route in (f"/tools/{invocation_id}/approve", f"/tools/{invocation_id}/deny",
                  f"/runs/{run['id']}/resume"):
        repeated = client.post(route, headers=identity.headers)
        assert repeated.status_code == 409, repeated.text
    assert fetch(client, identity, run["id"])["events"] == completed["events"]
    assert len(writes) == 1


@pytest.mark.parametrize("decision", ["deny", "cancel"])
def test_denied_or_cancelled_pending_write_never_executes(
    decision, postgres_db, run_client, tmp_path, monkeypatch,
):
    client, identity = run_client
    target = tmp_path / f"{decision}.txt"
    writes = counted_writes(monkeypatch)
    model = ScriptedModel(write_request(target), answer("The write was denied."))
    run = start(client, identity, model)
    invocation_id = pending_id(run)
    route = f"/tools/{invocation_id}/deny" if decision == "deny" else f"/runs/{run['id']}/cancel"
    response = client.post(route, headers=identity.headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == ("denied" if decision == "deny" else "cancelled")
    resolved = fetch(client, identity, run["id"])
    assert resolved["status"] == ("completed" if decision == "deny" else "cancelled")
    assert not target.exists()
    assert writes == []
    with postgres_db.session() as session:
        assert session.get(ToolInvocation, invocation_id).status == "denied"
    assert client.post(f"/tools/{invocation_id}/approve", headers=identity.headers).status_code == 409
    assert client.post(f"/runs/{run['id']}/resume", headers=identity.headers).status_code == 409
    if decision == "cancel":
        assert len(model.histories) == 1
        assert resolved["cancellation_requested"] is True
        again = client.post(route, headers=identity.headers)
        assert again.status_code == 200, again.text
        assert again.json()["events"] == resolved["events"]
        assert Counter(event["type"] for event in resolved["events"])["run.cancelled"] == 1
    else:
        assert len(model.histories) == 2
        tool_result = next(item for item in model.histories[-1] if item["role"] == "tool")
        assert tool_result["tool_call_id"] == "write-note"
        assert "denied" in tool_result["content"].lower()
    assert_timeline(postgres_db, resolved)


@pytest.mark.parametrize("cancel_during_write", [False, True])
def test_concurrent_controls_claim_one_real_write_and_preserve_late_result(
    cancel_during_write, postgres_db, run_client, tmp_path, monkeypatch,
):
    client, identity = run_client
    target = tmp_path / "one-execution.txt"
    entered, release = threading.Event(), threading.Event()
    writes = counted_writes(monkeypatch, entered=entered, release=release)
    model = ScriptedModel(write_request(target), answer())
    run = start(client, identity, model)
    invocation_id = pending_id(run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        approved = pool.submit(client.post, f"/tools/{invocation_id}/approve", headers=identity.headers)
        try:
            assert entered.wait(10), "approval never reached the production file executor"
            assert not target.exists()
            active = fetch(client, identity, run["id"])
            assert active["can_resume"] is False
            assert active["executing_tool_ids"] == [invocation_id]
            for route in (f"/tools/{invocation_id}/approve", f"/tools/{invocation_id}/deny",
                          f"/runs/{run['id']}/resume"):
                other = client.post(route, headers=identity.headers)
                assert other.status_code == 409, other.text
            if cancel_during_write:
                cancelled = client.post(f"/runs/{run['id']}/cancel", headers=identity.headers)
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()["status"] == "cancelled"
                event = next(item for item in cancelled.json()["events"] if item["type"] == "run.cancelled")
                assert invocation_id in event["data"]["executing_or_uncertain_tool_ids"]
        finally:
            release.set()
        response = approved.result(timeout=15)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert target.read_text(encoding="utf-8") == CONTENTS
    assert len(writes) == 1
    final = fetch(client, identity, run["id"])
    assert final["status"] == ("cancelled" if cancel_during_write else "completed")
    kinds = Counter(event["type"] for event in final["events"])
    assert kinds["tool.approved"] == kinds["tool.completed"] == 1
    assert kinds["run.completed"] == (0 if cancel_during_write else 1)
    assert len(model.histories) == (1 if cancel_during_write else 2)
    with postgres_db.session() as session:
        invocation = session.get(ToolInvocation, invocation_id)
        assert invocation.status == "completed"
        assert json.loads(invocation.result_json)["path"] == str(target.resolve())
    assert_timeline(postgres_db, final)


def test_concurrent_cancel_is_idempotent_and_never_approves(postgres_db, run_client, tmp_path, monkeypatch):
    client, identity = run_client
    target = tmp_path / "cancel-race.txt"
    writes = counted_writes(monkeypatch)
    model = ScriptedModel(write_request(target))
    run = start(client, identity, model)
    invocation_id = pending_id(run)
    barrier = threading.Barrier(3)

    def cancel():
        barrier.wait(timeout=10)
        return client.post(f"/runs/{run['id']}/cancel", headers=identity.headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(cancel) for _ in range(2)]
        barrier.wait(timeout=10)
        responses = [future.result(timeout=15) for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    final = fetch(client, identity, run["id"])
    kinds = Counter(event["type"] for event in final["events"])
    assert kinds["run.cancelled"] == kinds["tool.denied"] == 1
    assert final["status"] == "cancelled"
    assert not target.exists()
    assert writes == []
    assert len(model.histories) == 1
    assert client.post(f"/tools/{invocation_id}/approve", headers=identity.headers).status_code == 409
    assert_timeline(postgres_db, final)


def test_concurrent_resume_has_one_claim_model_answer_and_terminal_event(postgres_db, run_client):
    client, identity = run_client
    initial = ScriptedModel(ProviderError("temporary model outage"))
    run = start(client, identity, initial)
    assert run["status"] == "paused"
    assert run["can_resume"] is True
    entered, release = threading.Event(), threading.Event()

    async def blocked_answer():
        entered.set()
        assert await asyncio.to_thread(release.wait, 10), "test did not release model"
        return answer("Resumed exactly once.")

    model = ScriptedModel(blocked_answer)
    api.app.state.adapters = model
    with ThreadPoolExecutor(max_workers=2) as pool:
        resumed = pool.submit(client.post, f"/runs/{run['id']}/resume", headers=identity.headers)
        try:
            assert entered.wait(10), "resume never reached the model"
            competing = client.post(f"/runs/{run['id']}/resume", headers=identity.headers)
            assert competing.status_code == 409, competing.text
            assert fetch(client, identity, run["id"])["can_resume"] is False
        finally:
            release.set()
        response = resumed.result(timeout=15)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    final = fetch(client, identity, run["id"])
    kinds = Counter(event["type"] for event in final["events"])
    assert kinds["run.resumed"] == kinds["run.completed"] == 1
    assert len(model.histories) == 1
    assert model.histories[0] == initial.histories[0]
    assert client.post(f"/runs/{run['id']}/resume", headers=identity.headers).status_code == 409
    assert fetch(client, identity, run["id"])["events"] == final["events"]
    assert_timeline(postgres_db, final)


@pytest.mark.parametrize("revoke", ["tools", "membership"])
def test_approval_revalidates_revoked_permissions_without_a_write(
    revoke, postgres_db, run_client, tmp_path, monkeypatch,
):
    client, identity = run_client
    target = tmp_path / "revoked.txt"
    writes = counted_writes(monkeypatch)
    model = ScriptedModel(write_request(target))
    run = start(client, identity, model)
    invocation_id = pending_id(run)
    with postgres_db.session() as session:
        if revoke == "tools":
            # Removing admin too is essential: it otherwise authorizes tools.
            session.get(User, identity.user_id).scopes_csv = "chat,rag"
        else:
            session.delete(session.get(ProjectMember, (identity.project_id, identity.user_id)))
        session.commit()
    if revoke == "tools":
        # Existing access tokens still claim admin. Normal authentication rejects
        # that stale grant before the narrower per-action policy can run.
        stale = client.post(f"/tools/{invocation_id}/approve", headers=identity.headers)
        assert stale.status_code == 401, stale.text
        stale_resume = client.post(f"/runs/{run['id']}/resume", headers=identity.headers)
        assert stale_resume.status_code == 401, stale_resume.text
        renew_reduced_scope_token(client, identity)
    approved = client.post(f"/tools/{invocation_id}/approve", headers=identity.headers)
    assert approved.status_code == 403, approved.text
    resumed = client.post(f"/runs/{run['id']}/resume", headers=identity.headers)
    assert resumed.status_code == 403, resumed.text
    assert not target.exists()
    assert writes == []
    assert len(model.histories) == 1
    with postgres_db.session() as session:
        assert session.get(ToolInvocation, invocation_id).status == "pending"
    # Revocation must not prevent an owner from stopping their remaining work.
    cancelled = client.post(f"/runs/{run['id']}/cancel", headers=identity.headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert_timeline(postgres_db, cancelled.json())


def test_normal_registration_cannot_access_another_owners_run_controls(run_client, tmp_path):
    client, identity = run_client
    target = tmp_path / "private.txt"
    model = ScriptedModel(write_request(target))
    run = start(client, identity, model)
    invocation_id = pending_id(run)
    registered = client.post("/auth/register", json={
        "email": "other-run-owner@example.com", "password": PASSWORD,
        "display_name": "Other owner", "tenant_name": "Other workspace",
    })
    assert registered.status_code == 201, registered.text
    other_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    for route in (f"/runs/{run['id']}", f"/runs/{run['id']}/events"):
        assert client.get(route, headers=other_headers).status_code == 404
    for route in (f"/runs/{run['id']}/resume", f"/runs/{run['id']}/cancel"):
        response = client.post(route, headers=other_headers)
        assert response.status_code == 404, response.text
    # The new account has neither tool scope nor project access; it must not
    # gain either from presenting a known invocation ID.
    for route in (f"/tools/{invocation_id}/approve", f"/tools/{invocation_id}/deny"):
        response = client.post(route, headers=other_headers)
        assert response.status_code == 403, response.text
    assert fetch(client, identity, run["id"])["events"] == run["events"]
    assert not target.exists()
    assert len(model.histories) == 1


def test_restart_resume_uses_durable_model_checkpoint_without_replaying_committed_write(
    postgres_db, run_config, tmp_path, monkeypatch,
):
    target = tmp_path / "restart.txt"
    writes = counted_writes(monkeypatch)
    first_model = ScriptedModel(write_request(target), ProviderError("provider disconnected after tool commit"))
    with TestClient(api.app) as client:
        identity = bootstrap_identity(client, run_config)
        run = start(client, identity, first_model, session_id="restart-resume")
        invocation_id = pending_id(run)
        approved = client.post(f"/tools/{invocation_id}/approve", headers=identity.headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "completed"
        assert approved.json()["run_status"] == "paused"
        paused = fetch(client, identity, run["id"])
        assert paused["status"] == "paused"
        assert paused["can_resume"] is True
        assert target.read_text(encoding="utf-8") == CONTENTS
        assert len(writes) == 1
    # Simulate losing a worker while it was retrying model I/O. The actual
    # durable checkpoint/result were produced by the API before this fault.
    with postgres_db.session() as session:
        stored = session.get(AgentRun, run["id"])
        checkpoint = json.loads(stored.checkpoint_json)
        assert checkpoint["pending_calls"] == []
        assert any(message["role"] == "tool" for message in checkpoint["messages"])
        stored.status = "running"
        stored.claim_token = "lost-model-worker"
        stored.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert session.get(ToolInvocation, invocation_id).status == "completed"
        session.commit()
    # A new lifespan and model instance cannot rely on an in-memory transcript.
    second_model = ScriptedModel(answer("Recovered the saved result after restart."))
    with TestClient(api.app) as client:
        api.app.state.adapters = second_model
        stale = fetch(client, identity, run["id"])
        assert stale["can_resume"] is True
        response = client.post(f"/runs/{run['id']}/resume", headers=identity.headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "completed"
        final = fetch(client, identity, run["id"])
        assert len(second_model.histories) == 1
        assert second_model.histories[0] == first_model.histories[-1]
        assert len(writes) == 1
        assert target.read_text(encoding="utf-8") == CONTENTS
        kinds = Counter(event["type"] for event in final["events"])
        assert kinds["tool.approved"] == kinds["tool.completed"] == kinds["run.completed"] == 1
        assert client.post(f"/tools/{invocation_id}/approve", headers=identity.headers).status_code == 409
        assert_timeline(postgres_db, final)


def test_restart_never_replays_an_uncertain_executing_side_effect(
    postgres_db, run_config, tmp_path, monkeypatch,
):
    target = tmp_path / "uncertain.txt"
    target.write_text("Existing file must stay unchanged.", encoding="utf-8")
    writes = counted_writes(monkeypatch)
    with TestClient(api.app) as client:
        identity = bootstrap_identity(client, run_config)
        run = start(client, identity, ScriptedModel(write_request(target)))
        invocation_id = pending_id(run)
    # A crash after the execution claim but before a persisted result has an
    # unknown external outcome. This is fault injection on an API-created row.
    with postgres_db.session() as session:
        stored = session.get(AgentRun, run["id"])
        stored.status = "running"
        stored.claim_token = "lost-effect-worker"
        stored.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        invocation = session.get(ToolInvocation, invocation_id)
        invocation.status = "executing"
        invocation.decided_by = identity.user_id
        invocation.decided_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        session.commit()
    model = ScriptedModel()
    with TestClient(api.app) as client:
        api.app.state.adapters = model
        before = fetch(client, identity, run["id"])
        assert before["can_resume"] is False
        assert before["uncertain_tool_ids"] == [invocation_id]
        for _ in range(2):
            response = client.post(f"/runs/{run['id']}/resume", headers=identity.headers)
            assert response.status_code == 409, response.text
            assert "uncertain" in response.text.lower()
        assert client.post(f"/tools/{invocation_id}/approve", headers=identity.headers).status_code == 409
        final = fetch(client, identity, run["id"])
        assert final["status"] == "paused"
        assert final["can_resume"] is False
        assert final["uncertain_tool_ids"] == [invocation_id]
        assert final["events"] == before["events"]
        assert_timeline(postgres_db, final)
    with postgres_db.session() as session:
        assert session.get(ToolInvocation, invocation_id).status == "uncertain"
    assert target.read_text(encoding="utf-8") == "Existing file must stay unchanged."
    assert writes == []
    assert model.histories == []


def test_one_approval_never_authorizes_a_second_checkpointed_write(
    postgres_db, run_client, tmp_path, monkeypatch,
):
    client, identity = run_client
    first_target, second_target = tmp_path / "first.txt", tmp_path / "second.txt"
    writes = counted_writes(monkeypatch)
    model = ScriptedModel(CompletionResult(text="", tool_calls=[
        ToolCallRequest(id="first-call", name="write_file", arguments={
            "path": str(first_target), "content": "First approved content.",
        }),
        ToolCallRequest(id="second-call", name="write_file", arguments={
            "path": str(second_target), "content": "Second content requires its own approval.",
        }),
    ]), answer("Only the first note was saved."))
    run = start(client, identity, model)
    first_id = pending_id(run)
    first = client.post(f"/tools/{first_id}/approve", headers=identity.headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert first.json()["run_status"] == "awaiting_approval"
    assert first_target.read_text(encoding="utf-8") == "First approved content."
    assert not second_target.exists()
    assert len(writes) == 1
    assert len(model.histories) == 1
    waiting = fetch(client, identity, run["id"])
    pending_events = [event for event in waiting["events"] if event["type"] == "tool.pending_approval"]
    assert len(pending_events) == 2
    second_id = pending_events[-1]["data"]["invocation_id"]
    assert first_id != second_id
    assert pending_events[-1]["data"]["args"]["path"] == str(second_target.resolve())
    assert client.post(f"/tools/{first_id}/approve", headers=identity.headers).status_code == 409
    assert client.post(f"/runs/{run['id']}/resume", headers=identity.headers).status_code == 409
    denied = client.post(f"/tools/{second_id}/deny", headers=identity.headers)
    assert denied.status_code == 200, denied.text
    assert denied.json()["run_status"] == "completed"
    assert not second_target.exists()
    assert len(writes) == 1
    assert len(model.histories) == 2
    tool_results = [message for message in model.histories[-1] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_results] == ["first-call", "second-call"]
    assert json.loads(tool_results[0]["content"])["path"] == str(first_target.resolve())
    assert "denied" in tool_results[1]["content"].lower()
    assert_timeline(postgres_db, fetch(client, identity, run["id"]))


@pytest.mark.parametrize("revoke", ["tools", "membership"])
def test_paused_checkpoint_controls_and_resume_recheck_current_permissions(
    revoke, postgres_db, run_client,
):
    client, identity = run_client
    model = ScriptedModel(ProviderError("temporary provider outage"), answer())
    run = start(client, identity, model)
    assert run["status"] == "paused"
    assert run["can_resume"] is True
    with postgres_db.session() as session:
        if revoke == "tools":
            session.get(User, identity.user_id).scopes_csv = "chat,rag"
        else:
            session.delete(session.get(ProjectMember, (identity.project_id, identity.user_id)))
        session.commit()
    if revoke == "tools":
        stale = client.post(f"/runs/{run['id']}/resume", headers=identity.headers)
        assert stale.status_code == 401, stale.text
        renew_reduced_scope_token(client, identity)
    controls = fetch(client, identity, run["id"])
    assert controls["can_resume"] is False
    assert ("tools" if revoke == "tools" else "membership") in controls["resume_blocked_reason"]
    response = client.post(f"/runs/{run['id']}/resume", headers=identity.headers)
    assert response.status_code == 403, response.text
    assert len(model.histories) == 1
    final = fetch(client, identity, run["id"])
    assert final["status"] == "paused"
    assert final["events"] == run["events"]
    assert_timeline(postgres_db, final)
    cancelled = client.post(f"/runs/{run['id']}/cancel", headers=identity.headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert len(model.histories) == 1
    assert_timeline(postgres_db, cancelled.json())
