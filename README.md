# JARVIS Tamil Voice Assistant

Private multi-user Tamil/Tanglish assistant evolving into a permission-controlled Personal AI Platform for voice conversation, coding, authorized project knowledge, multi-agent work and operations.

## Canonical architecture

`services/api` is the production API. It coordinates PostgreSQL identity and persistence, STT, RAG/Qdrant, the prompted LLM runtime, TTS and monitoring services. `jarvis/` remains a lightweight compatibility and deterministic test harness; it is not the production deployment entrypoint.

Implemented capabilities include:

- registration, Argon2id login, short-lived JWTs and persistent one-time refresh rotation;
- tenant/project membership and scope enforcement;
- persistent conversations, messages, documents, versions, ingestion jobs and audit events;
- authenticated voice sessions with bounded PCM, resampling, partial/final transcripts, cancellation, citations, token streaming and phrase-chunked WAV audio;
- constrained personal/coding/RAG/monitoring routing with named read-only tools;
- a multi-model router (Ollama local default; Claude, ChatGPT, Gemini, OpenRouter and Groq opt-in)
  that never sends RAG/monitoring content off-host unless explicitly allowed, auto-selects the
  strongest configured model for personal/coding chat once cloud is allowed (personal chat prefers
  Groq first for low-latency live interaction), and reports the chosen provider/model back on every
  chat and voice turn;
- a deny-by-default tool gateway (`/tools/*`) for file access, Docker (read-only plus allowlisted
  start/stop/restart), an allowlisted dev-command executor, allowlisted-repo GitHub issues, and
  allowlisted-recipient email and SSH, with every write/exec held as a pending approval until a
  human approves or denies the exact recorded arguments, and a full audit trail;
- durable agent runs (`/runs/*`): each turn's classify/retrieve/tool/model-selection/result is
  persisted as an ordered, replayable event history instead of only the final chat answer, with
  authenticated SSE replay by sequence number, and a live variant (`POST /runs/stream`) that pushes
  each stage — and, for a plain answer, real per-token text — to the client as it actually happens;
- model-invoked tool use inside a run: for personal/coding turns, Ollama/Claude/OpenAI/OpenRouter can
  call a gateway tool mid-answer (reads execute immediately; writes/commands still pause for your
  approval through the same gateway a human would use);
- authorized RAG with safe roots, secret exclusion, content-version skipping, stale-vector deletion and line citations;
- host/container/GPU monitoring with validated inputs and request/STT/RAG/LLM/TTS latency metrics;
- web and Flutter client sources plus private-network Compose, TLS example, Grafana, CI, secret scanning and dependency auditing.

The production web migration is underway in `apps/web-next`: a Next.js/TypeScript/Tailwind
voice-first interface and honest AI Office foundation. The existing `apps/web` client is retained
until parity is verified. See [the master build audit and migration plan](docs/master-build-audit.md)
for current gaps, security invariants and phased exit criteria.

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

The automated suite uses SQLite and deterministic backend adapters. Real PostgreSQL, Qdrant, Whisper, Ollama, Piper/Edge, Docker networking, GPU exporters and Android/iOS builds must still pass deployment smoke tests in the target environment before a production release. Cloud model providers (Claude, ChatGPT, Gemini, OpenRouter) and their tool-calling wire formats are covered by tests against mocked HTTP responses, not live API calls — add a real key and try a request before relying on a provider in production. The same is true of the Docker/GitHub/email/SSH tool adapters: their tests mock the subprocess/HTTP/SMTP calls, so try each against a real container, repo, mailbox and host once you configure its allowlist, before trusting it unattended.

See [architecture](docs/architecture.md), [operations](docs/operations.md) and [security](docs/security.md).
