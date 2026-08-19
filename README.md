# JARVIS AI

Tamil-speaking personal + coding + RAG + server monitoring assistant.

This repository is an implementation of the architecture described in
`docs/Jarvis_AI_Architecture_Blueprint.md` (v1.1, August 2026). It is
modular: voice, LLM, RAG, authentication, monitoring, and the mobile UI
can be swapped without rewriting the rest of the system.

## Repository layout

```
jarvis-ai/
├── apps/
│   ├── mobile/         Flutter push-to-talk client
│   └── web/            Minimal browser push-to-talk client
├── services/
│   ├── api/            FastAPI gateway + agent orchestrator
│   ├── stt/            faster-whisper streaming transcription
│   ├── llm/            Ollama/vLLM compatible local LLM client
│   ├── tts/            Tamil-capable streaming TTS
│   ├── rag/            Qdrant + PostgreSQL ingestion/retrieval
│   └── monitoring/     Prometheus read-only query adapter
├── packages/
│   ├── auth/           Argon2id + JWT + refresh tokens
│   ├── schemas/        Pydantic models shared across services
│   └── common/         Shared utilities (logging, config, telemetry)
├── infra/
│   ├── docker-compose.yml
│   ├── prometheus/     Prometheus + node_exporter scrape config
│   └── grafana/        Grafana provisioning
├── prompts/            Master + mode prompts (versioned, no secrets)
├── docs/               Architecture blueprint + ops notes
└── tests/              Unit + integration tests
```

## Phase 1 build (laptop prototype)

1. Install Docker and a local LLM runtime (Ollama or vLLM).
2. `docker compose up -d` from `infra/`.
3. Open `apps/web/` for a quick browser test, or run `apps/mobile/`
   on a phone.
4. Speak in Tamil → STT → LLM → TTS → spoken Tamil response.

See `docs/architecture.md` for the full architecture, API surface,
security model, and deployment phases.

## License

Internal project. Treat all client code, family data, conversation
logs, and metrics as private. See section 7 of the blueprint.
