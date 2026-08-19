# JARVIS Tamil Voice Assistant

Private multi-user Tamil/Tanglish assistant for voice conversation, coding, authorized project knowledge and read-only monitoring.

## Canonical architecture

`services/api` is the production API. It coordinates PostgreSQL identity and persistence, STT, RAG/Qdrant, the prompted LLM runtime, TTS and monitoring services. `jarvis/` remains a lightweight compatibility and deterministic test harness; it is not the production deployment entrypoint.

Implemented capabilities include:

- registration, Argon2id login, short-lived JWTs and persistent one-time refresh rotation;
- tenant/project membership and scope enforcement;
- persistent conversations, messages, documents, versions, ingestion jobs and audit events;
- authenticated voice sessions with bounded PCM, resampling, partial/final transcripts, cancellation, citations, token streaming and phrase-chunked WAV audio;
- constrained personal/coding/RAG/monitoring routing with named read-only tools;
- authorized RAG with safe roots, secret exclusion, content-version skipping, stale-vector deletion and line citations;
- host/container/GPU monitoring with validated inputs and request/STT/RAG/LLM/TTS latency metrics;
- web and Flutter client sources plus private-network Compose, TLS example, Grafana, CI, secret scanning and dependency auditing.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
```

Model dependencies are optional:

```bash
python -m pip install -e '.[models,dev]'
```

For local API testing without Compose, set `DATABASE_URL=sqlite:///./jarvis-dev.db`, a 32+ character `JWT_SECRET`, and optional development-only bootstrap credentials.

## Docker

Copy `.env.example` to `.env`, replace all development values, then run:

```bash
docker compose -f infra/docker-compose.yml config
docker compose -f infra/docker-compose.yml up -d
```

Only the API should be externally reachable. PostgreSQL, Qdrant, Ollama, workers and Prometheus stay private. Grafana binds to localhost. Use `--profile gpu` only on a compatible NVIDIA host.

## Voice protocol

1. Register or log in over HTTPS.
2. Open `/voice/session` over WSS without query credentials.
3. Send `{"type":"auth","access_token":"..."}` as the first frame.
4. Send `start`, binary mono PCM16 frames and `stop`.
5. Receive `partial`, `transcript`, `citation`, `token`, `audio`, `final` or `error`.
6. Send `barge_in` or a new `start` to cancel the current response.

Raw audio is not persisted by default.

## Verification boundary

The automated suite uses SQLite and deterministic backend adapters. Real PostgreSQL, Qdrant, Whisper, Ollama, Piper/Edge, Docker networking, GPU exporters and Android/iOS builds must still pass deployment smoke tests in the target environment before a production release.

See [architecture](docs/architecture.md), [operations](docs/operations.md) and [security](docs/security.md).
