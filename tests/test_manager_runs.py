from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from services.api import app
from services.llm import CompletionResult, RoutingDecision
from services.llm.providers import ToolCallRequest


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    frames = [frame for frame in text.split("\n\n") if frame.strip()]
    parsed: list[tuple[str, dict]] = []
    for frame in frames:
        event_type: str | None = None
        data: dict | None = None
        for line in frame.splitlines():
            if line.startswith("event: "):
                event_type = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event_type is not None:
            parsed.append((event_type, data or {}))
    return parsed


class FakeAdapters:
    """No tool calls: drives the plain rag/monitoring path and a tool-free
    personal/coding turn (one llm_with_tools call that returns text)."""

    def __init__(self, tool_script: list[CompletionResult] | None = None) -> None:
        # Queue of CompletionResults returned by successive llm_with_tools
        # calls, for tests that simulate a multi-turn tool-calling exchange.
        self._tool_script = list(tool_script) if tool_script else None

    async def aclose(self):
        return None

    async def retrieve(self, *args):
        return [{"chunk_id": "c1", "file_path": "a.py", "snippet": "print(1)",
                 "line_start": 1, "line_end": 1, "score": 0.9}]

    async def llm(self, messages, *, mode, context, provider=None, model=None,
                  allow_cloud=None, on_decision=None):
        if on_decision:
            on_decision(RoutingDecision(provider="ollama", model="llama3.1:8b", reason=f"default for mode={mode}"))
        yield f"answer for mode={mode}"

    async def llm_with_tools(self, messages, *, mode, context, tools, provider=None, model=None,
                             allow_cloud=None, on_decision=None):
        if on_decision:
            on_decision(RoutingDecision(provider="ollama", model="llama3.1:8b", reason=f"default for mode={mode}"))
        if self._tool_script:
            return self._tool_script.pop(0)
        return CompletionResult(text=f"answer for mode={mode}", tool_calls=[])

    async def monitoring_tool(self, name, args):
        return {"healthy": True, "tool": name}


def _register(client: TestClient, *, tools=False, root=None, monitoring=False) -> tuple[dict, str]:
    email = f"runs-{uuid.uuid4().hex}@example.com"
    registered = client.post(
        "/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple",
              "display_name": "Runs Test", "tenant_name": "Runs Tenant"},
    )
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    project = client.post("/projects", headers=headers, json={"name": "Workspace"})
    assert project.status_code == 201
    project_id = project.json()["id"]
    if tools or monitoring:
        from packages.auth import decode_token
        from packages.db import Project, User, session_scope
        user_id = decode_token(registered.json()["access_token"], expected_type="access")["sub"]
        with session_scope() as session:
            if tools:
                session.get(User, user_id).scopes_csv += ",tools"
                session.get(Project, project_id).root_path = str(root)
            if monitoring:
                session.get(User, user_id).scopes_csv += ",monitoring:read"
            session.commit()
    return headers, project_id


