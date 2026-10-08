"""Bounded POSIX fixture adapter, sharing the tool gateway's path policy.

Descriptor-relative traversal never follows symlinks. No writable descriptors,
subprocesses, network clients, or implicit filesystem roots are used.
"""

from __future__ import annotations

import asyncio
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from pydantic import BaseModel, ConfigDict, Field

from packages.common.safe_paths import is_sensitive, resolve_authorized
from services.tools.adapters import TEXT_EXTENSIONS

MAX_ENTRIES = 2_000
MAX_FILES = 200
MAX_READ_BYTES = 200_000
MAX_SEARCH_BYTES = 2_000_000
MAX_RESULTS = 100
CHUNK_BYTES = 8192
SECRET_GLOBS = (".env*", "*.pem", "*.key", "*credentials*", "*secret*", ".ssh", ".aws")


class FileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=4096)


class ReadArgs(FileArgs):
    max_bytes: int = Field(default=MAX_READ_BYTES, ge=1, le=MAX_READ_BYTES)


class SearchArgs(FileArgs):
    query: str = Field(min_length=1, max_length=500)
    recursive: bool = True
    max_results: int = Field(default=MAX_RESULTS, ge=1, le=MAX_RESULTS)


@contextmanager
def _open_path(path: Path) -> Iterator[int]:
    """Open each canonical component without following swapped symlinks."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index < len(path.parts) - 2:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


class PosixReadOnlyFiles:
    def __init__(self, roots: tuple[str, ...], checkpoint: Callable[[], None]):
        if os.name != "posix":
            raise ValueError("only POSIX is implemented")
        if not roots:
            raise ValueError("explicit roots are required")
        self.roots = roots
        self.checkpoint = checkpoint
        self.identities: dict[str, tuple[int, int]] = {}
        for root in roots:
            path = Path(root)
            if not path.is_absolute() or str(path.resolve(strict=True)) != root:
                raise ValueError("roots must be absolute canonical paths")
            with _open_path(path) as descriptor:
                metadata = os.fstat(descriptor)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise ValueError("root must be a directory")
                self.identities[root] = (metadata.st_dev, metadata.st_ino)

    def _resolve(self, raw: str) -> Path:
        self.checkpoint()
        if not Path(raw).is_absolute():
            raise ValueError("an absolute path is required")
        # A replaced grant root must not silently retarget the session.
        for root, identity in self.identities.items():
            with _open_path(Path(root)) as descriptor:
                metadata = os.fstat(descriptor)
                if (metadata.st_dev, metadata.st_ino) != identity:
                    raise PermissionError("session root changed")
        path = resolve_authorized(raw, list(self.roots), list(SECRET_GLOBS))
        if str(Path(raw)) != str(path):
            raise PermissionError("noncanonical paths and symlinks are not allowed")
        return path

    @contextmanager
    def _open(self, path: Path) -> Iterator[int]:
        root = next(root for root in self.roots if path.is_relative_to(Path(root)))
        with _open_path(Path(root)) as root_fd:
            metadata = os.fstat(root_fd)
            if (metadata.st_dev, metadata.st_ino) != self.identities[root]:
                raise PermissionError("session root changed")
            descriptor = os.dup(root_fd)
            try:
                parts = path.relative_to(root).parts
                for index, part in enumerate(parts):
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    if index < len(parts) - 1:
                        flags |= os.O_DIRECTORY
                    child = os.open(part, flags, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                yield descriptor
            finally:
                os.close(descriptor)

    async def _read(self, descriptor: int, limit: int) -> tuple[str, int, bool]:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise PermissionError("only singly-linked regular files are supported")
        chunks = []
        consumed = 0
        while consumed < limit:
            await asyncio.sleep(0)
            self.checkpoint()
            chunk = os.read(descriptor, min(CHUNK_BYTES, limit - consumed))
            if not chunk:
                break
            chunks.append(chunk)
            consumed += len(chunk)
        self.checkpoint()
        return b"".join(chunks).decode("utf-8", errors="replace"), consumed, metadata.st_size > consumed

    async def read_text(self, args: ReadArgs) -> dict[str, Any]:
        path = self._resolve(args.path)
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            raise ValueError("unsupported text file type")
        with self._open(path) as descriptor:
            content, _, truncated = await self._read(descriptor, args.max_bytes)
        return {"path": str(path), "content": content, "truncated": truncated}

    async def list_directory(self, args: FileArgs) -> dict[str, Any]:
        path = self._resolve(args.path)
        entries = []
        truncated = False
        with self._open(path) as descriptor, os.scandir(descriptor) as children:
            for index, child in enumerate(children):
                await asyncio.sleep(0)
                self.checkpoint()
                if index >= MAX_ENTRIES:
                    truncated = True
                    break
                if is_sensitive(path / child.name, list(SECRET_GLOBS)):
                    continue
                metadata = child.stat(follow_symlinks=False)
                if stat.S_ISDIR(metadata.st_mode):
                    kind = "dir"
                elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                    kind = "file"
                else:
                    continue
                entries.append({"name": child.name, "type": kind,
                                "size": metadata.st_size if kind == "file" else None})
        return {"path": str(path), "entries": sorted(entries, key=lambda x: x["name"]),
                "truncated": truncated}

    async def search_text(self, args: SearchArgs) -> dict[str, Any]:
        path = self._resolve(args.path)
        matches: list[dict[str, Any]] = []
        budget = {"entries": 0, "files": 0, "bytes": 0}
        truncated = False

        async def visit(descriptor: int, current: Path, depth: int = 0) -> None:
            nonlocal truncated
            await asyncio.sleep(0)
            self.checkpoint()
            if (budget["files"] >= MAX_FILES or budget["bytes"] >= MAX_SEARCH_BYTES
                    or len(matches) >= args.max_results):
                truncated = True
                return
            metadata = os.fstat(descriptor)
            if stat.S_ISREG(metadata.st_mode):
                if current.suffix.lower() not in TEXT_EXTENSIONS or metadata.st_nlink != 1:
                    return
                text, size, cut = await self._read(
                    descriptor, min(MAX_READ_BYTES, MAX_SEARCH_BYTES - budget["bytes"]),
                )
                budget["files"] += 1
                budget["bytes"] += size
                truncated |= cut
                for line_number, line in enumerate(text.splitlines(), 1):
                    self.checkpoint()
                    if args.query.casefold() in line.casefold():
                        matches.append({"path": str(current), "line": line_number, "text": line[:300]})
                        if len(matches) >= args.max_results:
                            truncated = True
                            return
                return
            if not stat.S_ISDIR(metadata.st_mode):
                return
            # Bound both traversal and the number of concurrently open directory FDs.
            if depth >= 16:
                truncated = True
                return
            with os.scandir(descriptor) as children:
                for child in children:
                    await asyncio.sleep(0)
                    self.checkpoint()
                    if (budget["entries"] >= MAX_ENTRIES or budget["files"] >= MAX_FILES
                            or budget["bytes"] >= MAX_SEARCH_BYTES or len(matches) >= args.max_results):
                        truncated = True
                        return
                    budget["entries"] += 1
                    candidate = current / child.name
                    if is_sensitive(candidate, list(SECRET_GLOBS)) or child.is_symlink():
                        continue
                    if child.is_dir(follow_symlinks=False) and not args.recursive:
                        continue
                    try:
                        child_fd = os.open(child.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                           dir_fd=descriptor)
                    except OSError:
                        continue
                    try:
                        await visit(child_fd, candidate, depth + 1)
                    finally:
                        os.close(child_fd)

        with self._open(path) as descriptor:
            await visit(descriptor, path)
        return {"query": args.query, "matches": matches, "truncated": truncated}
