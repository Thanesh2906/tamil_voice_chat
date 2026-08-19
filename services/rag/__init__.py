"""RAG ingestion + retrieval (blueprint §4).

Pipeline:
- parse sources (md, txt, pdf, docx, code)
- chunk (code-aware for source, heading-aware for docs)
- embed (sentence-transformers locally; embeddings stay inside)
- index in Qdrant, keep authoritative metadata in PostgreSQL
- retrieval applies authorization filter before search
"""

from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional

import httpx
from fastapi import FastAPI, HTTPException

from packages.common import configure_logging, get_logger, get_settings
from packages.schemas.rag import IngestRequest, IngestResponse, RetrievedChunk

configure_logging()
log = get_logger("rag")

app = FastAPI(title="Jarvis RAG", version="1.0.0")


# ---------- chunking -------------------------------------------------------

@dataclass
class Chunk:
    text: str
    file_path: str
    language: Optional[str]
    start_line: int
    end_line: int
    tenant_id: str
    owner_id: str
    project_id: str
    branch: Optional[str]
    modified: float
    chunk_id: str


_CODE_LANGS = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php",
    ".sql": "sql", ".sh": "bash",
}


def _lang_for(path: str) -> Optional[str]:
    ext = os.path.splitext(path)[1].lower()
    return _CODE_LANGS.get(ext)


def _code_chunks(text: str, path: str, max_lines: int = 60) -> Iterable[Chunk]:
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        j = min(i + max_lines, n)
        yield text, path, None  # placeholder, filled by caller
        i = j


def _code_chunks_real(text: str, path: str, max_lines: int = 60) -> Iterable[tuple]:
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        j = min(i + max_lines, n)
        chunk = "\n".join(lines[i:j])
        yield chunk, i + 1, j
        i = j


def _heading_chunks(text: str, path: str) -> Iterable[tuple]:
    parts: List[tuple] = []
    buf: List[str] = []
    start_line = 1
    heading_re = re.compile(r"^\s*(#{1,6})\s+\S")
    for ln_no, line in enumerate(text.splitlines(), start=1):
        if heading_re.match(line) and buf:
            parts.append(("\n".join(buf), start_line, ln_no - 1))
            buf = []
            start_line = ln_no
        buf.append(line)
    if buf:
        parts.append(("\n".join(buf), start_line, text.count("\n") + 1))
    return parts


def _chunk_id(project_id: str, path: str, start: int, end: int) -> str:
    h = hashlib.sha256(f"{project_id}::{path}::{start}::{end}".encode("utf-8")).hexdigest()
    return h[:32]


def _read_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx",
               ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
               ".cs", ".rb", ".php", ".sql", ".sh"}:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    if ext == ".docx":
        from docx import Document
        return "\n".join(p.text for p in Document(path).paragraphs)
    raise ValueError(f"unsupported extension: {ext}")


def _iter_paths(paths: List[str], recursive: bool) -> Iterable[str]:
    for p in paths:
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            if recursive:
                for root, _, files in os.walk(p):
                    for f in files:
                        yield os.path.join(root, f)
            else:
                for f in os.listdir(p):
                    full = os.path.join(p, f)
                    if os.path.isfile(full):
                        yield full


# ---------- embeddings -----------------------------------------------------

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    return _embedder


def _embed(texts: List[str]) -> List[List[float]]:
    model = _get_embedder()
    vecs = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vecs]


# ---------- Qdrant ---------------------------------------------------------

async def _qdrant_ensure_collection() -> None:
    s = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f"{s.qdrant_url}/collections/{s.qdrant_collection}")
        if r.status_code == 404:
            await c.put(
                f"{s.qdrant_url}/collections/{s.qdrant_collection}",
                json={"vectors": {"size": 384, "distance": "Cosine"}},
            )


async def _qdrant_upsert(points: List[dict]) -> None:
    s = get_settings()
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.put(
            f"{s.qdrant_url}/collections/{s.qdrant_collection}/points",
            json={"points": points},
        )
        r.raise_for_status()


