# Operations

## Production checklist

- Terminate TLS at a reverse proxy and expose only the API.
- Generate `JWT_SECRET` with at least 32 random characters and use a secret manager.
- Set a unique PostgreSQL password; never use values from `.env.example`.
- Keep PostgreSQL, Qdrant, Ollama, Prometheus and worker ports private.
- Back up PostgreSQL with `pg_dump --format=custom` and Qdrant with its snapshot API.
- Test restores on a separate private environment before relying on backups.
- Run the optional GPU exporter with `docker compose --profile gpu up -d` only on NVIDIA hosts.
- Restrict Grafana to administrators or a private network.

## Restore outline

1. Stop API writers.
2. Restore PostgreSQL into an empty database with `pg_restore`.
3. Restore the matching Qdrant collection snapshot.
4. Start private services, run readiness checks, then start the API.
5. Verify tenant/project isolation with a read-only smoke test.

Commands here are intentionally examples; choose explicit backup paths and retention policies for the deployment.
## Health and metrics

- `/health/live` confirms the process is alive.
- `/health/ready` checks the local persistence connection.
- `/metrics` exposes API and voice metrics for Prometheus.

Track request/error latency, STT final latency, LLM first-token latency, and TTS first-audio
latency in real adapters. An independently secured cAdvisor/runtime endpoint can supply
container values; node-exporter supplies host values. This Compose file does not grant
cAdvisor privileged host mounts. Never label host metrics as project/container metrics.

## Backup and restore

For the development SQLite store, stop writes and copy `data/jarvis.db` to encrypted backup
storage. Restore to a new path, validate with `PRAGMA integrity_check`, then update
`JARVIS_DATABASE_PATH`. For production PostgreSQL use version-matched `pg_dump`/`pg_restore`,
encrypt backups, test restores regularly, and document retention and recovery objectives.

Qdrant/vector data can be regenerated from approved source documents, but preserve document
version metadata and source backups. Do not back up raw audio because it is not stored.

## Remaining production integrations

1. PostgreSQL driver/migrations for multi-instance deployment.
2. Streaming Whisper worker with resampling and bounded partial-transcript strategy.
3. Ollama/vLLM adapter with timeout, cancellation, and token metrics.
4. Piper Tamil voice validation and phrase-level streamed synthesis.
5. Qdrant plus optional keyword/hybrid retrieval and reranker interfaces.
6. Full platform-generated Flutter Android/iOS directories and release signing configuration.
