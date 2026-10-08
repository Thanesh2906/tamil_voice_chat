# Secure deployment and provider setup

## Start locally

1. Copy `.env.example` to `.env` at the repository root. Keep it private; it is ignored by Git. Generate independent, strong values for `JWT_SECRET`, `POSTGRES_PASSWORD`, and `GRAFANA_ADMIN_PASSWORD` using your password manager or `python -c 'import secrets; print(secrets.token_urlsafe(48))'`. Do not paste keys into the app or commit them.
2. Run `docker compose --env-file .env -f infra/docker-compose.yml config --quiet`, then `docker compose --env-file .env -f infra/docker-compose.yml up -d`. Use `--quiet`: the rendered configuration can contain credentials.
3. Run the Next.js app on port 3001. The default allowed origins include `http://localhost:3001`; Grafana is on localhost port 3000. List exact HTTPS origins and set `JARVIS_ENV=production` for a TLS-protected deployment.
4. API access binds to `127.0.0.1:8000` by default. Put a TLS reverse proxy in front before remote use. Backing services are not published. Do not expose this development stack publicly without deployment review.

Compose forwards each supported backend provider key and the cloud policy explicitly. It does not copy the entire `.env` into every worker or frontend. Provider configuration requires an API restart/recreate; browser settings cannot install server secrets.

## Provider accounts and truthful status

Consumer ChatGPT, Claude, Gemini or Grok subscriptions are not evidence of API access, credit, model access, or successful integration. Obtain an API credential and any required API billing from the appropriate developer account:

- OpenAI: https://platform.openai.com/api-keys
- Anthropic: https://console.anthropic.com/
- Gemini: https://aistudio.google.com/apikey
- xAI / Grok: https://console.x.ai/
- Groq: https://console.groq.com/keys
- OpenRouter: https://openrouter.ai/settings/keys

Grok is xAI (`XAI_API_KEY`, provider `xai`); Groq is a separate inference service (`GROQ_API_KEY`, provider `groq`). Model IDs in the example environment are configurable examples. Verify the model is offered to your account before selecting it. The xAI adapter follows its [OpenAI-compatible Chat Completions API](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions).

The provider catalog distinguishes `configured` from `verified`. A nonempty key only means configured. Discovery performs no network request, incurs no inference charge, and never exposes keys. `verified=false` / `verification_status=not_checked` remains honest until a future explicit verification workflow establishes model-level support. A health probe checks the API's model-list endpoint rather than pretending a stored key works; that probe still does not establish inference, tool support, latency or Tamil quality.

`LLM_ALLOW_CLOUD=false` is the default. Merely installing an API key does not allow messages, retrieved documents or tool results to leave the private deployment. Existing per-request cloud consent may allow a particular request. RAG and monitoring auto-selection remains Ollama; an explicit cloud request requires consent too. Selecting a provider never bypasses tool permissions or action approvals.

## OpenAI-compatible self-hosting

Set `SELF_HOSTED_BASE_URL` (usually including `/v1`) and `SELF_HOSTED_MODEL` to register `self_hosted`. Use `SELF_HOSTED_API_KEY` only if the endpoint requires authentication. With no key, the adapter sends no Authorization header. vLLM/llama.cpp/tool compatibility depends on the specific server and model; test it rather than assuming feature parity.

Custom endpoints are classified as cloud by default, including private-looking addresses. Only set `SELF_HOSTED_LOCAL=true` after you establish that the endpoint is operator-controlled, private, and does not forward requests to third parties. This is an explicit deployment trust assertion, not DNS validation or a sandbox. Do not mark a hosted vendor endpoint local to circumvent cloud consent. URLs with inline credentials, query strings or fragments are rejected. HTTPS is strongly recommended; plain HTTP should remain inside a trusted isolated network.

`localhost` inside the API container means that container, not your laptop. Use an explicitly connected private service/network and verify reachability. No host-network mode or broad host mount is enabled to make an endpoint work.

## Workspace and unsafe execution boundaries

The default `project_workspace` volume is empty and mounted as `/workspace` on the API, read-only on RAG. Changing `RAG_ALLOWED_ROOTS` alone never grants access to host files. To use a real project, create a private Compose override mounting only its explicitly approved folder at the same container path, read-only where possible. Never mount `/`, a home directory, a Docker socket, or a directory containing credentials. A per-project path restriction and exact approval remain necessary even for permitted files.

`TOOLS_UNSAFE_HOST_EXECUTION=false` disables host-command/SSH execution. These adapters can run arbitrary project code when enabled, including through interpreters/package managers, and are not OS sandboxed. Only enable them in an isolated, administrator-controlled deployment after understanding the environment and network exposure. They still require scoped authorization and approval. No Docker socket, SSH keys or broad host filesystem mounts are included here, so those integrations should be displayed as unavailable until separately configured and tested.

The node exporter describes its own container; it has no host PID namespace/root mount. There is no privileged cAdvisor service in the default stack. Real host/container telemetry requires independently secured collectors. Host collectors must carry an operator-verified `scope="host"` target label; host queries reject missing or different scope labels and report missing samples instead of healthy zeros. Do not label container-local metrics as host verification.

## Piper voice provisioning

`ta-IN-default` is a placeholder, not a bundled or verified Tamil voice. Provision a trusted, appropriately licensed Piper `.onnx` model and its adjacent `.onnx.json` configuration, mount only that model directory read-only into the TTS container, and set `TTS_VOICE` to the actual container-visible ONNX path. No voice model is downloaded automatically by this application. Missing files produce an actionable 503/setup error and a terminal WebSocket error, never pretend speech.

The adapter supports the [current Piper AudioChunk API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md) and the legacy 1.2 raw-stream API. It reads the actual source rate, validates mono PCM16, resamples each chunk to 16 kHz, and emits a matching WAV header. Native load/inference details and private model paths are not included in client errors. Unit tests mock realistic audio chunks; they do not establish a voice's Tamil pronunciation, code switching, Malaysian accent, speed or device playback quality.

## CI and remaining verification

- NumPy is constrained below 2.5 because this project supports Python 3.11 and type checks against that minimum; NumPy 2.5 stubs use Python 3.12 syntax. Do not disable type checking to mask the mismatch.
- `.github/gitleaks.toml` retains default secret rules and exempts only the exact synthetic password assignment in `tests/test_api.py`, with path and line matched together. Other secrets in that file remain scanned. Never add repository-wide exclusions for fixtures.
- Flutter checks include the app's own widget tests. Device audio, release signing, real provider inference and exact model availability remain deployment checks.
- Provider unit tests use mocked HTTP. No live keys, purchases, subscriptions or paid inference are required for CI.
