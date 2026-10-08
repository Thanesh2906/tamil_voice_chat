"""Durable continuation uses recorded outcomes and never replays effects."""
from __future__ import annotations

import copy
import json
from datetime import timedelta

from fastapi.testclient import TestClient
from test_manager_runs import FakeAdapters, _register

import services.api as api
from packages.db import AgentRun, ToolInvocation, session_scope
from services.llm import CompletionResult, ProviderError
from services.llm.providers import ToolCallRequest
from services.manager import state


class Scripted(FakeAdapters):
    def __init__(self, script):
        self.script = list(script)
        self.histories = []

    async def llm_with_tools(self, messages, **kwargs):
        self.histories.append(copy.deepcopy(messages))
        item = self.script.pop(0) if self.script else CompletionResult(text="Finished", tool_calls=[])
        if isinstance(item, BaseException):
            raise item
        return item


def write(call_id, path, text="exact content"):
    return ToolCallRequest(id=call_id, name="write_file", arguments={"path": str(path), "content": text})


def start(client, root, fake):
    headers, project = _register(client, tools=True, root=root)
    api.app.state.adapters = fake
    response = client.post("/runs", headers=headers, json={"message": "save a note", "project_id": project, "mode": "personal"})
    assert response.status_code == 201, response.text
    return headers, response.json()


def pending(run):
    return [e["data"]["invocation_id"] for e in run["events"] if e["type"] == "tool.pending_approval"][-1]


