# JARVIS architecture

## Boundary

Web and Flutter clients communicate only with the FastAPI gateway over HTTPS/WSS. PostgreSQL, Qdrant, STT, TTS, Ollama, Prometheus and monitoring adapters remain on private networks.

## Voice

```text
authenticated client → bounded PCM16 → STT/resample → authorized RAG → prompted LLM
                     ← partial/final text, citations, tokens, chunked WAV audio ← TTS
```

The first WebSocket frame authenticates the connection; access tokens are never required in query strings. Each utterance has request/session IDs. New speech and `barge_in` cancel active response work. Raw audio is kept in bounded memory and discarded after transcription.

## Identity and authorization

`packages/db.py` defines users, tenants, tenant members, projects, project members, refresh tokens, conversations/messages, documents/versions, ingestion jobs and audit events. Project lookup joins server-side memberships. Token scopes are checked against current database scopes. Refresh JTIs are hashed, rotated once and rejected on replay.

Production startup rejects short/default JWT secrets, default database passwords, bootstrap credentials and insecure/wildcard origins.

## Agent and RAG

`services/llm` is the single LLM adapter. It always composes `prompts/system.txt`, an optional mode prompt, and authorized context clearly marked as untrusted data.

RAG ingestion resolves every path under configured roots, rejects traversal and sensitive files, batches embeddings outside the event loop and replaces older vectors for changed files. Every vector carries tenant, owner, project, document hash and line metadata. Retrieval always applies all authorization filters and returns citations.

## Monitoring

The model-facing surface consists of fixed functions: host, project, GPU and service-health summaries. Project/window inputs are validated before controlled query templates are built; arbitrary PromQL is not exposed. Project CPU/RAM comes from container series rather than host values.

## Deployment and quality

Compose keeps internal services private, adds cAdvisor and an optional DCGM profile, uses health checks and provides a TLS reverse-proxy example. CI installs the package, runs tests/lint/compilation, validates Compose, scans secrets and audits dependencies.

The fake-adapter protocol suite verifies application behavior without models. A release still requires target-environment smoke tests for PostgreSQL migrations, Qdrant, Whisper, Ollama, the selected TTS engine, exporters, browser audio and Android/iOS builds.
