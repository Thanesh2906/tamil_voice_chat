"""Authorization and exactly-once claims at the real API/manager boundary."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import services.api as api
import services.llm as llm_runtime
from packages.auth import decode_token
from packages.common.config import Settings
from packages.db import Project, ProjectMember, User, session_scope
from services.llm import CompletionResult
from services.llm.providers import ToolCallRequest
from services.llm.router import ModelRouter


class Adapters:
    def __init__(self, calls=None, revoke=None):
        self.calls = list(calls or [])
        self.offered = []
        self.messages = []
        self.modes = []
        self.retrievals = []
        self.revoke = revoke

    async def aclose(self):
        pass

    async def retrieve(self, *args):
        self.retrievals.append(args)
        return []

    async def llm(self, messages, *, mode, **kwargs):
        self.messages.append(messages)
        self.modes.append(mode)
        yield "Grounded answer"

    async def llm_with_tools(self, messages, *, tools, mode, **kwargs):
        self.messages.append(messages)
        self.offered.append([tool["name"] for tool in tools])
        self.modes.append(mode)
        if self.revoke:
            self.revoke()
            self.revoke = None
        return self.calls.pop(0) if self.calls else CompletionResult(text="Done", tool_calls=[])


def identity(client, *, root=None, tools=False):
    response = client.post("/auth/register", json={
        "email": f"secure-{uuid.uuid4().hex}@example.com", "password": "long-test-password",
        "display_name": "Test", "tenant_name": "Test tenant",
    })
    assert response.status_code == 201
    token = response.json()["access_token"]
    user_id = decode_token(token, expected_type="access")["sub"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = client.post("/projects", headers=headers, json={"name": "Project"}).json()["id"]
    with session_scope() as session:
        if tools:
            session.get(User, user_id).scopes_csv += ",tools"
        if root:
            session.get(Project, project_id).root_path = str(root)
        session.commit()
    return headers, user_id, project_id


def pending_write(client, headers, project_id, path):
    response = client.post("/tools/invoke", headers=headers, json={
        "project_id": project_id, "tool_name": "write_file", "args": {"path": str(path), "content": "approved"},
    })
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"
    return response.json()["id"]


def test_signup_cannot_self_grant_tools_or_filesystem_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(api.app) as client:
        headers, _, _ = identity(client)
        assert set(client.get("/me", headers=headers).json()["scopes"]) == {"chat", "rag"}
        assert client.post("/runs", headers=headers, json={"message": "show CPU health"}).status_code == 403
        assert client.post("/runs/stream", headers=headers, json={"message": "status", "agent_id": "operator"}).status_code == 403
        assert client.get("/monitoring/summary", headers=headers).status_code == 403
        assert client.get("/tools", headers=headers).status_code == 403
        assert client.post("/projects", headers=headers, json={"name": "Escape", "root_path": str(tmp_path)}).status_code == 403


@pytest.mark.parametrize("endpoint", ["/runs", "/runs/stream"])
def test_chat_scope_never_offers_model_tools(endpoint, tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    fake = Adapters([CompletionResult(text="", tool_calls=[ToolCallRequest(id="x", name="list_dir", arguments={"path": str(tmp_path)})])])
    with TestClient(api.app) as client:
        api.app.state.adapters = fake
        headers, _, project_id = identity(client, root=tmp_path)
        response = client.post(endpoint, headers=headers, json={"message": "read code", "project_id": project_id, "agent_id": "coder"})
        assert response.status_code in {200, 201}
        assert fake.offered == []
        assert fake.messages


def test_tool_cannot_read_a_sibling_project_under_global_roots(tmp_path, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (two / "secret.txt").write_text("other tenant")
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(api.app) as client:
        headers, _, project_id = identity(client, root=one, tools=True)
        response = client.post("/tools/invoke", headers=headers, json={
            "project_id": project_id, "tool_name": "read_file", "args": {"path": str(two / "secret.txt")},
        })
        assert response.status_code == 403
        assert "other tenant" not in response.text
        indexed = client.post("/rag/index", headers=headers, json={"project_id": project_id, "paths": [str(two)]})
        assert indexed.status_code == 403


def test_model_tool_revalidates_scope_after_network_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(api.app) as client:
        headers, user_id, project_id = identity(client, root=tmp_path, tools=True)
        def revoke():
            with session_scope() as session:
                session.get(User, user_id).scopes_csv = "chat,rag,monitoring:read"
                session.commit()
        fake = Adapters([CompletionResult(text="", tool_calls=[ToolCallRequest(id="x", name="list_dir", arguments={"path": str(tmp_path)})])], revoke=revoke)
        api.app.state.adapters = fake
        response = client.post("/runs", headers=headers, json={"message": "read code", "project_id": project_id, "agent_id": "coder"})
        assert response.status_code == 201
        assert "list_dir" in fake.offered[0]
        events = response.json()["events"]
        assert any(event["type"] == "tool.rejected" and "scope" in event["data"]["error"] for event in events)
        assert not any(event["type"] == "tool.completed" for event in events)


def test_model_cannot_invent_an_unoffered_host_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(api.app) as client:
        headers, _, project_id = identity(client, root=tmp_path, tools=True)
        fake = Adapters([CompletionResult(text="", tool_calls=[ToolCallRequest(id="x", name="docker_ps", arguments={})])])
        api.app.state.adapters = fake
        response = client.post("/runs", headers=headers, json={"message": "inspect code", "project_id": project_id, "agent_id": "coder"})
        assert "docker_ps" not in fake.offered[0]
        assert any(event["type"] == "tool.rejected" for event in response.json()["events"])


def test_approval_rechecks_project_membership(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "blocked.txt"
    with TestClient(api.app) as client:
        headers, user_id, project_id = identity(client, root=tmp_path, tools=True)
        invocation_id = pending_write(client, headers, project_id, target)
        with session_scope() as session:
            session.delete(session.get(ProjectMember, (project_id, user_id)))
            session.commit()
        response = client.post(f"/tools/{invocation_id}/approve", headers=headers)
        assert response.status_code == 403
    assert not target.exists()


def test_approval_claim_blocks_duplicate_and_competing_deny(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    entered = threading.Event()
    release = threading.Event()
    executions = []
    async def execute(call, **kwargs):
        executions.append(call.args)
        entered.set()
        await asyncio.to_thread(release.wait, 5)
        return {"ok": True}
    monkeypatch.setattr(api, "execute_tool", execute)
    with TestClient(api.app) as client:
        headers, _, project_id = identity(client, root=tmp_path, tools=True)
        invocation_id = pending_write(client, headers, project_id, tmp_path / "once.txt")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.post, f"/tools/{invocation_id}/approve", headers=headers)
            assert entered.wait(5)
            try:
                assert client.post(f"/tools/{invocation_id}/approve", headers=headers).status_code == 409
                assert client.post(f"/tools/{invocation_id}/deny", headers=headers).status_code == 409
            finally:
                release.set()
            assert first.result(timeout=5).json()["status"] == "completed"
    assert len(executions) == 1


def test_agent_identity_routing_and_office_are_persisted():
    fake = Adapters()
    with TestClient(api.app) as client:
        api.app.state.adapters = fake
        headers, _, project_id = identity(client)
        catalog = client.get("/agents", headers=headers).json()
        assert {agent["id"] for agent in catalog["agents"]} == {"manager", "coder", "researcher", "operator", "writer"}
        assert catalog["execution"]["durable_queue"] is False
        assert not any(agent["tools_enabled"] for agent in catalog["agents"])
        response = client.post("/runs", headers=headers, json={"message": "fix this Python bug", "project_id": project_id})
        body = response.json()
        assert body["agent_id"] == "manager"
        assert body["execution_agent_id"] == "coder"
        assert fake.modes == ["coding"]
        assert "Code Engineer" in fake.messages[0][0]["content"]
        assert any(event["type"] == "agent.delegated" for event in body["events"])
        assert client.get(f"/runs/{body['id']}", headers=headers).json()["execution_agent_id"] == "coder"
        snapshot = client.get("/api/v1/office/snapshot", headers=headers).json()
        coder = next(agent for agent in snapshot["agents"] if agent["id"] == "coder")
        assert coder["status"] == "idle"
        assert coder["lastRunId"] == body["id"]
        assert any(task["id"] == body["id"] and task["status"] == "completed" for task in snapshot["tasks"])
        other_headers, _, _ = identity(client)
        other = client.get("/api/v1/office/snapshot", headers=other_headers).json()
        assert all(agent["lastRunId"] is None for agent in other["agents"])


def configured_history_router(monkeypatch, *, cloud=True):
    router = ModelRouter(Settings(_env_file=None, llm_allow_cloud=cloud,
        openai_api_key="test-placeholder", anthropic_api_key=None, gemini_api_key=None,
        openrouter_api_key=None, groq_api_key=None, xai_api_key=None,
        self_hosted_base_url=None))
    monkeypatch.setattr(llm_runtime, "_router", router)
    return router


def test_direct_chat_preserves_history_and_distinct_persona(monkeypatch):
    configured_history_router(monkeypatch)

    fake = Adapters()
    with TestClient(api.app) as client:
        api.app.state.adapters = fake
        headers, _, _ = identity(client)
        for message in ["Draft an introduction", "Make that shorter"]:
            response = client.post("/chat", headers=headers, json={"session_id": "writing", "agent_id": "writer", "message": message})
            assert response.status_code == 200
            assert response.json()["execution_agent_id"] == "writer"
        assert "Writing Partner" in fake.messages[-1][0]["content"]
        assert [item["role"] for item in fake.messages[-1]] == ["system", "user", "assistant", "user"]
        assert fake.messages[-1][1]["content"] == "Draft an introduction"
        switched = client.post("/chat", headers=headers, json={"session_id": "writing", "agent_id": "writer", "provider": "openai", "message": "A separate request"})
        assert switched.status_code == 200
        assert [item["role"] for item in fake.messages[-1]] == ["system", "user"]
        assert client.post("/chat", headers=headers, json={"session_id": "x", "message": "hello", "agent_id": "fake-agent"}).status_code == 400
        assert client.post("/runs", headers=headers, json={"message": "hello", "agent_id": "fake-agent"}).status_code == 400


def test_stream_returns_persisted_run_identity():
    with TestClient(api.app) as client:
        api.app.state.adapters = Adapters()
        headers, _, _ = identity(client)
        response = client.post("/runs/stream", headers=headers, json={"message": "write a greeting", "agent_id": "writer"})
        end_frame = response.text.split("event: end\ndata: ")[1].strip()
        run_id = json.loads(end_frame)["run_id"]
        body = client.get(f"/runs/{run_id}", headers=headers).json()
        assert body["agent_id"] == body["execution_agent_id"] == "writer"


def test_run_threads_use_persisted_context_without_crossing_agent_project_or_provider(monkeypatch):
    configured_history_router(monkeypatch)
    fake = Adapters()
    with TestClient(api.app) as client:
        api.app.state.adapters = fake
        headers, _, project_id = identity(client)
        base = {"session_id": "explicit-thread", "agent_id": "writer", "message": "First request"}
        first = client.post("/runs", headers=headers, json=base).json()
        second = client.post("/runs", headers=headers, json={**base, "message": "Revise that"}).json()
        assert first["conversation_id"] == second["conversation_id"]
        assert [item["role"] for item in fake.messages[-1]] == ["system", "user", "assistant", "user"]
        assert fake.messages[-1][1]["content"] == "First request"
        assert fake.messages[-1][2]["content"] == "Grounded answer"
        for change in [{"agent_id": "coder"}, {"project_id": project_id}, {"provider": "openai"}, {"session_id": "other-thread"}]:
            separate = client.post("/runs", headers=headers, json={**base, **change}).json()
            assert separate["conversation_id"] != first["conversation_id"]
            assert [item["role"] for item in fake.messages[-1]] == ["system", "user"]
        # Omitted session IDs retain the stateless API contract.
        stateless = client.post("/runs", headers=headers, json={"agent_id": "writer", "message": "New question"}).json()
        assert stateless["conversation_id"] is None
        assert len(fake.messages[-1]) == 2


def test_project_change_requires_new_direct_chat_session():
    with TestClient(api.app) as client:
        api.app.state.adapters = Adapters()
        headers, _, project_id = identity(client)
        assert client.post("/chat", headers=headers, json={"session_id": "one", "message": "hello"}).status_code == 200
        response = client.post("/chat", headers=headers, json={"session_id": "one", "message": "hello", "project_id": project_id})
        assert response.status_code == 409


def test_approved_run_resumes_and_office_no_longer_waits(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(api.app) as client:
        headers, _, project_id = identity(client, root=tmp_path, tools=True)
        fake = Adapters([CompletionResult(text="", tool_calls=[ToolCallRequest(
            id="write", name="write_file", arguments={"path": str(tmp_path / "note.txt"), "content": "hello"},
        )])])
        api.app.state.adapters = fake
        response = client.post("/runs", headers=headers, json={"agent_id": "coder", "message": "write code", "project_id": project_id})
        run = response.json()
        assert run["status"] == "awaiting_approval"
        invocation_id = next(event["data"]["invocation_id"] for event in run["events"] if event["type"] == "tool.pending_approval")
        before = client.get("/api/v1/office/snapshot", headers=headers).json()
        assert next(agent for agent in before["agents"] if agent["id"] == "coder")["status"] == "waiting_approval"
        assert client.post(f"/tools/{invocation_id}/approve", headers=headers).json()["status"] == "completed"
        after = client.get(f"/runs/{run['id']}", headers=headers).json()
        assert after["status"] == "completed"
        assert after["events"][-1]["type"] == "run.completed"
        assert len(fake.messages) == 2
        assert fake.messages[-1][-1]["tool_call_id"] == "write"
        snapshot = client.get("/api/v1/office/snapshot", headers=headers).json()
        assert snapshot["approvals"] == []
        assert next(agent for agent in snapshot["agents"] if agent["id"] == "coder")["status"] == "idle"


class RoutedHistoryAdapters(Adapters):
    """Use the production router and prompt builder without provider network I/O."""
    async def monitoring_tool(self, name, args):
        return {"private_host": "internal-production-host"}

    async def llm(self, messages, **kwargs):
        async for token in llm_runtime.stream_chat(messages, **kwargs):
            yield token


def record_history_providers(router):
    calls = []
    class Provider:
        def __init__(self, name):
            self.name = name

        async def stream(self, messages, **kwargs):
            calls.append((self.name, messages, kwargs))
            yield "private host is internal-production-host" if self.name == "ollama" else "Cloud answer"
    router._providers["ollama"] = Provider("ollama")
    router._providers["openai"] = Provider("openai")
    return calls


@pytest.mark.parametrize("endpoint", ["/chat", "/runs", "/runs/stream"])
def test_auto_mode_switch_cannot_send_local_monitoring_history_to_cloud(endpoint, monkeypatch):
    router = configured_history_router(monkeypatch)
    calls = record_history_providers(router)
    with TestClient(api.app) as client:
        api.app.state.adapters = RoutedHistoryAdapters()
        headers, user_id, _ = identity(client)
        with session_scope() as session:
            session.get(User, user_id).scopes_csv += ",monitoring:read"
            session.commit()
        for message in ["show CPU health", "Summarize that", "Make that shorter"]:
            response = client.post(endpoint, headers=headers, json={"session_id": "same-thread", "message": message})
            assert response.status_code in {200, 201}, response.text
        assert [call[0] for call in calls] == ["ollama", "openai", "openai"]
        assert "internal-production-host" in json.dumps(calls[0][1])
        assert "internal-production-host" not in json.dumps(calls[1][1])
        assert "show CPU health" not in json.dumps(calls[1][1])
        assert "Cloud answer" in json.dumps(calls[2][1]), "same-route context should still persist"
        assert "internal-production-host" not in json.dumps(calls[2][1])


@pytest.mark.parametrize("endpoint", ["/chat", "/runs", "/runs/stream"])
def test_auto_config_change_cannot_send_same_mode_local_history_to_cloud(endpoint, monkeypatch):
    router = configured_history_router(monkeypatch, cloud=False)
    calls = record_history_providers(router)
    with TestClient(api.app) as client:
        api.app.state.adapters = RoutedHistoryAdapters()
        headers, _, _ = identity(client)
        first = client.post(endpoint, headers=headers, json={"session_id": "same-thread", "message": "A private note"})
        assert first.status_code in {200, 201}
        router.settings.llm_allow_cloud = True
        second = client.post(endpoint, headers=headers, json={"session_id": "same-thread", "message": "Summarize that"})
        assert second.status_code in {200, 201}
        assert [call[0] for call in calls] == ["ollama", "openai"]
        assert "internal-production-host" not in json.dumps(calls[1][1])
        assert "A private note" not in json.dumps(calls[1][1])


@pytest.mark.parametrize("endpoint", ["/chat", "/runs"])
def test_history_route_is_pinned_before_external_retrieval(endpoint, monkeypatch):
    router = configured_history_router(monkeypatch, cloud=False)
    calls = record_history_providers(router)
    class ChangeConfig(RoutedHistoryAdapters):
        async def retrieve(self, *args):
            router.settings.llm_allow_cloud = True
            return []
    with TestClient(api.app) as client:
        api.app.state.adapters = ChangeConfig()
        headers, _, project_id = identity(client)
        response = client.post(endpoint, headers=headers, json={
            "session_id": "pin-test", "agent_id": "coder", "project_id": project_id, "message": "review this code",
        })
        assert response.status_code in {200, 201}
        assert [call[0] for call in calls] == ["ollama"]
