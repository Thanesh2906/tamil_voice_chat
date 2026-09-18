"""Deny-by-default policy gate in front of the tool adapters
(docs/master-build-audit.md, Phase 4 security invariants).

This module never touches the database or the approval workflow — see
services/api/__init__.py's /tools endpoints for that. It only answers two
questions: is this a real tool with valid arguments, and can it run right now
without a human's approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from services.tools.adapters import EXECUTORS, ToolExecutionError
from services.tools.registry import ToolSpec, get_tool


class ToolPolicyError(RuntimeError):
    """The request was rejected before anything ran: unknown tool or bad arguments."""


@dataclass(slots=True)
class ValidatedCall:
    spec: ToolSpec
    args: dict[str, Any]  # normalized, JSON-safe (pydantic .model_dump())


def validate(tool_name: str, raw_args: dict[str, Any]) -> ValidatedCall:
    """Look up the tool and validate arguments against its schema. Raises
    ToolPolicyError for an unknown tool or arguments that don't fit the schema
    -- this is the deny-by-default gate; nothing reaches here without a spec."""
    spec = get_tool(tool_name)
    if spec is None:
        raise ToolPolicyError(f"unknown tool: {tool_name}")
    try:
        model = spec.args_model.model_validate(raw_args)
    except ValidationError as exc:
        raise ToolPolicyError(f"invalid arguments for {tool_name}: {exc.errors()!r}") from exc
    return ValidatedCall(spec=spec, args=model.model_dump())


async def execute(call: ValidatedCall, *, roots: list[str], sensitive_globs: list[str]) -> dict:
    """Actually run a validated call. Callers are responsible for having
    obtained approval first when call.spec.risk != "read"."""
    executor = EXECUTORS[call.spec.name]
    args_model = call.spec.args_model.model_validate(call.args)
    try:
        return await executor(args_model, roots=roots, sensitive_globs=sensitive_globs)
    except ToolExecutionError:
        raise
    except Exception as exc:  # pragma: no cover - defensive: adapters should raise ToolExecutionError
        raise ToolExecutionError(f"{call.spec.name} failed: {exc}") from exc
