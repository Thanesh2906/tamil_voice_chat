from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SENSITIVE_NAMES = {".env", ".npmrc", ".pypirc", "id_rsa", "id_ed25519", "credentials.json"}
IGNORED_PARTS = {".git", "node_modules", "__pycache__", ".venv", "venv", "build", "dist", "models"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdbx"}


class UnsafePath(ValueError):
    pass


def resolve_allowed_path(requested: Path, roots: tuple[Path, ...]) -> Path:
    candidate = requested.expanduser().resolve(strict=True)
    for root in roots:
        resolved_root = root.expanduser().resolve(strict=True)
        try:
            candidate.relative_to(resolved_root)
            return candidate
        except ValueError:
            continue
    raise UnsafePath("path is outside configured project roots")


def is_sensitive(path: Path) -> bool:
    lowered = {part.lower() for part in path.parts}
    name = path.name.lower()
    return (
        bool(lowered & IGNORED_PARTS)
        or name in SENSITIVE_NAMES
        or path.suffix.lower() in SENSITIVE_SUFFIXES
        or "secret" in name
    )


@dataclass(frozen=True, slots=True)
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str
    content_hash: str


def chunk_text(path: Path, text: str, max_chars: int = 1200) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    start = 0
    while start < len(lines):
        end = start
        size = 0
        while end < len(lines) and (size + len(lines[end]) + 1 <= max_chars or end == start):
            size += len(lines[end]) + 1
            end += 1
        body = "\n".join(lines[start:end]).strip()
        if body:
            chunks.append(
                Chunk(str(path), start + 1, end, body, hashlib.sha256(body.encode()).hexdigest())
            )
        start = end
    return chunks


class MemoryRagStore:
    """Deterministic, permission-filtered local adapter; replaceable by Qdrant."""

    def __init__(self) -> None:
        self._chunks: dict[tuple[str, str], list[Chunk]] = {}

    def upsert(self, tenant_id: str, project_id: str, chunks: list[Chunk]) -> None:
        self._chunks[(tenant_id, project_id)] = chunks

    def retrieve(self, tenant_id: str, project_id: str, query: str, limit: int = 5) -> list[Chunk]:
        words = set(re.findall(r"[\w\u0B80-\u0BFF]+", query.lower()))
        scored = []
        for chunk in self._chunks.get((tenant_id, project_id), []):
            score = len(words & set(re.findall(r"[\w\u0B80-\u0BFF]+", chunk.text.lower())))
            if score:
                scored.append((score, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].path, item[1].start_line))
        return [chunk for _, chunk in scored[:limit]]
