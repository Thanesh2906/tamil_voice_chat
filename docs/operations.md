# Operations

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
