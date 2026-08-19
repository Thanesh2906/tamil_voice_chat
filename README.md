# JARVIS AI

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
