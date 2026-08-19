# Architecture

## Production path

```text
Web / Flutter
    ↓ HTTPS / WSS
services/api (authentication, authorization, sessions, routing)
    ├─ services/stt
    ├─ services/rag → Qdrant
    ├─ services/llm → Ollama/vLLM
    ├─ services/tts
    ├─ services/monitoring → Prometheus
    └─ PostgreSQL persistence and audit
```

The API is the only public backend. `services/api` is canonical. The older `jarvis/` package is retained as a small deterministic compatibility harness and is excluded from production Docker entrypoints.

## Identity and persistence

PostgreSQL stores users, tenants, tenant memberships, projects, project memberships, hashed refresh-token state, conversations/messages, documents/versions, ingestion jobs and audit events. Authorization is derived from current database membership, never client-provided tenant identity or prompt content.

## Agent routing

The server selects one of four constrained modes: personal, coding, RAG or monitoring. Only named monitoring functions can run, with validated project IDs and time windows. Arbitrary PromQL, shell commands and infrastructure writes are not exposed to the model. System and mode prompts are composed in `services/llm` for both chat and voice.

## Voice

The client authenticates in the first WebSocket frame. Audio is bounded in memory, normalized to mono 16 kHz PCM and discarded after transcription. Structured partial/final transcript, citation, token, audio, cancellation and final events carry request/session IDs. Starting a new turn cancels the active response.

## RAG

Ingestion resolves paths inside configured roots, rejects traversal/symlink escape and ignores secrets/generated content. A content hash skips unchanged documents; changed documents replace stale vectors. Embeddings run in batches outside the event loop. Every point includes tenant, owner, project, file, line and version metadata. Retrieval applies all authorization filters before returning cited context.

## Monitoring

Prometheus scrapes host, container, optional GPU and application metrics. The application records request counts plus STT, retrieval, LLM first-token and TTS first-audio latency. Project CPU/RAM is derived from controlled container labels instead of host metrics.

## Deployment

The maintained topology is `infra/docker-compose.yml`. Internal services use private networking; the API is public and Grafana is restricted. `infra/nginx/jarvis.conf.example` shows TLS/WSS termination. CI validates Python 3.11/3.12, tests, lint, compilation, Compose, secrets and dependencies.

Live model, database, vector-store, Docker/GPU and mobile-platform smoke tests remain release gates because those runtimes are not available in every development environment.
