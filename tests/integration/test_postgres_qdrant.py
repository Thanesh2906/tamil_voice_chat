from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy import inspect

pytestmark = pytest.mark.integration

if os.getenv("RUN_INTEGRATION") != "1":
    pytest.skip("set RUN_INTEGRATION=1 with disposable services", allow_module_level=True)


def test_postgres_schema_migrates() -> None:
    from packages.db import _engine, init_db

    init_db()
    tables = set(inspect(_engine).get_table_names())
    assert {
        "users", "tenants", "tenant_members", "projects", "project_members",
        "refresh_tokens", "conversations", "messages", "documents",
        "document_versions", "ingestion_jobs", "audit_events",
    }.issubset(tables)


def test_qdrant_filter_isolates_tenants() -> None:
    base = os.environ["QDRANT_URL"].rstrip("/")
    collection = f"jarvis_integration_{uuid.uuid4().hex}"
    with httpx.Client(timeout=20, trust_env=False) as client:
        created = client.put(
            f"{base}/collections/{collection}",
            json={"vectors": {"size": 2, "distance": "Cosine"}},
        )
        created.raise_for_status()
        try:
            upsert = client.put(
                f"{base}/collections/{collection}/points?wait=true",
                json={"points": [
                    {"id": 1, "vector": [1.0, 0.0], "payload": {"tenant_id": "a", "project_id": "p"}},
                    {"id": 2, "vector": [1.0, 0.0], "payload": {"tenant_id": "b", "project_id": "p"}},
                ]},
            )
            upsert.raise_for_status()
            result = client.post(
                f"{base}/collections/{collection}/points/search",
                json={
                    "vector": [1.0, 0.0],
                    "limit": 10,
                    "with_payload": True,
                    "filter": {"must": [
                        {"key": "tenant_id", "match": {"value": "a"}},
                        {"key": "project_id", "match": {"value": "p"}},
                    ]},
                },
            )
            result.raise_for_status()
            payloads = [point["payload"] for point in result.json()["result"]]
            assert payloads == [{"tenant_id": "a", "project_id": "p"}]
        finally:
            client.delete(f"{base}/collections/{collection}")
