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

## Multi-model router

`services/llm/router.py` picks a provider/model per request instead of always calling Ollama. Ollama is always registered (private, on-host, no credential needed); Claude (Anthropic), ChatGPT (OpenAI), Gemini and OpenRouter (a gateway to large open-weight models such as Llama 3.1 405B or Qwen2.5-72B that are impractical to self-host) register only when their API key is configured. RAG and monitoring modes stay local-only by default — project documents and infrastructure data never leave the host — unless `LLM_ALLOW_CLOUD` or a per-request override explicitly allows it; this boundary is enforced in the router, not just documented. A caller may pin an exact `provider`/`model` (chat request field, or the voice `start` event); otherwise the router applies a per-mode default preference — for "personal"/"coding" that means the strongest configured cloud model once `LLM_ALLOW_CLOUD=true`, falling back to Ollama automatically when no cloud provider is configured or cloud is disabled. The resolved provider/model/reason is returned on `ChatResponse` and emitted as a `model` voice event before the first token, and `/models` exposes non-secret provider metadata for a client-side picker.

## Durable agent runs

`services/manager/` turns one turn of /chat's logic into a persisted, replayable record instead of only a final answer. `POST /runs` classifies the message (reusing the same `route_agent` as /chat), retrieves/calls a monitoring tool as needed, calls the model router, and persists every stage as an ordered `RunEvent` (`run.started`, `run.classified`, `retrieval.completed`, `tool.completed`, `model.selected`, `run.completed`/`run.failed`) under a monotonically increasing per-run sequence number. `GET /runs`, `GET /runs/{id}` and `GET /runs/{id}/events` (authenticated SSE, `?after=N` to resume from a sequence number) let a client fetch or replay that timeline.

Execution is synchronous, the same as /chat: a run is fully computed before the `POST /runs` response returns, so the SSE endpoint replays a finished run's history rather than streaming one still in progress. Making a run resumable across a process restart, or pushing events while a run executes, needs a durable job queue and is not built yet — that gap is intentional and recorded in docs/master-build-audit.md. `/chat` and the voice WebSocket are not yet routed through the manager; they remain their own, separately-tested paths.

### Tool-calling inside a run

For "personal"/"coding" modes, a run offers the model every tool in the gateway (`services/tools/registry.AGENT_OFFERED_TOOLS`) and lets it call one mid-answer — this is what lets the assistant read a file or check `git status` on its own, not only through a human calling `/tools/invoke`. `ProviderAdapter.complete()` (`services/llm/providers.py`) is the non-streaming, tool-calling-capable counterpart to `stream()`; `ServiceAdapters.llm_with_tools` → `services/llm/complete_with_tools` resolve a provider/model exactly like the plain chat path, then loop with the model up to `MAX_TOOL_ITERATIONS` (6) turns. A `read` tool call executes immediately, its result is fed back to the model, and the loop continues. A `write`/`exec` call is never auto-executed: it becomes the same kind of pending `ToolInvocation` the gateway already uses (now with `run_id` set for traceability), the run's status becomes `awaiting_approval`, and the loop stops. Approving it via `POST /tools/{id}/approve` runs it — but does not resume the run's conversation with the result yet; that needs the same durable-job-queue work noted above.

All five providers implement real tool-calling (`ProviderAdapter.SUPPORTS_TOOLS`); each converts the manager's provider-agnostic `{"role": "assistant", "tool_calls": [...]}` / `{"role": "tool", ...}` history into its own wire format — Anthropic has no "tool" role (a result becomes a user-role message with a `tool_result` block), and Gemini has three roles (`user`/`model`/`function`, the last for a tool result). Wire-format correctness is covered by `tests/test_llm_tool_calling.py` against mocked HTTP responses for each provider; per the project's existing verification boundary (see README), this has not been validated against a live provider API yet.

## Tool gateway

`services/tools/` is a deny-by-default gateway for giving Jarvis controlled access to this machine (docs/master-build-audit.md, Phase 4). `registry.py` is the only list of tools that exist, each with a pydantic argument schema and a risk level (`read`, `write`, `exec`); an unlisted tool name or malformed arguments are rejected before anything runs (`services/tools/gateway.py`). `adapters.py` does the real work — list/read/search files, `git status`/`git diff`, `docker ps`/`docker logs`, write a file, or run one allowlisted dev command (`git`, `pytest`, `npm`, `python`, `ruff`, `flutter`, ...) with a structured argument list, never a shell string. File tools reuse `packages/common/safe_paths.py` (shared with RAG) so a path must resolve inside a configured root and not match a sensitive pattern.

