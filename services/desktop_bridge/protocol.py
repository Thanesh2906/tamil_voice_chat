"""In-process protocol foundation. There is deliberately no pairing transport.

SessionBinding is trusted harness input, never accepted from a wire request.
The default construction is disconnected. Only temporary fixtures instantiate a
session in this repository; connecting real computers requires a separate review.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services.desktop_bridge.files import FileArgs, PosixReadOnlyFiles, ReadArgs, SearchArgs
from services.desktop_bridge.status import IMPLEMENTED_CAPABILITIES, PROTOCOL_VERSION

MAX_OPERATIONS = 1024
MAX_OPERATION_SECONDS = 30
MAX_SESSION_SECONDS = 600


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: int = Field(ge=1, le=1)
    owner_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    machine_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    deadline_at: float = Field(allow_inf_nan=False)
    action: Literal["execute", "cancel"] = "execute"
    capability: str | None = Field(default=None, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    target_operation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,128}$")


@dataclass(frozen=True)
class SessionBinding:
    owner_id: str
    project_id: str
    machine_id: str
    session_id: str
    roots: tuple[str, ...]
    capabilities: frozenset[str]
    expires_at: float


class BridgeError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DesktopBridge:
    """Single-event-loop bridge with bounded, non-evicting replay protection."""

    def __init__(self, binding: SessionBinding | None = None, *, clock: Callable[[], float] = time.time):
        self.binding = binding
        self.clock = clock
        self.seen: set[str] = set()
        self.active: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self.closed = False
        self.files: PosixReadOnlyFiles | None = None
        if binding is not None:
            for value in (binding.owner_id, binding.project_id, binding.machine_id, binding.session_id):
                if not isinstance(value, str) or not 1 <= len(value) <= 128 or not value.strip():
                    raise ValueError("session identities must be explicit nonempty strings")
            if (not math.isfinite(binding.expires_at)
                    or not 0 < binding.expires_at - clock() <= MAX_SESSION_SECONDS):
                raise ValueError("session must expire within ten minutes")
            if (not isinstance(binding.roots, tuple) or not isinstance(binding.capabilities, frozenset)
                    or not binding.capabilities
                    or not binding.capabilities.issubset(IMPLEMENTED_CAPABILITIES)):
                raise ValueError("explicit immutable roots and implemented read capabilities required")
            self._expires_monotonic = time.monotonic() + binding.expires_at - clock()
            self.files = PosixReadOnlyFiles(binding.roots, self._session_checkpoint)

    def _session_checkpoint(self) -> None:
        if self.closed or self.binding is None:
            raise BridgeError("disconnected")
        if self.clock() >= self.binding.expires_at or time.monotonic() >= self._expires_monotonic:
            raise BridgeError("session_expired")

    def close(self) -> None:
        """Revocation cancels outstanding operations; replay records remain."""
        self.closed = True
        for task in self.active.values():
            task.cancel()

    def _validate_binding(self, request: Request) -> None:
        self._session_checkpoint()
        assert self.binding is not None
        for field in ("owner_id", "project_id", "machine_id", "session_id"):
            if getattr(request, field) != getattr(self.binding, field):
                raise BridgeError("binding_mismatch")
        if request.operation_id in self.seen:
            raise BridgeError("replay_rejected")
        if len(self.seen) >= MAX_OPERATIONS:
            raise BridgeError("session_operation_limit")
        self.seen.add(request.operation_id)
        if request.deadline_at <= self.clock():
            raise BridgeError("deadline_exceeded")
        if (request.deadline_at > self.binding.expires_at
                or request.deadline_at - self.clock() > MAX_OPERATION_SECONDS):
            raise BridgeError("deadline_out_of_bounds")

    async def handle(self, payload: Any) -> dict[str, Any]:
        operation_id = None
        try:
            request = Request.model_validate(payload)
            operation_id = request.operation_id
            self._validate_binding(request)
            assert self.binding is not None
            if request.action == "cancel":
                if request.capability is not None or request.arguments or not request.target_operation_id:
                    raise BridgeError("invalid_request")
                task = self.active.get(request.target_operation_id)
                if task is None or task.done():
                    raise BridgeError("operation_not_active")
                task.cancel()
                return self._response(operation_id, {"cancel_requested": True,
                                                     "target_operation_id": request.target_operation_id})
            if request.target_operation_id is not None:
                raise BridgeError("invalid_request")
            if request.capability not in self.binding.capabilities:
                raise BridgeError("capability_denied")
            if self.active:
                raise BridgeError("operation_in_progress")
            task = asyncio.create_task(self._execute(request))
            self.active[request.operation_id] = task
            try:
                result = await task
                self._session_checkpoint()
                if self.clock() >= request.deadline_at:
                    raise BridgeError("deadline_exceeded")
                return self._response(operation_id, result)
            finally:
                self.active.pop(request.operation_id, None)
        except ValidationError:
            return self._error(operation_id, "invalid_request")
        except BridgeError as exc:
            return self._error(operation_id, exc.code)
        except asyncio.CancelledError:
            return self._error(operation_id, "cancelled")
        except TimeoutError:
            return self._error(operation_id, "deadline_exceeded")
        except (OSError, ValueError):
            # Do not expose raw exception paths, credentials, or partial reads.
            return self._error(operation_id, "file_access_denied")

    async def _execute(self, request: Request) -> dict[str, Any]:
        assert self.files is not None
        remaining = request.deadline_at - self.clock()
        monotonic_deadline = time.monotonic() + remaining

        def checkpoint() -> None:
            self._session_checkpoint()
            if self.clock() >= request.deadline_at or time.monotonic() >= monotonic_deadline:
                raise BridgeError("deadline_exceeded")

        self.files.checkpoint = checkpoint
        try:
            checkpoint()
            async with asyncio.timeout(remaining):
                if request.capability == "files.list":
                    result = await self.files.list_directory(FileArgs.model_validate(request.arguments))
                elif request.capability == "files.read_text":
                    result = await self.files.read_text(ReadArgs.model_validate(request.arguments))
                elif request.capability == "files.search_text":
                    result = await self.files.search_text(SearchArgs.model_validate(request.arguments))
                else:
                    raise BridgeError("capability_denied")
                checkpoint()
                return result
        finally:
            self.files.checkpoint = self._session_checkpoint

    @staticmethod
    def _response(operation_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return {"protocol_version": PROTOCOL_VERSION, "operation_id": operation_id,
                "ok": True, "result": result}

    @staticmethod
    def _error(operation_id: str | None, code: str) -> dict[str, Any]:
        return {"protocol_version": PROTOCOL_VERSION, "operation_id": operation_id,
                "ok": False, "error": {"code": code}}
