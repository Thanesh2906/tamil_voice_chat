from __future__ import annotations

from fastapi.testclient import TestClient

from packages.common.config import Settings
from services.api import app


def test_production_rejects_unsafe_defaults() -> None:
    settings = Settings(JARVIS_ENV="production", POSTGRES_PASSWORD="change-me",
                        JWT_SECRET="development-only-change-me-secret-32bytes",
                        ALLOWED_ORIGINS=["http://localhost:5173"])
    try:
        settings.validate_runtime()
    except RuntimeError as exc:
        assert "unsafe production configuration" in str(exc)
    else:
        raise AssertionError("unsafe production configuration was accepted")


def test_refresh_rotation_rejects_replay() -> None:
    with TestClient(app) as client:
        login = client.post("/auth/login", json={
            "email": "admin@example.com", "password": "correct-horse-battery-staple"
        })
        assert login.status_code == 200
        original = login.json()["refresh_token"]
        first = client.post("/auth/refresh", json={"refresh_token": original})
        assert first.status_code == 200
        replay = client.post("/auth/refresh", json={"refresh_token": original})
        assert replay.status_code == 401


def test_projects_require_authentication() -> None:
    with TestClient(app) as client:
        assert client.get("/projects").status_code == 401
