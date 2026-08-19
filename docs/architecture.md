# Jarvis architecture notes

These notes accompany the v1.1 blueprint
(`docs/Jarvis_AI_Architecture_Blueprint.md`) and explain how the
reference implementation in this repo maps onto it.

## 1. Voice pipeline (§3)

```
mic PCM/Opus → STT service (faster-whisper) → partials → API → RAG → LLM → TTS → PCM/WAV → speaker
```

The mobile / web clients stream 16-bit PCM at 16kHz over a single
WebSocket. `services/api/voice_session` is the only component that
talks to all four backends in one place.

## 2. RAG pipeline (§4)

- `services/rag.ingest()` parses source code, markdown, PDFs and
  docx, chunks code with line windows and docs by heading, embeds
  locally with a multilingual MiniLM model, and stores vectors in
  Qdrant with `tenant_id`, `owner_id`, `project_id`, and `branch`
  on every payload.
- `services/rag.retrieve()` requires those three IDs and pushes
  them into Qdrant as a hard filter. The agent cannot retrieve
  across users or projects.

## 3. Agent orchestrator (§5)

`/chat` and `/voice/session` are the two entry points. Both:

1. Authenticate (JWT + refresh-token rotation).
2. Decide whether the question is coding, RAG, monitoring, or
   personal.
3. Pull only the minimum authorized context.
4. Stream tokens from `services/llm` (Ollama-compatible).
5. Synthesize audio with `services/tts` for voice replies.

## 4. Monitoring (§6)

`services/monitoring` exposes a read-only Prometheus adapter. The
queries live next to the service so the agent never invents a
metric. Per-project queries replace the `name` filter on the
container metrics so the report separates host-level from
project-level numbers.

## 5. Security (§7)

- Argon2id password hashing (`packages/auth/hashing.py`).
- JWT access tokens + opaque refresh-token rotation
  (`packages/auth/jwt.py`).
- Per-tenant filtering at retrieval time, never in the prompt.
- `services/api` enforces `Authorization: Bearer` on every route
  except `/healthz` and `/auth/*`.
- No raw microphone audio is stored by default.

## 6. Deployment phases (§8)

| Phase | Where the stack runs |
| --- | --- |
| 1 | Laptop: everything via `infra/docker-compose.yml`. |
| 2 | Move model services to a GPU-capable home server; clients connect over HTTPS/WSS. |
| 3 | Family multi-user: add onboarding, quotas, backups, monitoring alerts, mobile accounts. |

## 7. API surface (§9)

Every endpoint listed in the blueprint is implemented in
`services/api`. The WebSocket event types are documented in
`packages/schemas/voice.py`.

## 8. Master prompt (§14 + §15)

`prompts/system.txt` is the master system prompt. Mode-specific
templates live in `prompts/coding.txt`, `prompts/rag.txt`,
`prompts/monitoring.txt`. **Do not put secrets in any of these.**
