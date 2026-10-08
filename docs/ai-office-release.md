# AI Office stabilization and redesign

## What changed

The existing FastAPI, PostgreSQL/RAG and Next.js architecture is retained. This is an upgraded foundation, not a claim of production-ready autonomous desktop execution.

- Responsive Next.js office with manager workspace, five selectable specialist personas, separate provider/project conversations, real recorded runs, exact-argument approval review, and server configuration guidance.
- Serial manager routing identifies the specialist actually handling a request. Personas are not independent background workers. Completed conversation history is persisted and separated by user, session, agent, project, provider and model.
- Tool scopes and current project membership are checked in both direct and model-invoked paths. Filesystem tools use assigned project roots; host-wide tools require administrator scope.
- Approval uses an atomic pending-to-executing claim and durable checkpoints. New runs continue synchronously from the actual approved/denied tool result; explicit resume and cancellation are fenced and reauthorized. Uncertain effects are never replayed. Legacy checkpoint-less runs remain paused. See [continuation semantics](run-continuation.md).
- File access rejects symlink escapes, bounds reads, and filters Git results. Process output is bounded; cancellation/timeout cleans process groups. General command/SSH execution is disabled unless explicitly enabled and remains unsandboxed administrator-only execution.
- Provider catalog includes OpenAI, Anthropic/Claude, Gemini, xAI/Grok, Groq, OpenRouter, Ollama and self-hosted OpenAI-compatible servers. Configured means configuration exists, not verified availability. Credentials stay in the API environment. Consumer subscriptions and Claude Code access do not automatically confer provider API access.
- Voice waits for authentication/readiness, refreshes access tokens, honors provider/project selection, plays audio in sequence, cancels capture/playback, and rejects stale session events. Server TTS begins at phrase boundaries while model text is still streaming; bounded queues and cancellation contain failures.
- CI/type compatibility, Flutter PCM capture interfaces, schema migration startup, deployment configuration, secret-scan fixture specificity, and host-metric scope were corrected.

## Safe setup

Follow [deployment setup](../infra/SETUP.md). Copy `.env.example` to an ignored private `.env` and supply credentials there or through your deployment secret manager. Never put provider keys in `NEXT_PUBLIC_*`, browser storage, source control, logs, screenshots or chat. Provider setup in the dashboard is instructional; it deliberately has no insecure credential form.

Back up the database before upgrading. Run `alembic upgrade head` before starting the API; the API container now runs this step. Migrations 0006/0007 add conversation/agent identity and bounded continuation metadata without deleting existing messages or runs; run status is widened for PostgreSQL approval states. Review existing user scopes and project roots: historical versions granted new users tool/monitoring access and permitted arbitrary project root assignment, so existing grants cannot be assumed safe.

Next.js runs at port 3001. API runs at 8000. Only expose the API/frontend behind HTTPS and appropriate access controls. Do not expose general tools, Docker sockets, or an unrestricted command executor publicly. Server filesystem paths refer to the machine hosting the API, never automatically to your laptop.

## Verification boundaries

Default backend unit tests use deterministic adapters and disposable SQLite. Opt-in CI integration tests require real PostgreSQL, migrate a fresh isolated schema, and exercise normal registration/bootstrap, project/conversation writes, real foreign-key failures, rollback and refresh-token rotation without manually seeded users. Provider protocol tests mock HTTP. Browser tests use explicitly synthetic API fixtures and do not establish live model availability. Local cloud-browser launch was blocked by runtime socket restrictions; GitHub Actions is the browser-test execution route, with screenshot/trace artifacts for visual review.

Not established by these changes:

- live provider credentials, quota, inference quality or provider latency;
- Malaysian Tamil pronunciation, English code-switch recognition or microphone/speaker behavior on the target Windows/mobile devices;
- an authenticated laptop bridge or browser/RPA executor; a disabled read-only POSIX adapter/protocol foundation exists, but no machine is paired or activated;
- a durable background job queue, automatic restart scheduler, or parallel autonomous specialists; explicit checkpoint recovery is available only when safe;
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

## Web dependency maintenance

Next.js and its matching ESLint configuration are pinned to 16.3.8. The lockfile
also resolves patched `sharp` 0.35.5 and `source-map-js` 1.2.2 within their
existing compatible ranges. CI rejects high/critical production npm audit
findings; a passing production audit does not establish that development tools
or application logic are free of vulnerabilities.

As of 2026-10-08, the development-only `braces` 3.0.3 dependency still has an
[upstream denial-of-service advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
with no patched release. It is pulled in by `eslint-config-next` →
`@next/eslint-plugin-next` → `fast-glob` → `micromatch`, not shipped in
the production dependency set. Avoid processing untrusted glob patterns in
these tools, keep builds isolated, and recheck for an upstream fix. A full
`npm audit` therefore remains non-clean; do not suppress or misrepresent it.
