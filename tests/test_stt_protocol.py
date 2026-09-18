from fastapi.testclient import TestClient

from packages.common import get_settings
from services.stt import app


def test_stt_websocket_enforces_bounded_audio_buffer() -> None:
    settings = get_settings()
    original = settings.max_audio_bytes
    settings.max_audio_bytes = 4
    try:
        with TestClient(app) as client, client.websocket_connect("/voice/transcribe") as ws:
            ws.send_json({"type": "start", "request_id": "r", "session_id": "s"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(b"12345")
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["data"]["detail"] == "audio limit exceeded"
    finally:
        settings.max_audio_bytes = original
