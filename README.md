# JARVIS Tamil Voice Assistant

Private multi-user Tamil/Tanglish voice assistant for personal use, project RAG, coding and read-only monitoring.

## Implemented architecture

- FastAPI gateway with Argon2id login, short-lived JWT access tokens, persistent one-time refresh rotation and server-side scopes.
- PostgreSQL schema and migration for users, tenants, memberships, projects, conversations, documents, ingestion jobs and audit events.
- Authenticated voice WebSocket using a first-frame token (credentials are not placed in the URL).
- Bounded PCM capture, partial/final STT, cancellation/barge-in, one prompted LLM adapter, phrase-chunked Tamil TTS and structured events.
- Authorized RAG retrieval with file/line citations and tenant/owner/project filters.
- RAG ingestion root allowlists, traversal/symlink checks, secret exclusions, versioned chunk IDs, stale-vector deletion and off-loop batch embeddings.
- Read-only, parameterized Prometheus summaries; node exporter, cAdvisor and optional DCGM exporter; service `/metrics` endpoints.
- Browser login, AudioWorklet capture, reconnect backoff, project selection, citations and streamed audio playback.
- Flutter source with login, secure refresh-token storage, microphone permission, configurable backend and audio playback. Platform projects must be regenerated/validated with Flutter before release.

The reviewed Word blueprint is stored at `docs/Jarvis_AI_Architecture_Blueprint.docx`. Current implementation details are in `docs/architecture.md`; production operations are in `docs/operations.md`.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
ruff check .
```

For a local API without Compose, set `DATABASE_URL=sqlite:///./jarvis-dev.db`, a 32+ character `JWT_SECRET`, and explicit `BOOTSTRAP_ADMIN_EMAIL` / `BOOTSTRAP_ADMIN_PASSWORD`. Bootstrap variables are development-only and rejected in production.

## Docker Compose

Create `.env` from `.env.example`, replace every development value, then:

```bash
docker compose -f infra/docker-compose.yml config
docker compose -f infra/docker-compose.yml up -d
```

Only the API is intended to be public. Grafana is bound to localhost; databases, Qdrant, Ollama, Prometheus and worker ports stay private. Add `--profile gpu` only on a compatible NVIDIA host.

## Voice protocol

1. `POST /auth/login` over HTTPS.
2. Open `wss://host/voice/session` without query credentials.
3. First JSON frame: `{"type":"auth","access_token":"..."}`.
4. Send `start` with request/session IDs, sample rate, language and optional authorized project ID.
5. Send mono signed PCM16 binary frames, then `{"type":"stop"}`.
6. Receive `partial`, `transcript`, `citation`, `token`, `audio`, `final` or `error` events.
7. Send `barge_in` or a new `start` to cancel the current answer.

Raw audio is not persisted by default.

## Verification boundaries

Unit/protocol tests use deterministic fake STT/LLM/TTS adapters and SQLite. Real Whisper, Ollama, Piper/Edge TTS, Qdrant, PostgreSQL, Docker networking, GPU exporters and mobile platform builds require their corresponding runtimes and models. Do not claim those integrations are healthy until deployment smoke tests pass in the target environment.

## Privacy

Never commit client data, family data, raw audio, `.env` files, tokens, private keys, model caches or database/vector-store contents.
JARVIS is a private, multi-user Tamil/Tanglish assistant for voice chat, authorized
project knowledge, personal/family use, and read-only server monitoring. This repository
contains a secure, testable foundation built from the August 2026 architecture review.

## Implemented now

- FastAPI gateway with health endpoints and Prometheus metrics.
- Persistent tenants, users, projects, memberships, refresh tokens, conversations,
  documents, ingestion jobs, and audit events in SQLite (the schema is PostgreSQL-ready).
- Scrypt password hashing, short-lived signed access tokens, one-time refresh rotation,
  scope checks, and server-side project membership checks.
- Authenticated WebSocket voice protocol using a subprotocol token rather than a query
  string: `start -> binary PCM16 -> stop -> transcript -> token/citation/audio/final`.
- Cancellation/barge-in, PCM validation, size bounds, explicit WAV media type, and fake
  deterministic STT/LLM/TTS adapters for local development and tests.
- A single system-prompted LLM path for both chat and voice.
- Tenant/project-filtered retrieval adapter, citations, safe-root resolution, traversal
  and symlink-escape rejection, and sensitive-file exclusions.
- Safe parameterized monitoring functions; no arbitrary PromQL input.
- Browser client with login, project selection, microphone capture, WSS-compatible URL,
  reconnect, cancellation, transcript, answer, citations, and audio playback.
- Docker Compose development topology, a private production override, safe exporter profiles,
  Grafana provisioning, CI, secret scanning, and dependency scanning. cAdvisor is intentionally
  not granted privileged host mounts by this repository; connect an independently secured
  runtime metrics endpoint when per-container metrics are required.

## Important integration status

The default adapters are deterministic fakes so the complete protocol is runnable without
downloading large models. Production STT (Whisper), LLM (Ollama/vLLM), TTS (Piper), Qdrant,
and PostgreSQL adapters remain explicit deployment integrations. The project does not claim
those external engines or a GPU were verified in this environment.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
uvicorn jarvis.app:app --reload
pytest
```

Register a user with `POST /auth/register`, sign in with `POST /auth/login`, then pass the
access token as `Authorization: Bearer ...`. The browser client uses the WebSocket
subprotocols `jarvis-bearer,<access-token>`; never put a long-lived token in the URL.

## Production rules

- Set `JARVIS_ENV=production` and a random JWT secret of at least 32 characters.
- Use explicit HTTPS CORS origins. Unsafe defaults cause startup to fail.
- Terminate TLS at a reverse proxy and expose only the API; use `docker-compose.prod.yml`
  to keep databases, model runtimes, exporters, and dashboards private.
- Replace SQLite with PostgreSQL before multi-instance deployment.
- Do not store raw audio by default. Keep logs and audit detail redacted.
- Restrict `JARVIS_ALLOWED_RAG_ROOTS` to approved project/document roots.

## Voice events

Client events: `start`, binary PCM16 frames, `stop`, `cancel`/`barge_in`.

Server events: `ready`, `transcript`, `token`, `citation`, `audio`, `final`, `cancelled`,
and `error`. Each turn has a `request_id`; audio is mono PCM16 input and explicit WAV output.

See [docs/architecture.md](docs/architecture.md), [docs/security.md](docs/security.md), and
[docs/operations.md](docs/operations.md) for boundaries, remaining integrations, backup,
restore, and deployment guidance.
