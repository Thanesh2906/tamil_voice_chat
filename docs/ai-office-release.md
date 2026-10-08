# AI Office stabilization and redesign

## What changed

The existing FastAPI, PostgreSQL/RAG and Next.js architecture is retained. This is an upgraded foundation, not a claim of production-ready autonomous desktop execution.

- Responsive Next.js office with manager workspace, five selectable specialist personas, separate provider/project conversations, real recorded runs, exact-argument approval review, and server configuration guidance.
- Serial manager routing identifies the specialist actually handling a request. Personas are not independent background workers. Completed conversation history is persisted and separated by user, session, agent, project, provider and model.
- Tool scopes and current project membership are checked in both direct and model-invoked paths. Filesystem tools use assigned project roots; host-wide tools require administrator scope.
- Approval uses an atomic pending-to-executing claim. Approved runs become paused; starting a new turn is required after reviewing results. Approval does not pretend to resume a suspended conversation.
- File access rejects symlink escapes, bounds reads, and filters Git results. Process output is bounded; cancellation/timeout cleans process groups. General command/SSH execution is disabled unless explicitly enabled and remains unsandboxed administrator-only execution.
- Provider catalog includes OpenAI, Anthropic/Claude, Gemini, xAI/Grok, Groq, OpenRouter, Ollama and self-hosted OpenAI-compatible servers. Configured means configuration exists, not verified availability. Credentials stay in the API environment. Consumer subscriptions and Claude Code access do not automatically confer provider API access.
- Voice waits for authentication/readiness, refreshes access tokens, honors provider/project selection, plays audio in sequence, cancels capture/playback, and rejects stale session events. Server TTS begins at phrase boundaries while model text is still streaming; bounded queues and cancellation contain failures.
- CI/type compatibility, Flutter PCM capture interfaces, schema migration startup, deployment configuration, secret-scan fixture specificity, and host-metric scope were corrected.

## Safe setup

Follow [deployment setup](../infra/SETUP.md). Copy `.env.example` to an ignored private `.env` and supply credentials there or through your deployment secret manager. Never put provider keys in `NEXT_PUBLIC_*`, browser storage, source control, logs, screenshots or chat. Provider setup in the dashboard is instructional; it deliberately has no insecure credential form.

Back up the database before upgrading. Run `alembic upgrade head` before starting the API; the API container now runs this step. Migration 0006 adds nullable conversation linkage and agent identity without deleting existing messages or runs. Review existing user scopes and project roots: historical versions granted new users tool/monitoring access and permitted arbitrary project root assignment, so existing grants cannot be assumed safe.

Next.js runs at port 3001. API runs at 8000. Only expose the API/frontend behind HTTPS and appropriate access controls. Do not expose general tools, Docker sockets, or an unrestricted command executor publicly. Server filesystem paths refer to the machine hosting the API, never automatically to your laptop.

## Verification boundaries

Automated backend tests use deterministic adapters and disposable SQLite. Provider protocol tests mock HTTP. Browser tests use explicitly synthetic API fixtures and do not establish live model availability. Local cloud-browser launch was blocked by runtime socket restrictions; GitHub Actions is the browser-test execution route, with screenshot/trace artifacts for visual review.

Not established by these changes:

- live provider credentials, quota, inference quality or provider latency;
- Malaysian Tamil pronunciation, English code-switch recognition or microphone/speaker behavior on the target Windows/mobile devices;
- an authenticated laptop bridge or browser/RPA executor;
- a durable job queue, crash-resumable agents, automatic approval continuation, or parallel autonomous specialists;
- voice-driven privileged agent runs (voice remains conversational; text runs use the approval gateway);
- production Docker/GPU/model installation, real Piper voice assets, or physical Flutter device builds.

Cloud implementation does not modify a separate Windows checkout. Pull the chosen GitHub branch into an isolated local worktree before local-machine validation. The hardened filesystem/process adapters require POSIX protections; use an explicitly configured WSL/Linux or container executor rather than treating native Windows access as verified.

## Checks

From the repository root:

```sh
python -m pip install '.[dev]'
pytest
ruff check .
mypy packages services
python -m compileall -q jarvis packages services tests
```

From `apps/web-next`:

```sh
npm ci
npm run lint
npm run typecheck
npm run build
npx playwright install --with-deps chromium
npm run test:e2e
```

CI uploads `ai-office-browser-evidence` containing screenshots and failure traces. Backend checks include cross-project/scope denials, duplicate approvals, migration data preservation, conversation isolation, real subprocess output/timeout/cancellation, and streamed phrase TTS failure/cancellation.
