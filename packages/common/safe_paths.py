"""Shared, security-critical path resolution.

Used by both RAG ingestion (services/rag) and the tool gateway (services/tools)
so there is exactly one implementation of "is this path inside an authorized
root and not a secret" to audit and fix, instead of two that can drift apart.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

# Directories that are never indexed or touched even if they sit inside an
# otherwise-authorized root: build artifacts, dependency trees and VCS internals.
DEFAULT_SENSITIVE_DIR_NAMES = {
    ".git", "node_modules", "build", "dist", ".venv", "venv", "models", "__pycache__",
}


class PathSecurityError(PermissionError):
    """A path fell outside an authorized root, or matched a sensitive pattern."""


def is_sensitive(path: Path, sensitive_globs: list[str]) -> bool:
    parts = set(path.parts)
    if parts.intersection(DEFAULT_SENSITIVE_DIR_NAMES):
        return True
    return any(fnmatch.fnmatch(path.name.lower(), pattern.lower()) for pattern in sensitive_globs)


def resolve_authorized(path: str, roots: list[str], sensitive_globs: list[str]) -> Path:
    """Resolve a path that must already exist (read, list, search, run-in)."""
    candidate = Path(path).expanduser().resolve(strict=True)
    return _check_within_roots(candidate, roots, sensitive_globs)


def resolve_authorized_for_write(path: str, roots: list[str], sensitive_globs: list[str]) -> Path:
    """Resolve a path for a new or overwritten file.

    The parent directory must already exist and be inside an authorized root;
    this deliberately does not create directory trees, so a write can only ever
    land next to files a human already put there.
    """
    raw = Path(path).expanduser()
    parent = raw.parent.resolve(strict=True)
    candidate = parent / raw.name
    _check_within_roots(parent, roots, sensitive_globs)
    if is_sensitive(candidate, sensitive_globs):
        raise PathSecurityError("sensitive path is excluded")
    return candidate


def _check_within_roots(candidate: Path, roots: list[str], sensitive_globs: list[str]) -> Path:
    resolved_roots = [Path(root).expanduser().resolve(strict=True) for root in roots]
    if not any(candidate == root or candidate.is_relative_to(root) for root in resolved_roots):
        raise PathSecurityError("path is outside the configured allowed roots")
    if is_sensitive(candidate, sensitive_globs):
        raise PathSecurityError("sensitive path is excluded")
    return candidate
