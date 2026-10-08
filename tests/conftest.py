from __future__ import annotations

import os
import tempfile

import pytest

_db = tempfile.NamedTemporaryFile(prefix="jarvis-test-", suffix=".db", delete=False)
_db.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_db.name}")
os.environ.setdefault("JWT_SECRET", "test-only-secret-that-is-longer-than-thirty-two-bytes")
os.environ.setdefault("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("BOOTSTRAP_ADMIN_PASSWORD", "correct-horse-battery-staple")
os.environ.setdefault("RAG_ALLOWED_ROOTS", '["."]')


# TestClient uses one host for every case; isolate its rate-limit state without
# weakening production limits or tests that verify multiple requests in a case.


@pytest.fixture(autouse=True)
def isolated_api_rate_limit():
    from services.api import _request_windows

    _request_windows.clear()
    yield
    _request_windows.clear()
