from __future__ import annotations

from pathlib import Path

from services.monitoring import PROJECT_ID_RE, _parse_window
from services.rag import _resolve_authorized


def test_rag_rejects_traversal_and_secret_files(tmp_path: Path) -> None:
    outside = Path("/etc/passwd")
    try:
        _resolve_authorized(str(outside))
    except PermissionError:
        pass
    else:
        raise AssertionError("outside path was accepted")

    secret = Path.cwd() / ".env.private"
    secret.write_text("SECRET=value", encoding="utf-8")
    try:
        try:
            _resolve_authorized(str(secret))
        except PermissionError:
            pass
        else:
            raise AssertionError("secret file was accepted")
    finally:
        secret.unlink()


def test_monitoring_inputs_are_strict() -> None:
    assert PROJECT_ID_RE.fullmatch("prj-safe_01")
    assert not PROJECT_ID_RE.fullmatch('bad"} or vector(1)')
    assert _parse_window("15m").total_seconds() == 900
    for value in ("0m", "999h", "5m or vector(1)"):
        try:
            _parse_window(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe window accepted: {value}")
