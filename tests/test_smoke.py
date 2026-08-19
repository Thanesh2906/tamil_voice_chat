"""Tiny smoke tests that don't require GPUs or live models.

These run on any laptop and make sure the imports wire up before we
go near docker-compose.
"""

from __future__ import annotations


def test_settings_load() -> None:
    from packages.common import get_settings

    s = get_settings()
    assert s.jarvis_env
    assert s.llm_model


def test_password_hash_roundtrip() -> None:
    from packages.auth import hash_password, verify_password

    h = hash_password("correcthorsebattery")
    assert verify_password("correcthorsebattery", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip() -> None:
    from packages.auth import (
        create_access_token,
        decode_token,
        create_refresh_token,
        TokenError,
    )

    pair = create_access_token("user-123", scopes=["chat"])
    payload = decode_token(pair.access_token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert "chat" in payload["scopes"]

    rt = create_refresh_token("user-123")
    payload = decode_token(rt, expected_type="refresh")
    assert payload["sub"] == "user-123"

    try:
        decode_token(rt, expected_type="access")
    except TokenError:
        pass
    else:
        raise AssertionError("refresh token must not decode as access")


def test_chunking_code() -> None:
    from services.rag import _code_chunks_real, _heading_chunks

    code = "\n".join(f"line {i}" for i in range(200))
    parts = list(_code_chunks_real(code, "x.py"))
    assert len(parts) >= 3
    md = "# heading one\nbody\n## heading two\nmore"
    parts = list(_heading_chunks(md, "x.md"))
    assert len(parts) == 2