async def _qdrant_search(vector: List[float], tenant_id: str, owner_id: str, project_id: str, top_k: int = 6) -> List[dict]:
    s = get_settings()
    flt = {
        "must": [
            {"key": "tenant_id", "match": {"value": tenant_id}},
            {"key": "owner_id", "match": {"value": owner_id}},
            {"key": "project_id", "match": {"value": project_id}},
        ]
    }
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.post(
            f"{s.qdrant_url}/collections/{s.qdrant_collection}/points/search",
            json={"vector": vector, "filter": flt, "limit": top_k, "with_payload": True},
        )
        r.raise_for_status()
        return r.json().get("result", [])


# ---------- ingestion ------------------------------------------------------

async def ingest(
    *,
    project_id: str,
    tenant_id: str,
    owner_id: str,
    paths: List[str],
    recursive: bool = True,
    branch: Optional[str] = None,
) -> IngestResponse:
    await _qdrant_ensure_collection()
    files_seen = 0
    chunks_indexed = 0
    skipped: List[str] = []
    pending: List[Chunk] = []

    for path in _iter_paths(paths, recursive):
        files_seen += 1
        try:
            text = _read_text(path)
        except Exception as e:
            skipped.append(f"{path}: {e.__class__.__name__}")
            continue
        lang = _lang_for(path)
        mtime = os.path.getmtime(path)
        if lang:
            chunk_iter = _code_chunks_real(text, path)
        else:
            chunk_iter = _heading_chunks(text, path)
        for body, start, end in chunk_iter:
            cid = _chunk_id(project_id, path, start, end)
            pending.append(
                Chunk(
                    text=body,
                    file_path=path,
                    language=lang,
                    start_line=start,
                    end_line=end,
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                    project_id=project_id,
                    branch=branch,
                    modified=mtime,
                    chunk_id=cid,
                )
            )

    if pending:
        vectors = _embed([c.text for c in pending])
        points = []
        for c, v in zip(pending, vectors):
            points.append(
                {
                    "id": c.chunk_id,
                    "vector": v,
                    "payload": {
                        "file_path": c.file_path,
                        "language": c.language,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "tenant_id": c.tenant_id,
                        "owner_id": c.owner_id,
                        "project_id": c.project_id,
                        "branch": c.branch,
                        "modified": c.modified,
                        "text": c.text,
                    },
                }
            )
        await _qdrant_upsert(points)
        chunks_indexed = len(points)

    return IngestResponse(
        project_id=project_id,
        files_seen=files_seen,
        chunks_indexed=chunks_indexed,
        skipped=skipped,
    )


async def retrieve(
    *,
    question: str,
    tenant_id: str,
    owner_id: str,
    project_id: str,
    top_k: int = 6,
) -> List[RetrievedChunk]:
    qv = _embed([question])[0]
    hits = await _qdrant_search(qv, tenant_id=tenant_id, owner_id=owner_id, project_id=project_id, top_k=top_k)
    out: List[RetrievedChunk] = []
    for h in hits:
        payload = h.get("payload", {})
        out.append(
            RetrievedChunk(
                chunk_id=str(h.get("id")),
                file_path=payload.get("file_path"),
                language=payload.get("language"),
                snippet=payload.get("text", ""),
                score=float(h.get("score", 0.0)),
                project_id=payload.get("project_id", project_id),
                tenant_id=payload.get("tenant_id", tenant_id),
                owner_id=payload.get("owner_id", owner_id),
            )
        )
    return out


# ---------- HTTP -----------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/rag/index", response_model=IngestResponse)
async def rag_index(req: IngestRequest, tenant_id: str, owner_id: str) -> IngestResponse:
    if not req.paths:
        raise HTTPException(status_code=400, detail="paths required")
    return await ingest(
        project_id=req.project_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        paths=req.paths,
        recursive=req.recursive,
        branch=req.branch,
    )


@app.get("/rag/sources", response_model=List[RetrievedChunk])
async def rag_sources(
    q: str,
    project_id: str,
    tenant_id: str,
    owner_id: str,
    top_k: int = 6,
) -> List[RetrievedChunk]:
    return await retrieve(question=q, tenant_id=tenant_id, owner_id=owner_id, project_id=project_id, top_k=top_k)
