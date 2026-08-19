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
