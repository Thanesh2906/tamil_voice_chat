# Jarvis AI Office web client

A Next.js 16 client for the existing FastAPI services. The application provides:

- A manager workspace and selectable specialist conversations
- Separate conversations by agent, provider, and project; stable backend session IDs for follow-up turns
- Real task activity, persisted run history, and complete tool-approval arguments
- Non-secret provider configuration and server-side setup guidance
- Shared project selection and server-side source indexing
- Tamil/English voice with an explicitly separate conversational boundary
- Responsive navigation, keyboard focus handling, and reduced-motion styles

## Run locally

```bash
npm ci
npm run dev
```

The UI runs at http://localhost:3001. Set `NEXT_PUBLIC_JARVIS_API_URL` to the browser-reachable FastAPI origin before building. Without it, the client uses the current hostname on port 8000. Add the UI origin to the API's `ALLOWED_ORIGINS`.

```bash
npm run lint
npm run typecheck
npm run build
npm start
npm run test:e2e
```

Playwright needs Chromium installed (`npx playwright install --with-deps chromium`), or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to a supported system Chromium executable. The browser suite uses clearly isolated API fixtures, not production user data or live inference. It covers authentication hydration, persona isolation, approvals, provider setup, mobile navigation, stale API recovery, delayed run detail, and chat-session rotation.

## Data and execution boundaries

`OfficeDataProvider` polls authenticated API discovery and office activity. Errors explicitly mark disconnected or stale data; the interface does not generate sample tasks, fake working agents, or pretend a remote computer is attached. Registered personas are not autonomous background workers. Current tool execution is on the API server inside its authorized roots. A tool decision may leave a run paused; approval does not imply automatic conversation resumption.

Chat streams are scoped to their initiating agent/provider/project even when navigation changes. “Stop stream” ends the browser response stream; it is not a claim that server work was cancelled. A new chat rotates the conversation session. Completed runs remain available from server-side run history; in-memory message views reset on page reload.

Provider configuration is managed in the server environment. Provider app subscriptions do not automatically include API credentials or billing. Never put provider keys in `NEXT_PUBLIC_*` variables or browser storage. “Configured” and “verified” are intentionally separate states.

Authentication currently uses session-scoped browser storage and refresh tokens. Returning sessions hydrate deterministically; signing out removes both tokens. A future HTTP-only-cookie/BFF flow would further reduce script-readable-token exposure. Production deployments still need HTTPS, correct CORS, strong server secrets, and the deployment checks documented in `../../infra/SETUP.md`.

Microphone access requires HTTPS or localhost and browser permission. Text and voice use the same project selection, but voice does not execute approved tools. Actual Malaysian Tamil recognition/pronunciation and audio playback require device testing with the chosen STT/TTS services.
