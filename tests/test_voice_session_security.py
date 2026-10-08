from __future__ import annotations

import asyncio
import json
import threading
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import services.api as api
from packages.auth import decode_token
from packages.db import ProjectMember, User, session_scope
from services.api.adapters import Transcript


class Adapter:
    def __init__(self):
        self.received = []
        self.retrieved = []

    async def transcribe(self, pcm, sample_rate, language):
        self.received.append((pcm, sample_rate))
        return Transcript("Hello", "en")

    async def retrieve(self, *args):
        self.retrieved.append(args)
        return []

    async def llm(self, *args, **kwargs):
        yield "Hello!"

    async def synthesize(self, text):
        return {"audio": "00"}


@pytest.fixture
def client():
    with TestClient(api.app) as value:
        api.app.state.adapters = Adapter()
        yield value


def register(client):
    response = client.post("/auth/register", json={"email": f"voice-{uuid.uuid4().hex}@example.com",
        "password": "long-test-password", "display_name": "Voice", "tenant_name": "Voice"})
    token = response.json()["access_token"]
    user_id = decode_token(token, expected_type="access")["sub"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = client.post("/projects", headers=headers, json={"name": "Project"}).json()["id"]
    return token, user_id, project_id


@contextmanager
def socket(client, token=None):
    if token is None:
        token, _, _ = register(client)
    with client.websocket_connect("/voice/session") as ws:
        ws.send_json({"type": "auth", "access_token": token})
        assert ws.receive_json()["type"] == "authenticated"
        yield ws


def start(ws, **kwargs):
    ws.send_json({"type": "start", "request_id": "req", "session_id": "sess", **kwargs})
    return ws.receive_json()


def finish(ws):
    ws.send_bytes(b"\0\0")
    ws.send_json({"type": "stop"})
    events = []
    for _ in range(10):
        event = ws.receive_json()
        events.append(event)
        if event["type"] in {"final", "error"}:
            return events
    raise AssertionError("voice turn did not finish")


def test_malformed_controls_and_rates_do_not_kill_connection(client):
    with socket(client) as ws:
        for raw in ["{", "[]", "null", '"string"', json.dumps({"type": 1}), "[" * 1500 + "]" * 1500, "x" * 4097]:
            ws.send_text(raw)
            assert ws.receive_json()["type"] == "error"
        for rate in ["16000", "wrong", 0, -1, True, 1.5, 999999]:
            response = start(ws, sample_rate=rate)
            assert response["type"] == "error"
            assert "sample_rate" in response["data"]["detail"]
        assert start(ws, sample_rate=48000)["type"] == "ready"
        assert finish(ws)[-1]["type"] == "final"
        assert api.app.state.adapters.received == [(b"\0\0", 48000)]


def test_audio_only_accepted_between_ready_and_stop(client):
    with socket(client) as ws:
        ws.send_bytes(b"\0\0")
        assert ws.receive_json()["type"] == "error"
        assert start(ws)["type"] == "ready"
        ws.send_bytes(b"x")
        assert ws.receive_json()["type"] == "error"
        assert finish(ws)[-1]["type"] == "final"
        ws.send_bytes(b"\0\0")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "error"
        assert len(api.app.state.adapters.received) == 1


def test_stale_stop_and_cancel_cannot_touch_new_recording(client):
    with socket(client) as ws:
        assert start(ws, request_id="new", session_id="new-session")["type"] == "ready"
        ws.send_bytes(b"\0\0")
        for control in ["stop", "barge_in"]:
            ws.send_json({"type": control, "request_id": "old", "session_id": "old-session"})
            assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "stop", "request_id": "new", "session_id": "new-session"})
        for _ in range(4):
            event = ws.receive_json()
            assert event["request_id"] == "new"
            assert event["session_id"] == "new-session"
        assert len(api.app.state.adapters.received) == 1


def test_unauthorized_start_never_enables_audio_capture(client):
    with socket(client) as ws:
        response = start(ws, project_id="not-my-project")
        assert response["type"] == "error"
        ws.send_bytes(b"\0\0")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "error"
        assert api.app.state.adapters.received == []
        assert start(ws)["type"] == "ready"


@pytest.mark.parametrize("revocation", ["membership", "rag"])
def test_project_access_revalidated_after_start(client, revocation):
    token, user_id, project_id = register(client)
    with socket(client, token) as ws:
        assert start(ws, project_id=project_id)["type"] == "ready"
        with session_scope() as db:
            if revocation == "membership":
                db.delete(db.get(ProjectMember, (project_id, user_id)))
            else:
                db.get(User, user_id).scopes_csv = "chat"
            db.commit()
        events = finish(ws)
        assert events[-1]["type"] == "error"
        assert api.app.state.adapters.received == []
        assert api.app.state.adapters.retrieved == []


@pytest.mark.parametrize("stage", ["transcribe", "retrieve"])
def test_service_errors_are_sanitized_and_session_recovers(client, stage):
    token, _, project_id = register(client)
    adapter = api.app.state.adapters
    original = getattr(adapter, stage)
    async def fail(*args, **kwargs):
        raise PermissionError("secret-api-key/private-internal-url")
    setattr(adapter, stage, fail)
    with socket(client, token) as ws:
        assert start(ws, project_id=project_id)["type"] == "ready"
        events = finish(ws)
        assert events[-1]["type"] == "error"
        assert "secret-api-key" not in json.dumps(events)
        assert events[-1]["request_id"] == "req"
        setattr(adapter, stage, original)
        assert start(ws, project_id=project_id)["type"] == "ready"
        assert finish(ws)[-1]["type"] == "final"


def test_new_start_cancels_partial_stt_before_ready(client):
    entered = threading.Event()
    cancelled = threading.Event()
    class SlowPartial(Adapter):
        async def transcribe(self, pcm, sample_rate, language):
            if len(pcm) > 2:
                entered.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    cancelled.set()
            return await super().transcribe(pcm, sample_rate, language)
    api.app.state.adapters = SlowPartial()
    with socket(client) as ws:
        assert start(ws)["type"] == "ready"
        ws.send_bytes(b"\0\0" * 32000)
        assert entered.wait(3)
        ready = start(ws, request_id="replacement", session_id="replacement")
        assert ready["type"] == "ready"
        assert cancelled.is_set()
        events = finish(ws)
        assert events[-1]["type"] == "final"
        assert all(event["request_id"] == "replacement" for event in events)


def test_barge_in_cancels_final_stt_without_waiting_for_service(client):
    entered = threading.Event()
    cancelled = threading.Event()
    class SlowFinal(Adapter):
        async def transcribe(self, *args):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()
    api.app.state.adapters = SlowFinal()
    with socket(client) as ws:
        assert start(ws)["type"] == "ready"
        ws.send_bytes(b"\0\0")
        ws.send_json({"type": "stop"})
        assert entered.wait(3)
        ws.send_json({"type": "barge_in"})
        assert ws.receive_json()["type"] == "barge_in"
        assert cancelled.is_set()


def test_empty_stop_also_ends_recording(client):
    with socket(client) as ws:
        assert start(ws)["type"] == "ready"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "error"
        ws.send_bytes(b"\0\0")
        assert ws.receive_json()["type"] == "error"
        assert api.app.state.adapters.received == []


@pytest.mark.parametrize("raw", ["[]", "null", "{", "[" * 1500 + "]" * 1500, "x" * 8193])
def test_invalid_auth_frame_closes_cleanly(client, raw):
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/voice/session") as ws:
        ws.send_text(raw)
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
        assert caught.value.code == 4401
