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
| Agent routing | Four deterministic modes with monitoring tools, now also reachable as a durable, replayable `AgentRun` (`services/manager/`) alongside the original /chat path | Replace gradually with a real Manager plan plus policy-checked specialist dispatch; unify /chat and voice onto the same run pipeline |
| Models | Provider registry with Ollama, Claude, ChatGPT, Gemini and OpenRouter adapters; mode-based default routing with an explicit per-request override; privacy-boundary enforcement (RAG/monitoring stay local unless explicitly allowed) | Add live health checks beyond "configured", cost/latency metadata and budget-aware routing (still open) |
| Tools | Deny-by-default gateway (`services/tools/`) for files (list/read/search/write), Docker (read-only `ps`/`logs` plus allowlisted `start`/`stop`/`restart`), one allowlisted local command executor, allowlisted GitHub (read issues, open issue, comment), allowlisted-recipient email, and allowlisted-host SSH (same command set as local exec) — approval-gated for every write/exec, real audit trail; reachable both directly (`/tools/invoke`) and autonomously by the model inside a run (all five providers support tool-calling: Ollama/Claude/OpenAI/OpenRouter/Gemini) | `docker run`/`docker exec` (arbitrary code in a container), GitHub PR creation, worker isolation for command execution, live health/cost-aware routing |
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
  **Partially done**: `agent_runs` and `run_events` exist (`packages/db.py`). `agents` (a registry of
  distinct specialist agents), `agent_steps` (as a table distinct from run_events), `tasks` and
  `model_endpoints` (the multi-model router already tracks provider metadata in-process — see Phase
  3 — so a separate persisted table wasn't added) do not exist yet.
- Implement the Manager state machine: receive, classify, plan, authorize, dispatch, synthesize.
  **Partially done**: `services/manager/execute_run` does receive, classify (via the existing
  `route_agent`), authorize (scope check before dispatch), dispatch (retrieval/monitoring tool/model
  call, plus a bounded model↔tool-calling loop for personal/coding runs — see Phase 4) and synthesize,
  all recorded as run events. There is no real "plan" step yet — a run is still one
  classify-then-dispatch decision (with an in-loop sequence of tool calls the model itself chooses),
  not a multi-step plan a model produces and a human or policy engine can revise before dispatch.
- Implement specialist interfaces and bounded handoffs with deadlines and cancellation. **Not done.**
  There is one Manager path, no distinct specialist agents to hand off to, and no per-run
  deadline/cancellation — a run cannot currently be cancelled once `POST /runs` is called (unlike
  voice, which already supports barge-in cancellation on its own separate path). The tool-calling loop
  does have its own bound (`MAX_TOOL_ITERATIONS = 6`) so a confused model cannot loop forever.
- Add authenticated SSE replay using monotonic event sequence numbers. **Done**:
  `GET /runs/{id}/events` replays `RunEvent` rows in strict sequence order, `?after=N` resumes.

Exit: restart-safe runs with deterministic tests and complete event history.
**Status**: runs and their full event history survive a process restart (they are ordinary
PostgreSQL/SQLite rows), and `tests/test_manager_runs.py` covers the classify/retrieve/tool/model
event sequence, replay/resume via `?after=`, ownership, and the failure path. What "restart-safe"
does **not** yet mean here: a run that is still executing when the process restarts is not resumed
or requeued, because execution is synchronous within one request (same as /chat) — there is no
in-flight state to resume. That needs a durable job queue (Phase 6 territory) and is open work.

### Phase 3 — Multi-model router

- Add OpenAI-compatible, Gemini and local Ollama/vLLM adapters. **Done**: `services/llm/providers.py`
  has Ollama, Anthropic, OpenAI, Gemini and OpenRouter (open-weight gateway) adapters.
- Route using required capabilities, privacy class, health, context size, latency and configured
  budget. Do not silently fall back across privacy boundaries. **Partially done**: `services/llm/router.py`
  routes by mode and privacy class (local vs cloud) with an explicit per-request override, and
  refuses a cloud provider for RAG/monitoring unless allowed. Capability/context-size/latency/budget
  aware routing is not implemented yet — the mode preference lists are static.
- Record selected provider/model and routing reason without storing hidden reasoning. **Done**:
  `ChatResponse.provider/model/routing_reason` and the voice `model` event.

Exit: provider contract tests, health checks, fallback tests and budget enforcement pass.
**Status**: provider contract tests and routing/privacy-boundary tests pass
(`tests/test_llm_providers.py`, `tests/test_model_router.py`). Cloud providers only self-report as
"configured" (have a key); they are not live health-pinged before every request, since that would
add latency and cost to a chat turn. Fallback-on-failure between same-privacy-class providers and
budget enforcement are not implemented — exit criteria not yet fully met.

### Phase 4 — Controlled tool gateway

- Add registry and policy engine, then read-only files/GitHub/search/server tools. **Done** for
  files: `services/tools/registry.py` + `services/tools/gateway.py`. GitHub/search/server (monitoring
  already existed separately) are not part of this registry yet.
- Add approval records and write-capable GitHub/email/Docker/SSH adapters. **Done, narrowly**:
  `ToolInvocation` rows (`packages/db.py`) plus `/tools/invoke`, `/tools/{id}/approve`,
  `/tools/{id}/deny`, `/tools/pending` implement real approval records for every write/exec tool,
  these four included. Docker: read-only `docker_ps`/`docker_logs` plus `docker_start`/`stop`/
  `restart` gated by `DOCKER_ALLOWED_CONTAINERS` — no `docker run`/`docker exec` (arbitrary code in a
  container is a bigger decision than a same-shape addition). GitHub: `github_list_issues`/
  `github_create_issue`/`github_comment_issue` gated by `GITHUB_ALLOWED_REPOS` (checked for reads
  too) and `GITHUB_TOKEN` — no PR creation yet. Email: `send_email` gated by
  `EMAIL_ALLOWED_RECIPIENTS` (exact address or `*@domain`) via stdlib `smtplib`. SSH: `ssh_run` gated
  by `SSH_ALLOWED_HOSTS`, restricted to the same command set as local `run_command`, with every
  argument `shlex.quote()`-ed before being joined into the one command string `ssh` sends the remote
  shell (SSH re-interprets trailing arguments remotely even though no local shell is involved, so
  this is load-bearing, not decorative). All four ran in-process, not an isolated worker — that gap
  is still open, same as local `run_command` below.
- Run command actions through allowlisted structured operations in an isolated worker. **Partially
  done**: `run_command` takes a fixed binary name plus a structured argument list (never a shell
  string) restricted to `services/tools/registry.py`'s `ALLOWED_COMMANDS`, executed via
  `asyncio.create_subprocess_exec` with a timeout and bounded output. It runs in-process, not in a
  separate isolated worker/container yet.

Exit: every action has verified executor output, audit evidence, timeouts and cancellation.
**Status**: file, command, Docker, GitHub, email and SSH tools all return real executor output (no
tool claims success from model text), every transition writes an `AuditEvent`, and subprocess/SSH
calls have a timeout. Cancellation of an in-flight tool call and worker isolation are not
implemented yet — exit criteria not fully met. `tests/test_tool_gateway.py` and
`tests/test_tools_api.py` cover the policy gate, path-safety boundaries, every allowlist (container/
repo/recipient/host), and the pending→approve/deny lifecycle end to end — all against mocked
subprocess/HTTP/SMTP calls; none of Docker, GitHub, SMTP or SSH have been exercised against the real
thing yet (README's verification boundary).

**Addendum — model-invoked tools (built alongside Phase 2)**: the gateway is no longer only reachable
by a human calling `/tools/invoke` directly. `services/manager`'s tool-calling loop lets the model
itself call a tool mid-run through the identical policy path — a "read" tool still executes
immediately and a "write"/"exec" tool still becomes a pending `ToolInvocation` needing the same human
approval, just proposed by the model instead of a person. This does not change the security
invariants above; it changes who can *propose* a call, never who can approve a write. Covered by
`tests/test_manager_runs.py`'s read-auto-execute, write-pauses-for-approval and
tool-iteration-limit cases, and by `tests/test_llm_tool_calling.py` for each provider's wire format
(against mocked HTTP responses — not yet validated against a live provider API, per the README's
verification boundary).

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