def test_personal_run_persists_a_full_event_timeline() -> None:
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        headers, _ = _register(client)
        response = client.post("/runs", headers=headers, json={"message": "வணக்கம்"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    event_types = [event["type"] for event in body["events"]]
    assert event_types == ["run.started", "run.classified", "model.selected", "run.completed"]
    sequences = [event["sequence"] for event in body["events"]]
    assert sequences == sorted(sequences) == list(range(1, len(sequences) + 1))
    assert body["events"][-1]["data"]["answer"] == "answer for mode=personal"


def test_coding_run_with_project_emits_retrieval_event() -> None:
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        headers, project_id = _register(client)
        response = client.post(
            "/runs", headers=headers,
            json={"message": "fix this python bug", "project_id": project_id},
        )
    assert response.status_code == 201
    body = response.json()
    event_types = [event["type"] for event in body["events"]]
    assert "retrieval.completed" in event_types
    retrieval = next(e for e in body["events"] if e["type"] == "retrieval.completed")
    assert retrieval["data"]["chunk_count"] == 1


def test_monitoring_run_emits_tool_completed_event() -> None:
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        headers, _ = _register(client, monitoring=True)
        response = client.post("/runs", headers=headers, json={"message": "show CPU health"})
    assert response.status_code == 201
    body = response.json()
    tool_event = next(e for e in body["events"] if e["type"] == "tool.completed")
    assert tool_event["data"]["tool_name"] == "get_host_summary"
    assert tool_event["data"]["output"]["healthy"] is True


def test_run_is_listed_and_fetchable_and_replayable_via_sse() -> None:
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        headers, _ = _register(client)
        created = client.post("/runs", headers=headers, json={"message": "hello"})
        run_id = created.json()["id"]

        listed = client.get("/runs", headers=headers)
        assert listed.status_code == 200
        assert any(row["id"] == run_id for row in listed.json())

        fetched = client.get(f"/runs/{run_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["id"] == run_id

        events = client.get(f"/runs/{run_id}/events", headers=headers)
        assert events.status_code == 200
        assert "event: run.started" in events.text
        assert "event: run.completed" in events.text
        assert "event: end" in events.text

        # ?after= replays only later events
        partial = client.get(f"/runs/{run_id}/events", headers=headers, params={"after": 2})
        assert "event: run.started" not in partial.text
        assert "event: run.completed" in partial.text


def test_another_user_cannot_read_someone_elses_run() -> None:
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        owner_headers, _ = _register(client)
        other_headers, _ = _register(client)
        created = client.post("/runs", headers=owner_headers, json={"message": "private"})
        run_id = created.json()["id"]
        response = client.get(f"/runs/{run_id}", headers=other_headers)
    assert response.status_code == 404


def test_run_with_unresolvable_provider_fails_without_a_500() -> None:
    """No FakeAdapters override here: the real router rejects an unknown
    provider before any network call, so this exercises the actual failure
    path end to end without needing a live Ollama/cloud endpoint."""
    with TestClient(app) as client:
        headers, _ = _register(client)
        response = client.post(
            "/runs", headers=headers,
            json={"message": "hello", "provider": "does-not-exist"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "failed"
    failure = next(e for e in body["events"] if e["type"] == "run.failed")
    assert "does-not-exist" in failure["data"]["error"]


def test_personal_run_auto_executes_a_read_tool_the_model_requests(tmp_path, monkeypatch) -> None:
    import services.api as api_module

    (tmp_path / "readme.md").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    script = [
        CompletionResult(
            text="", tool_calls=[ToolCallRequest(id="call_1", name="list_dir", arguments={"path": str(tmp_path)})]
        ),
        CompletionResult(text="I see readme.md in that folder.", tool_calls=[]),
    ]
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters(tool_script=script)
        headers, project_id = _register(client, tools=True, root=tmp_path)
        response = client.post("/runs", headers=headers, json={"project_id": project_id, "mode": "personal", "message": "what files are in my project?"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    event_types = [event["type"] for event in body["events"]]
    assert "tool.completed" in event_types
    tool_event = next(e for e in body["events"] if e["type"] == "tool.completed")
    assert tool_event["data"]["tool_name"] == "list_dir"
    assert any(entry["name"] == "readme.md" for entry in tool_event["data"]["output"]["entries"])
    final = next(e for e in body["events"] if e["type"] == "run.completed")
    assert final["data"]["answer"] == "I see readme.md in that folder."


def test_personal_run_pauses_for_approval_on_a_write_tool_request(tmp_path, monkeypatch) -> None:
    import services.api as api_module

    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "note.txt"
    script = [
        CompletionResult(
            text="", tool_calls=[ToolCallRequest(
                id="call_1", name="write_file",
                arguments={"path": str(target), "content": "written by jarvis"},
            )],
        ),
    ]
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters(tool_script=script)
        headers, project_id = _register(client, tools=True, root=tmp_path)
        response = client.post("/runs", headers=headers, json={"project_id": project_id, "mode": "personal", "message": "save a note for me"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "awaiting_approval"
    assert not target.exists(), "a write tool must never run without approval, even mid-run"
    pending_event = next(e for e in body["events"] if e["type"] == "tool.pending_approval")
    invocation_id = pending_event["data"]["invocation_id"]

    with TestClient(app) as client:
        approved = client.post(f"/tools/{invocation_id}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"
    assert approved.json()["run_id"] is not None
    assert target.read_text(encoding="utf-8") == "written by jarvis"


def test_personal_run_stops_at_the_tool_iteration_limit(tmp_path, monkeypatch) -> None:
    import services.api as api_module

    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    # Always propose the same read tool call: the loop must stop instead of looping forever.
    endless = [
        CompletionResult(
            text="", tool_calls=[ToolCallRequest(id="call_x", name="list_dir", arguments={"path": str(tmp_path)})]
        )
        for _ in range(10)
    ]
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters(tool_script=endless)
        headers, project_id = _register(client, tools=True, root=tmp_path)
        response = client.post("/runs", headers=headers, json={"project_id": project_id, "mode": "personal", "message": "keep looking"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert any(event["type"] == "run.tool_limit_reached" for event in body["events"])


def test_runs_stream_delivers_events_live_and_ends_with_status() -> None:
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        headers, _ = _register(client, monitoring=True)
        response = client.post("/runs/stream", headers=headers, json={"message": "show CPU health"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(response.text)
    types = [event_type for event_type, _ in frames]
    assert types[0] == "run.started"
    assert "tool.completed" in types
    assert "token" in types  # the monitoring answer streams through adapters.llm()
    assert types[-2] == "run.completed"
    assert types[-1] == "end"
    end_data = frames[-1][1]
    assert end_data["status"] == "completed"


def test_runs_stream_token_events_are_ephemeral_not_persisted() -> None:
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters()
        headers, _ = _register(client, monitoring=True)
        streamed = client.post("/runs/stream", headers=headers, json={"message": "show CPU health"})
        frames = _parse_sse(streamed.text)
        # The stream never echoes back its own run id, so recover it from the
        # persisted list to check what actually landed in the database.
        listed = client.get("/runs", headers=headers)
        run_id = listed.json()[0]["id"]
        fetched = client.get(f"/runs/{run_id}", headers=headers)
    persisted_types = [event["type"] for event in fetched.json()["events"]]
    assert "token" not in persisted_types
    assert any(event_type == "token" for event_type, _ in frames)


def test_runs_stream_emits_each_token_chunk_as_its_own_event() -> None:
    class ChunkyAdapters(FakeAdapters):
        async def llm(self, messages, *, mode, context, provider=None, model=None,
                      allow_cloud=None, on_decision=None):
            if on_decision:
                on_decision(RoutingDecision(provider="ollama", model="llama3.1:8b", reason="default"))
            for chunk in ["Hi", " there", "!"]:
                yield chunk

    with TestClient(app) as client:
        app.state.adapters = ChunkyAdapters()
        headers, _ = _register(client, monitoring=True)
        # "show CPU health" classifies as monitoring mode, which uses the
        # plain streaming adapters.llm() path (personal/coding mode instead
        # goes through the non-streaming, tool-calling llm_with_tools()).
        response = client.post("/runs/stream", headers=headers, json={"message": "show CPU health"})
    frames = _parse_sse(response.text)
    token_texts = [data["data"]["text"] for event_type, data in frames if event_type == "token"]
    assert token_texts == ["Hi", " there", "!"]
    completed = next(data for event_type, data in frames if event_type == "run.completed")
    assert completed["data"]["answer"] == "Hi there!"


def test_runs_stream_surfaces_awaiting_approval_in_end_event(tmp_path, monkeypatch) -> None:
    import services.api as api_module

    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    script = [
        CompletionResult(
            text="", tool_calls=[ToolCallRequest(
                id="call_1", name="write_file",
                arguments={"path": str(tmp_path / "note.txt"), "content": "hi"},
            )],
        ),
    ]
    with TestClient(app) as client:
        app.state.adapters = FakeAdapters(tool_script=script)
        headers, project_id = _register(client, tools=True, root=tmp_path)
        response = client.post("/runs/stream", headers=headers, json={"project_id": project_id, "mode": "personal", "message": "save a note"})
    frames = _parse_sse(response.text)
    assert any(event_type == "tool.pending_approval" for event_type, _ in frames)
    end_data = frames[-1][1]
    assert end_data["status"] == "awaiting_approval"
