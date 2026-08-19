# Jarvis web client (Phase 1)

Vanilla-JS push-to-talk client. No build step.

```bash
# from repo root
python -m http.server 8080 --directory apps/web
```

Then open <http://localhost:8080>. The page opens a WebSocket to
`ws://<host>:8000/voice/session?token=dev` and streams 16-bit PCM at
16kHz from the microphone while the button is held.

## Notes

- The dev token is hard-coded; swap for a real one from `/auth/login`
  before sharing outside your laptop.
- `apps/web` is intentionally minimal so it works without npm. The
  React/web build from `apps/web/src` (when present) can replace it
  in Phase 2 without changing the API surface.
