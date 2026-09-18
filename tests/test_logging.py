import logging

from packages.common.logging import _RedactSecrets


def test_sensitive_log_values_are_redacted() -> None:
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1,
        "Authorization: Bearer abc access_token=def password=hunter2", (), None,
    )
    assert _RedactSecrets().filter(record)
    rendered = record.getMessage()
    assert "abc" not in rendered
    assert "def" not in rendered
    assert "hunter2" not in rendered
    assert rendered.count("[REDACTED]") == 3
