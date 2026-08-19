# Security model

- Access tokens are short-lived and signed; refresh tokens are fingerprinted, persisted,
  rotated once, and rejected on replay.
- Passwords use Argon2id with explicit memory, iteration, parallelism, salt, and hash settings.
- WebSocket authentication uses a negotiated subprotocol. Avoid query-string tokens because
  URLs are commonly logged.
- Scopes and project membership are checked server-side.
- Production startup rejects short/default secrets, wildcard CORS, and HTTP origins.
- RAG roots are allowlisted. Resolved paths prevent `..` and symlink escape. Environment,
  credential, private-key, model, cache, and build paths are excluded.
- Monitoring is read-only and parameterized. No arbitrary PromQL or command execution exists.
- Raw audio is processed in memory and is not persisted by default.

Run secret and dependency scans in CI. Never commit `.env`, credentials, tokens, private
keys, model files, client data, or personal audio.
