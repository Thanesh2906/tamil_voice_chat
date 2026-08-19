from __future__ import annotations

import pytest

import services.rag as rag


@pytest.mark.asyncio
async def test_unchanged_rag_document_skips_embedding(monkeypatch) -> None:
    async def no_collection():
        return None

    async def unchanged(*args, **kwargs):
        return True

    monkeypatch.setattr(rag, "_qdrant_ensure_collection", no_collection)
    monkeypatch.setattr(rag, "_qdrant_has_version", unchanged)
    monkeypatch.setattr(rag, "_iter_paths", lambda paths, recursive: iter(["example.py"]))
    monkeypatch.setattr(rag, "_read_text", lambda path: "print('hello')")
    monkeypatch.setattr(rag.os.path, "getmtime", lambda path: 1.0)

    result = await rag.ingest(
        project_id="p1", tenant_id="t1", owner_id="u1", paths=["example.py"]
    )
    assert result.chunks_indexed == 0
    assert result.skipped == ["example.py: unchanged"]
