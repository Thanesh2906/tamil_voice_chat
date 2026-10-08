"""The API can advertise implemented foundations without claiming a connection."""

from typing import Any

PROTOCOL_VERSION = 1
IMPLEMENTED_CAPABILITIES = ("files.list", "files.read_text", "files.search_text")
UNIMPLEMENTED_CAPABILITIES = ("browser.control", "desktop.rpa", "shell.execute")


def disconnected_status() -> dict[str, Any]:
    """Return a fresh, deliberately non-activating public status response."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "state": "disconnected",
        "paired": False,
        "machine_id": None,
        "session_id": None,
        "platform_support": ["posix"],
        "capabilities": [
            {"name": name, "implemented": True, "available": False}
            for name in IMPLEMENTED_CAPABILITIES
        ] + [
            {"name": name, "implemented": False, "available": False}
            for name in UNIMPLEMENTED_CAPABILITIES
        ],
        "reason": "No user-reviewed desktop session. This server cannot access a user’s computer.",
    }