def get(client, headers, run):
    response = client.get(f"/runs/{run['id']}", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_multiple_calls_are_correlated_across_separate_approvals(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    first, second = tmp_path / "one.txt", tmp_path / "two.txt"
    fake = Scripted([CompletionResult(text="", tool_calls=[write("first", first), write("second", second)])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        assert run["can_cancel"] and not run["can_resume"]
        approved = client.post(f"/tools/{pending(run)}/approve", headers=headers)
        assert approved.status_code == 200
        assert approved.json()["run_status"] == "awaiting_approval"
        assert first.exists() and not second.exists()
        next_run = get(client, headers, run)
        assert pending(next_run) != pending(run)
        assert len(fake.histories) == 1
        second_result = client.post(f"/tools/{pending(next_run)}/approve", headers=headers)
        assert second_result.json()["run_status"] == "completed"
        assert second.exists()
        tool_messages = [m for m in fake.histories[-1] if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_messages] == ["first", "second"]
        assert json.loads(tool_messages[0]["content"])["path"] == str(first)
        assert json.loads(tool_messages[1]["content"])["path"] == str(second)
        final = get(client, headers, run)
        assert [e["sequence"] for e in final["events"]] == list(range(1, len(final["events"]) + 1))


def test_denial_continues_with_a_recorded_denied_result(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "denied.txt"
    fake = Scripted([CompletionResult(text="", tool_calls=[write("denied-call", target)])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        result = client.post(f"/tools/{pending(run)}/deny", headers=headers)
        assert result.status_code == 200
        assert result.json()["status"] == "denied"
        assert result.json()["run_status"] == "completed"
        message = fake.histories[-1][-1]
        assert message["tool_call_id"] == "denied-call"
        assert json.loads(message["content"])["status"] == "denied"
        assert not target.exists()


def test_approval_result_survives_model_failure_and_explicit_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "once.txt"
    fake = Scripted([CompletionResult(text="", tool_calls=[write("once", target)]), ProviderError("temporarily unavailable")])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        approved = client.post(f"/tools/{pending(run)}/approve", headers=headers)
        assert approved.json()["status"] == "completed"
        assert approved.json()["run_status"] == "paused"
        assert get(client, headers, run)["can_resume"]
        target.write_text("changed after the actual execution")
        assert client.post(f"/tools/{pending(run)}/approve", headers=headers).status_code == 409
        resumed = client.post(f"/runs/{run['id']}/resume", headers=headers)
        assert resumed.status_code == 200
        assert resumed.json()["status"] == "completed"
        assert target.read_text() == "changed after the actual execution"
        assert json.loads(fake.histories[-1][-1]["content"])["bytes_written"] == len("exact content")


def test_pending_cancel_denies_without_running_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "cancelled.txt"
    fake = Scripted([CompletionResult(text="", tool_calls=[write("cancel", target)])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        cancelled = client.post(f"/runs/{run['id']}/cancel", headers=headers)
        body = cancelled.json()
        assert body["status"] == "cancelled" and body["cancellation_requested"]
        assert not body["can_resume"] and not body["can_cancel"]
        assert client.post(f"/tools/{pending(run)}/approve", headers=headers).status_code == 409
        assert client.post(f"/runs/{run['id']}/resume", headers=headers).status_code == 409
        assert client.post(f"/runs/{run['id']}/cancel", headers=headers).json()["events"] == body["events"]
        assert not target.exists() and len(fake.histories) == 1


def test_restart_with_executing_effect_is_uncertain_never_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "uncertain.txt"
    fake = Scripted([CompletionResult(text="", tool_calls=[write("uncertain", target)])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        with session_scope() as session:
            stored = session.get(AgentRun, run["id"])
            stored.status, stored.claim_token = "running", "dead-process"
            stored.lease_expires_at = state.now() - timedelta(seconds=1)
            session.get(ToolInvocation, pending(run)).status = "executing"
            session.commit()
        before = get(client, headers, run)
        assert before["uncertain_tool_ids"] == [pending(run)]
        assert not before["can_resume"]
        assert client.post(f"/runs/{run['id']}/resume", headers=headers).status_code == 409
        with session_scope() as session:
            assert session.get(ToolInvocation, pending(run)).status == "uncertain"
        assert not target.exists() and len(fake.histories) == 1


def test_legacy_approval_executes_exact_action_but_cannot_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "legacy.txt"
    fake = Scripted([CompletionResult(text="", tool_calls=[write("legacy", target)])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        with session_scope() as session:
            session.get(AgentRun, run["id"]).checkpoint_json = None
            session.commit()
        result = client.post(f"/tools/{pending(run)}/approve", headers=headers)
        assert result.status_code == 200
        assert result.json()["status"] == "completed"
        assert result.json()["run_status"] == "paused"
        assert target.read_text() == "exact content"
        assert len(fake.histories) == 1
        assert not get(client, headers, run)["can_resume"]
        assert client.post(f"/runs/{run['id']}/resume", headers=headers).status_code == 409


def test_resume_budget_is_not_reset_by_approval_or_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "limited.txt"
    fake = Scripted([CompletionResult(text="", tool_calls=[write("limit", target)]), ProviderError("retry later")])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        with session_scope() as session:
            stored = session.get(AgentRun, run["id"])
            cp = json.loads(stored.checkpoint_json)
            cp["remaining_iterations"] = 1
            stored.checkpoint_json = json.dumps(cp)
            session.commit()
        assert client.post(f"/tools/{pending(run)}/approve", headers=headers).json()["run_status"] == "paused"
        resumed = client.post(f"/runs/{run['id']}/resume", headers=headers).json()
        assert resumed["status"] == "completed"
        assert any(e["type"] == "run.tool_limit_reached" for e in resumed["events"])
        assert len(fake.histories) == 2


def test_post_dispatch_write_error_is_uncertain_and_cannot_resume(tmp_path, monkeypatch):
    from services.tools.adapters import EXECUTORS, ToolExecutionError
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "partial.txt"
    actual_write = EXECUTORS["write_file"]

    async def interrupted_after_effect(args, **kwargs):
        await actual_write(args, **kwargs)
        raise ToolExecutionError("response lost after effect")

    monkeypatch.setitem(EXECUTORS, "write_file", interrupted_after_effect)
    fake = Scripted([CompletionResult(text="", tool_calls=[write("uncertain", target)])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        result = client.post(f"/tools/{pending(run)}/approve", headers=headers)
        assert result.status_code == 200
        assert result.json()["status"] == "uncertain"
        assert result.json()["run_status"] == "paused"
        assert target.read_text() == "exact content"
        fetched = get(client, headers, run)
        assert fetched["uncertain_tool_ids"] == [pending(run)]
        assert not fetched["can_resume"]
        assert client.post(f"/runs/{run['id']}/resume", headers=headers).status_code == 409
        assert len(fake.histories) == 1


def test_oversized_checkpoint_is_rejected_before_tool_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    monkeypatch.setattr(state, "MAX_CHECKPOINT_BYTES", 5_000)
    fake = Scripted([CompletionResult(text="", tool_calls=[write("huge", tmp_path / "huge.txt", "X" * 10_000)])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        assert run["status"] == "paused"
        assert not (tmp_path / "huge.txt").exists()
        assert not any(e["type"] == "tool.pending_approval" for e in run["events"])
        with session_scope() as session:
            assert len(session.get(AgentRun, run["id"]).checkpoint_json.encode()) < 5_000


def test_malformed_checkpoint_fails_closed_without_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    fake = Scripted([ProviderError("offline")])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        with session_scope() as session:
            session.get(AgentRun, run["id"]).checkpoint_json = '{"version":1}'
            session.commit()
        assert get(client, headers, run)["can_resume"] is False
        assert client.post(f"/runs/{run['id']}/resume", headers=headers).status_code == 409
        assert len(fake.histories) == 1


def test_desktop_status_is_authenticated_and_never_claims_a_connection():
    with TestClient(api.app) as client:
        assert client.get("/desktop/status").status_code == 401
        headers, _ = _register(client)
        response = client.get("/desktop/status", headers=headers)
        assert response.status_code == 200
        assert response.json()["state"] == "disconnected"
        assert response.json()["paired"] is False
        assert all(not capability["available"] for capability in response.json()["capabilities"])
        assert client.post("/desktop/pair", headers=headers, json={}).status_code == 404


def test_approved_canonical_target_cannot_be_retargeted_at_dispatch(tmp_path, monkeypatch):
    from services.tools.gateway import execute as real_execute
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    approved_path = tmp_path / "approved.txt"
    sibling = tmp_path / "other.txt"
    approved_path.write_text("original approved target")
    sibling.write_text("must stay unchanged")
    fake = Scripted([CompletionResult(text="", tool_calls=[write("exact-target", approved_path)])])

    async def raced_execute(call, **kwargs):
        approved_path.unlink()
        approved_path.symlink_to(sibling)
        return await real_execute(call, **kwargs)

    monkeypatch.setattr(api, "execute_tool", raced_execute)
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        response = client.post(f"/tools/{pending(run)}/approve", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "uncertain"
        assert sibling.read_text() == "must stay unchanged"
        assert len(fake.histories) == 1


def test_changed_provider_privacy_blocks_approval_and_resume(tmp_path, monkeypatch):
    from dataclasses import replace

    import services.llm as llm_runtime
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    fake = Scripted([CompletionResult(text="", tool_calls=[write("private", tmp_path / "private.txt")])])
    with TestClient(api.app) as client:
        headers, run = start(client, tmp_path, fake)
        with session_scope() as session:
            checkpoint = json.loads(session.get(AgentRun, run["id"]).checkpoint_json)
        router = llm_runtime.get_router()
        name = checkpoint["provider"]
        monkeypatch.setitem(router._specs, name, replace(router._specs[name], privacy="cloud"))
        for route in (f"/tools/{pending(run)}/approve", f"/runs/{run['id']}/resume"):
            response = client.post(route, headers=headers)
            assert response.status_code == 403, response.text
        assert not (tmp_path / "private.txt").exists()
        assert len(fake.histories) == 1


def test_recovered_model_claim_fences_out_late_original_response(tmp_path, monkeypatch):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    entered, release = threading.Event(), threading.Event()

    class Delayed(FakeAdapters):
        def __init__(self):
            self.calls = 0

        async def llm_with_tools(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                assert await asyncio.to_thread(release.wait, 10)
                return CompletionResult(text="Stale response must be discarded", tool_calls=[])
            return CompletionResult(text="Recovered answer", tool_calls=[])

    fake = Delayed()
    with TestClient(api.app) as client:
        headers, project = _register(client, tools=True, root=tmp_path)
        api.app.state.adapters = fake
        with ThreadPoolExecutor(max_workers=2) as pool:
            original = pool.submit(client.post, "/runs", headers=headers, json={
                "message": "answer me", "project_id": project, "mode": "personal",
            })
            try:
                assert entered.wait(10)
                run = client.get("/runs", headers=headers).json()[0]
                with session_scope() as session:
                    session.get(AgentRun, run["id"]).lease_expires_at = state.now() - timedelta(seconds=1)
                    session.commit()
                recovered = client.post(f"/runs/{run['id']}/resume", headers=headers)
                assert recovered.status_code == 200, recovered.text
                assert recovered.json()["status"] == "completed"
            finally:
                release.set()
            assert original.result(timeout=15).status_code == 201
        final = get(client, headers, run)
        completed = [event for event in final["events"] if event["type"] == "run.completed"]
        assert len(completed) == 1
        assert completed[0]["data"]["answer"] == "Recovered answer"
        assert fake.calls == 2
