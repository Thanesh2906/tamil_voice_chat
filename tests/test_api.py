import json
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.app import app

client = TestClient(app)


def authenticated_user() -> tuple[dict, dict]:
    email = f"test-{uuid.uuid4().hex}@example.com"
    password = "correct-horse-battery-123"
    registered = client.post(
        "/auth/register",
        json={"email": email, "password": password, "tenant_name": "Test tenant"},
    )
    assert registered.status_code == 201
    tokens = client.post("/auth/login", json={"email": email, "password": password})
    assert tokens.status_code == 200
    return registered.json(), tokens.json()


def test_login_project_and_prompted_chat() -> None:
    _, tokens = authenticated_user()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    created = client.post("/projects", headers=headers, json={"name": "Clinic app"})
    assert created.status_code == 201
    listed = client.get("/projects", headers=headers)
    assert created.json()["id"] in {project["id"] for project in listed.json()}
    chat = client.post("/chat", headers=headers, json={"message": "வணக்கம்"})
    assert chat.status_code == 200
    assert chat.json()["answer"]
    assert chat.json()["citations"] == []


def test_voice_rejects_missing_authentication() -> None:
    with (
        pytest.raises(WebSocketDisconnect) as error,
        client.websocket_connect("/voice/session"),
    ):
        pass
    assert error.value.code == 4401


def test_authenticated_binary_voice_protocol() -> None:
    _, tokens = authenticated_user()
    with client.websocket_connect(
        "/voice/session", subprotocols=["jarvis-bearer", tokens["access_token"]]
    ) as websocket:
        websocket.send_text(
            json.dumps({"type": "start", "request_id": "api-turn", "sample_rate": 16_000})
        )
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_bytes(b"\x00\x00" * 100)
        websocket.send_text(json.dumps({"type": "stop"}))
        event_types = []
        while "final" not in event_types:
            event_types.append(websocket.receive_json()["type"])
        assert event_types[0] == "transcript"
        assert "token" in event_types
        assert "audio" in event_types
