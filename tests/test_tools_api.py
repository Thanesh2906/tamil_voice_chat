from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

import services.api as api_module
from packages.auth import decode_token
from packages.db import Project, User, session_scope
from services.api import app


def _register(client: TestClient) -> tuple[dict, str]:
    email = f"tools-{uuid.uuid4().hex}@example.com"
    registered = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery-staple",
            "display_name": "Tools Test",
            "tenant_name": "Tools Tenant",
        },
    )
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    project = client.post("/projects", headers=headers, json={"name": "Workspace"})
    assert project.status_code == 201
    project_id = project.json()["id"]
    # Tools/root assignment are administrator grants, never public signup defaults.
    user_id = decode_token(registered.json()["access_token"], expected_type="access")["sub"]
    with session_scope() as session:
        user = session.get(User, user_id)
        user.scopes_csv += ",tools"
        session.get(Project, project_id).root_path = api_module.settings.rag_allowed_roots[0]
        session.commit()
    return headers, project_id


def test_read_tool_executes_immediately_without_approval(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "readme.md").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(app) as client:
        headers, project_id = _register(client)
        response = client.post(
            "/tools/invoke", headers=headers,
            json={"tool_name": "list_dir", "project_id": project_id, "args": {"path": str(tmp_path)}},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "auto_approved"
    assert any(entry["name"] == "readme.md" for entry in body["result"]["entries"])


def test_write_tool_requires_approval_then_executes_on_approve(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "note.txt"
    with TestClient(app) as client:
        headers, project_id = _register(client)
        invoked = client.post(
            "/tools/invoke", headers=headers,
            json={"tool_name": "write_file", "project_id": project_id,
                  "args": {"path": str(target), "content": "written by jarvis"}},
        )
        assert invoked.status_code == 201
        body = invoked.json()
        assert body["status"] == "pending"
        assert body["result"] is None
        assert not target.exists(), "must not execute before approval"

        pending = client.get("/tools/pending", headers=headers)
        assert any(row["id"] == body["id"] for row in pending.json())

        approved = client.post(f"/tools/{body['id']}/approve", headers=headers)
        assert approved.status_code == 200
        approved_body = approved.json()
        assert approved_body["status"] == "completed"
        assert target.read_text(encoding="utf-8") == "written by jarvis"

        # approving twice is rejected: the invocation is no longer pending
        replay = client.post(f"/tools/{body['id']}/approve", headers=headers)
        assert replay.status_code == 409


def test_write_tool_deny_never_executes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    target = tmp_path / "denied.txt"
    with TestClient(app) as client:
        headers, project_id = _register(client)
        invoked = client.post(
            "/tools/invoke", headers=headers,
            json={"tool_name": "write_file", "project_id": project_id,
                  "args": {"path": str(target), "content": "should not land"}},
        )
        invocation_id = invoked.json()["id"]
        denied = client.post(f"/tools/{invocation_id}/deny", headers=headers)
        assert denied.status_code == 200
        assert denied.json()["status"] == "denied"
    assert not target.exists()


def test_another_user_cannot_approve_someone_elses_pending_invocation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(app) as client:
        owner_headers, owner_project = _register(client)
        other_headers, _ = _register(client)
        invoked = client.post(
            "/tools/invoke", headers=owner_headers,
            json={"tool_name": "write_file", "project_id": owner_project,
                  "args": {"path": str(tmp_path / "mine.txt"), "content": "x"}},
        )
        invocation_id = invoked.json()["id"]
        response = client.post(f"/tools/{invocation_id}/approve", headers=other_headers)
    assert response.status_code == 404


def test_unknown_tool_name_is_rejected_before_persisting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(api_module.settings, "rag_allowed_roots", [str(tmp_path)])
    with TestClient(app) as client:
        headers, project_id = _register(client)
        response = client.post(
            "/tools/invoke", headers=headers,
            json={"tool_name": "delete_everything", "project_id": project_id, "args": {}},
        )
    assert response.status_code == 400
