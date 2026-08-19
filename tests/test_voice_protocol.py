from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from services.api import app
from services.api.adapters import Transcript


class FakeAdapters:
    received = b""

    async def transcribe(self, pcm: bytes, sample_rate: int, language: str | None) -> Transcript:
        self.received = pcm
        assert sample_rate == 16_000
        return Transcript("வணக்கம் Jarvis", "ta")

    async def retrieve(self, *args):
        return []

    async def llm(self, messages, *, mode, context):
        yield "வணக்கம்!"

    async def synthesize(self, text):
        return {"audio": "52494646", "format": "wav", "media_type": "audio/wav", "sample_rate": 16000}


def _login(client: TestClient) -> str:
    response = client.post("/auth/login", json={
        "email": "admin@example.com", "password": "correct-horse-battery-staple"
    })
    assert response.status_code == 200
    return response.json()["access_token"]


def test_invalid_websocket_authentication() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/voice/session") as ws:
            ws.send_json({"type": "auth", "access_token": "invalid"})
            try:
                ws.receive_json()
            except Exception as exc:
                assert getattr(exc, "code", 4401) == 4401


def test_binary_pcm_reaches_stt_and_client_does_not_send_transcript() -> None:
    fake = FakeAdapters()
    with TestClient(app) as client:
        app.state.adapters = fake
        token = _login(client)
        with client.websocket_connect("/voice/session") as ws:
            ws.send_json({"type": "auth", "access_token": token})
            assert ws.receive_json()["type"] == "authenticated"
            ws.send_json({"type": "start", "request_id": "req-1", "session_id": "sess-1",
                          "sample_rate": 16000, "language": "ta"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(b"\x01\x00\x02\x00")
            ws.send_json({"type": "stop"})
            events = [ws.receive_json() for _ in range(4)]
            assert [event["type"] for event in events] == ["transcript", "token", "audio", "final"]
            assert fake.received == b"\x01\x00\x02\x00"
            assert events[0]["data"]["text"] == "வணக்கம் Jarvis"


def test_barge_in_acknowledges_cancellation() -> None:
    class Slow(FakeAdapters):
        async def llm(self, messages, *, mode, context):
            await asyncio.sleep(1)
            yield "late"

    with TestClient(app) as client:
        app.state.adapters = Slow()
        token = _login(client)
        with client.websocket_connect("/voice/session") as ws:
            ws.send_json({"type": "auth", "access_token": token})
            ws.receive_json()
            ws.send_json({"type": "start"})
            ws.receive_json()
            ws.send_bytes(b"\x00\x00")
            ws.send_json({"type": "stop"})
            assert ws.receive_json()["type"] == "transcript"
            ws.send_json({"type": "barge_in"})
            assert ws.receive_json()["type"] == "barge_in"
