# Jarvis Personal AI Platform: Repository Audit and Migration Plan

Date: 2026-09-18  
Baseline: `34c7389` (`agent/finish-jarvis-remaining`)  
Canonical backend: `services/api`

## Executive decision

Keep the proven FastAPI voice, identity, RAG, persistence, and monitoring services. Build the
platform around them instead of replacing them. Introduce the Next.js client beside the existing
web client, validate feature parity, and only then retire the old client. The `jarvis/` Python
package remains a deterministic compatibility harness until its remaining tests are migrated.

No tool may claim success from model text. Every tool result must be produced by an execution
adapter, recorded in the audit log, and returned with a stable run and invocation identifier.

## Current repository audit

| Area | Current state | Decision |
|---|---|---|
| API gateway | FastAPI with JWT, refresh rotation, scopes, project membership, rate/body limits | Preserve and version under `/api/v1` incrementally |
| Voice | Authenticated WSS, PCM buffering, STT, streaming tokens, chunked TTS, cancellation | Preserve; add event-envelope compatibility and model selection |
| Languages | Tamil/Tanglish-oriented STT and TTS configuration | Preserve; add explicit `ta`, `en`, and `auto` preferences |
| RAG | Project-scoped Qdrant retrieval, citations, safe roots, hashes and versions | Preserve; add memory collections and retrieval policy |
| Persistence | PostgreSQL users, tenants, projects, conversations, messages, documents and audit | Extend with agents, runs, tasks, approvals, memories and tool invocations |
| Agent routing | Four deterministic modes with monitoring tools | Replace gradually with a Manager plan plus policy-checked specialist dispatch |
| Models | One Ollama-compatible LLM path | Add provider registry, health/cost/capability metadata and routing policy |
| Tools | Four read-only monitoring functions | Add a deny-by-default gateway for files, GitHub, email, search, Docker and SSH |
| Events | Voice WebSocket events only | Add durable run events and authenticated SSE; keep WSS for duplex audio |
| Web client | Static HTML/JavaScript client | Keep during migration; introduce `apps/web-next` now |
| Mobile | Flutter source with auth and voice support | Keep; consume the same versioned event contracts later |
| Observability | Prometheus metrics, Grafana, structured/redacted logs | Extend with run/model/tool labels that have bounded cardinality |
| CI | Python, integration, Docker smoke, security and Flutter jobs | Add Next.js lint/type/build tests and contract tests |

## Important baseline risks

1. The latest completed work exists in local commits `f41b38a` and `34c7389`; it is ahead of the
   currently known `origin/main`. Publication/merge must happen before production deployment.
2. `Base.metadata.create_all()` still runs at API startup. Alembic must become the only production
   schema authority before multiple platform services write to PostgreSQL.
3. The in-process rate limiter cannot coordinate across replicas. Move it to Redis before scaling.
4. Tool execution, approvals, multi-model routing, task queues and durable agent runs do not yet
   exist. Existing deterministic routing must not be presented as a full multi-agent system.
5. Real Whisper/Ollama/TTS/Qdrant/PostgreSQL/Docker/GPU/mobile validation remains a release gate.

## Target boundaries

```text
Next.js / Flutter
  |-- HTTPS: auth, projects, conversations, tasks, approvals
  |-- SSE: run and office events
  `-- WSS: duplex voice
          |
       FastAPI gateway
          |
     Manager / Orchestrator
       |-- model router --> GPT / Gemini / Ollama / vLLM
       |-- specialists --> conversation / coding / research / RAG / DevOps
       |-- tool gateway --> policy --> approval --> sandboxed adapter
       |-- memory -------> working / episodic / semantic / user preference
       `-- event store --> PostgreSQL + Redis stream
```

The gateway is the only public backend. Agents never receive raw credentials. Tool adapters run
with scoped credentials and an explicit workspace/project boundary.

## Security invariants

- Deny tools by default and authorize every invocation by user, tenant, project, scope and risk.
- Reads can be auto-approved only when policy explicitly permits them.
- Writes, sends, pushes, deployments, destructive shell commands and privilege changes require a
  short-lived approval bound to the exact normalized arguments.
- Use argument schemas; never pass model-produced shell strings directly to a shell.
- Resolve file paths and symlinks beneath configured roots before access.
- Keep SSH keys, OAuth tokens and provider keys in a secret manager; never in prompts or events.
- Persist an append-only audit trail for plan, dispatch, approval, execution and result.
- Treat retrieved documents, webpages, email and repository text as untrusted data, not commands.

## Phased implementation

### Phase 0 — Baseline and contracts

- Preserve commit `34c7389` and publish it before deployment.
- Record this audit and architecture decision.
- Define versioned agent, model, event, tool and approval contracts.
- Add contract fixtures and migration tests.

Exit: baseline checks remain green and no existing voice/API behavior regresses.

### Phase 1 — Next.js voice shell and AI Office foundation

- Add `apps/web-next` using Next.js, TypeScript and Tailwind.
- Implement accessible Jarvis orb states: idle, listening, thinking, speaking and error.
- Add Tamil/English transcript, streaming response area, connection/model indicators and controls.
- Add AI Office pages for agent cards, run timeline, tasks and approvals using typed mock-free API
  clients. Empty/unavailable backends must render honestly as offline or no data.
- Add Next.js lint, typecheck and production build to CI.

Exit: production build succeeds and the client never invents agent/tool status.

### Phase 2 — Durable orchestration

- Add `agents`, `agent_runs`, `agent_steps`, `tasks`, `run_events` and `model_endpoints` tables.
- Implement the Manager state machine: receive, classify, plan, authorize, dispatch, synthesize.
- Implement specialist interfaces and bounded handoffs with deadlines and cancellation.
- Add authenticated SSE replay using monotonic event sequence numbers.

Exit: restart-safe runs with deterministic tests and complete event history.

### Phase 3 — Multi-model router

- Add OpenAI-compatible, Gemini and local Ollama/vLLM adapters.
- Route using required capabilities, privacy class, health, context size, latency and configured
  budget. Do not silently fall back across privacy boundaries.
- Record selected provider/model and routing reason without storing hidden reasoning.

Exit: provider contract tests, health checks, fallback tests and budget enforcement pass.

### Phase 4 — Controlled tool gateway

- Add registry and policy engine, then read-only files/GitHub/search/server tools.
- Add approval records and write-capable GitHub/email/Docker/SSH adapters.
- Run command actions through allowlisted structured operations in an isolated worker.

Exit: every action has verified executor output, audit evidence, timeouts and cancellation.

### Phase 5 — Layered memory and RAG

- Separate working context, conversation history, episodic summaries, semantic project knowledge
  and user preferences.
- Add retention, provenance, sensitivity and deletion controls.
- Defend against prompt injection during ingestion and retrieval.

Exit: tenant isolation and memory provenance tests pass.

### Phase 6 — Production operations

- Redis-backed rate limits/queues, Alembic-only migrations, OpenTelemetry traces and SLO alerts.
- Backup/restore drills, key rotation, dependency/image scanning and incident runbooks.
- Real Tamil/Tanglish voice and all model/tool integrations tested on target hardware.

Exit: signed release checklist and rollback rehearsal pass.

## Definition of done

The platform is complete only when a real authenticated user can start a Tamil/English voice or
text task, observe a durable manager run and specialist activity in AI Office, approve controlled
side effects, receive executor-backed results with citations/audit evidence, reconnect and replay
events, and operate the stack under documented security and recovery procedures. A simulated card,
hard-coded success message, or model-authored tool result does not satisfy this definition.