Docker visibility (`docker_ps`/`docker_logs`) is read-only and narrow: hardcoded subcommands, never an arbitrary docker subcommand the model supplies. These tools also need the `docker` CLI reachable from wherever `services/api` actually runs: that's true by default for a host/dev run, but the API's own container in `infra/docker-compose.yml` has no Docker socket mounted, so they will fail with "command not found" there unless you deliberately mount `/var/run/docker.sock` into it — a real privilege-escalation decision (equivalent to root on the Docker host) left to you, not made silently by this change.

`read` tools execute immediately through `POST /tools/invoke`. `write`/`exec` tools are persisted as a pending `ToolInvocation` and return without running; `POST /tools/{id}/approve` re-validates and executes the *exact* normalized arguments that were stored (never a fresh re-parse of model text), and `POST /tools/{id}/deny` discards it. Every transition (`requested`/`approved`/`denied`/`completed`/`failed`) writes an `AuditEvent`, and `/api/v1/office/snapshot`'s `approvals` list is now backed by real pending invocations instead of a stub.

### Higher-privilege adapters: Docker actions, GitHub, email, SSH

Four adapters reach further than "this machine's filesystem": `docker_start`/`docker_stop`/`docker_restart` (exec risk), `github_list_issues`/`github_create_issue`/`github_comment_issue` (read/write), `send_email` (write), and `ssh_run` (exec). Each is bounded by its own allowlist in `packages/common/config.py`, empty by default:

- `DOCKER_ALLOWED_CONTAINERS` — exact container names/IDs; no `docker run`/`docker exec` exist as tools at all yet (arbitrary code execution inside a container needs more design than a same-shape addition here).
- `GITHUB_ALLOWED_REPOS` (plus `GITHUB_TOKEN`) — the allowlist is checked for the read tool too, not just writes: an unlisted repo is unreachable regardless of risk tier.
- `EMAIL_ALLOWED_RECIPIENTS` (plus `SMTP_*`) — an exact address or a `*@domain` wildcard; sent via stdlib `smtplib` off the event loop (`asyncio.to_thread`), no new dependency.
- `SSH_ALLOWED_HOSTS` — the command itself is restricted to the same `ALLOWED_COMMANDS` set as local `run_command`, so remote execution never gets a broader surface than local execution already has. Every argument is `shlex.quote()`-ed before being joined into the single command string `ssh` hands to the remote shell — unlike local execution, SSH inherently re-interprets its trailing arguments through the remote `$SHELL -c`, so without this an argument containing `;` would be remote-shell-injectable even though no local shell is ever involved. Requires the host to already be trusted (`StrictHostKeyChecking=yes`; this tool will not auto-accept an unknown host key on first connect).

An allowlist bounds *what* can ever be proposed; it does not weaken the approval gate above — a write/exec on an allowlisted repo/host/container/recipient still becomes a pending `ToolInvocation` and still needs your approval. Wire-format and policy correctness for all four are covered by `tests/test_tool_gateway.py` against mocked subprocess/HTTP/SMTP calls; none of the four have been exercised against a real Docker daemon, GitHub API, SMTP server, or SSH host yet, per the project's verification boundary (README).

## Voice

The client authenticates in the first WebSocket frame. Audio is bounded in memory, normalized to mono 16 kHz PCM and discarded after transcription. Structured partial/final transcript, citation, token, audio, cancellation and final events carry request/session IDs. Starting a new turn cancels the active response.

## RAG

Ingestion resolves paths inside configured roots, rejects traversal/symlink escape and ignores secrets/generated content. A content hash skips unchanged documents; changed documents replace stale vectors. Embeddings run in batches outside the event loop. Every point includes tenant, owner, project, file, line and version metadata. Retrieval applies all authorization filters before returning cited context.

## Monitoring

Prometheus scrapes host, container, optional GPU and application metrics. The application records request counts plus STT, retrieval, LLM first-token and TTS first-audio latency. Project CPU/RAM is derived from controlled container labels instead of host metrics.

## Deployment

The maintained topology is `infra/docker-compose.yml`. Internal services use private networking; the API is public and Grafana is restricted. `infra/nginx/jarvis.conf.example` shows TLS/WSS termination. CI validates Python 3.11/3.12, tests, lint, compilation, Compose, secrets and dependencies.

Live model, database, vector-store, Docker/GPU and mobile-platform smoke tests remain release gates because those runtimes are not available in every development environment.
