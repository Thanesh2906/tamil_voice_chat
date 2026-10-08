"""One-frame stdio prototype: disconnected, or an isolated temporary-file demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from services.desktop_bridge.protocol import DesktopBridge, SessionBinding
from services.desktop_bridge.status import IMPLEMENTED_CAPABILITIES, disconnected_status

MAX_FRAME_BYTES = 16_384


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonstandard JSON constant: {value}")


async def fixture_demo() -> dict[str, Any]:
    """Read only files created here; arguments cannot supply any host path."""
    with tempfile.TemporaryDirectory(prefix="jarvis-bridge-fixture-") as directory:
        root = Path(directory).resolve()
        note = root / "example.txt"
        note.write_text("JARVIS temporary fixture\nhello desktop bridge\n", encoding="utf-8")
        now = time.time()
        binding = SessionBinding("fixture-owner", "fixture-project", "fixture-machine",
                                 "fixture-session", (str(root),),
                                 frozenset(IMPLEMENTED_CAPABILITIES), now + 60)
        bridge = DesktopBridge(binding)
        results = []
        try:
            for index, capability in enumerate(IMPLEMENTED_CAPABILITIES):
                arguments = {"path": str(note if capability == "files.read_text" else root)}
                if capability == "files.search_text":
                    arguments["query"] = "hello"
                results.append(await bridge.handle({
                    "protocol_version": 1, "owner_id": binding.owner_id,
                    "project_id": binding.project_id, "machine_id": binding.machine_id,
                    "session_id": binding.session_id, "operation_id": f"fixture-op-{index}",
                    "deadline_at": time.time() + 5, "capability": capability, "arguments": arguments,
                }))
        finally:
            bridge.close()
    return {"mode": "temporary_fixture_only", "fixture_removed": not root.exists(),
            "runtime_status": disconnected_status(), "operations": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--demo", action="store_true", help="read newly created temporary fixtures only")
    group.add_argument("--status", action="store_true", help="print disconnected status and exit")
    args = parser.parse_args()
    if args.demo:
        response = asyncio.run(fixture_demo())
    elif args.status:
        response = disconnected_status()
    else:
        frame = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
        if not frame or len(frame) > MAX_FRAME_BYTES:
            response = DesktopBridge._error(None, "invalid_frame")
        else:
            try:
                payload = json.loads(frame, object_pairs_hook=_object, parse_constant=_reject_constant)
                response = asyncio.run(DesktopBridge().handle(payload))
            except (ValueError, UnicodeError, RecursionError):
                response = DesktopBridge._error(None, "invalid_frame")
    print(json.dumps(response, ensure_ascii=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
