from fastapi.testclient import TestClient

from services.api import app, settings


def test_oversized_request_is_rejected_before_route_processing() -> None:
    original = settings.max_request_body_bytes
    settings.max_request_body_bytes = 4
    try:
        with TestClient(app) as client:
            response = client.post(
                "/auth/login",
                content=b"12345",
                headers={"content-type": "application/json", "content-length": "5"},
            )
        assert response.status_code == 413
    finally:
        settings.max_request_body_bytes = original
