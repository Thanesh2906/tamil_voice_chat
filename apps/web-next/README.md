# Jarvis Next.js client

This is the migration target for the production web client. The existing `apps/web` client remains
available until voice, authentication and accessibility parity is verified.

```bash
npm install
npm run dev
```

Set `NEXT_PUBLIC_JARVIS_API_URL` to the externally reachable FastAPI origin. Authentication tokens
are expected in session storage during this first UI phase; the authentication route and secure
session-cookie BFF are part of the next phase. The AI Office panel never creates sample activity: it
shows live API data or an explicit offline/empty state.
