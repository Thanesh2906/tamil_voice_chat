"""Centralized settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. Values come from env vars or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jarvis_env: str = Field(default="dev", alias="JARVIS_ENV")
    log_level: str = Field(default="info", alias="JARVIS_LOG_LEVEL")

    # Postgres
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "jarvis"
    postgres_user: str = "jarvis"
    postgres_password: str = "change-me"
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    # Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "jarvis_chunks"

    # LLM (local default; always available, needs no credential)
    llm_base_url: str = "http://llm:11434"
    llm_model: str = "llama3.1:8b-instruct-q5_K_M"

    # Multi-model router (Phase 3). Cloud providers register only when their key is
    # set. RAG/monitoring modes stay local-only unless llm_allow_cloud is explicit,
    # since those modes carry project/document/infrastructure content.
    # No explicit alias needed: pydantic-settings already maps e.g. anthropic_api_key
    # to env var ANTHROPIC_API_KEY by default. An alias here would make the *only*
    # accepted constructor kwarg the alias, which is a silent footgun combined with
    # this model's extra="ignore".
    llm_allow_cloud: bool = False

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.0-flash"

    # OpenRouter is the practical path to large (70B-400B class) open-weight models
    # that are not realistic to self-host on a personal machine.
    openrouter_api_key: str | None = None
    openrouter_model: str = "meta-llama/llama-3.1-405b-instruct"

    # STT
    stt_model: str = "small"
    stt_language: str = "auto"
    stt_compute_type: str = "int8"

    # TTS
    tts_engine: str = "piper"
    tts_voice: str = "ta-IN-default"

    # Monitoring
    prometheus_url: str = "http://prometheus:9090"

    # Internal services (only the API is public)
    stt_url: str = "http://stt:8000"
    tts_url: str = "http://tts:8000"
    rag_url: str = "http://rag:8000"
    monitoring_url: str = "http://monitoring:8000"

    # Auth
    jwt_secret: str = "development-only-secret-change-me-now"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 2_592_000

    # CORS
    allowed_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:8080", "http://localhost:5173"]
    )

    # Audio
    audio_sample_rate: int = 16_000
    vad_aggressiveness: int = 2
    max_audio_bytes: int = 16_000 * 2 * 60
    request_timeout_seconds: float = 60.0
    api_rate_limit_per_minute: int = 120
    max_request_body_bytes: int = 10 * 1024 * 1024

    # RAG safety
    rag_allowed_roots: List[str] = Field(default_factory=lambda: [str(Path.cwd())])
    rag_sensitive_globs: List[str] = Field(
        default_factory=lambda: [
            ".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*credentials*",
            "*secret*", "id_rsa*", "id_ed25519*",
        ]
    )

    # Development bootstrap is explicit and forbidden in production.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    # ---- High-privilege tool adapters (services/tools) -----------------------
    # Every allowlist below defaults to empty: deny by default, nothing is
    # reachable until you explicitly name it. Writes/exec still go through the
    # same approval gate (ToolInvocation) as everything else in the gateway --
    # these allowlists bound *what* can ever be proposed, not whether a human
    # still has to approve it.

    # Docker write actions (start/stop/restart) -- container names/IDs. Exec-into-
    # a-container and `docker run` are intentionally not exposed at all yet.
    docker_allowed_containers: List[str] = Field(default_factory=list)

    # GitHub: "owner/repo" entries Jarvis may read issues/PRs on or, for the
    # write tools, open an issue/comment on. A token with the narrowest scope
    # that covers the repos you list is strongly recommended over a full-access PAT.
    github_token: str | None = None
    github_allowed_repos: List[str] = Field(default_factory=list)

    # Outbound email via SMTP. Recipients: an exact address ("me@example.com")
    # or a whole-domain wildcard ("*@example.com"); nothing else is reachable.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    email_allowed_recipients: List[str] = Field(default_factory=list)

    # SSH: "user@host" or "host" entries. Commands are restricted to the same
    # ALLOWED_COMMANDS set as the local run_command tool (services/tools/registry.py)
    # -- remote execution never gets a broader command surface than local execution
    # already has. Requires host-key trust to already exist (StrictHostKeyChecking=yes);
    # this tool will not silently trust an unknown host on first connect.
    ssh_allowed_hosts: List[str] = Field(default_factory=list)

    def validate_runtime(self) -> None:
        if self.jarvis_env.lower() not in {"prod", "production"}:
            return
        errors: list[str] = []
        if self.jwt_secret.startswith("development-only-") or self.jwt_secret in {"change-me-32-bytes-or-more", "change-me"} or len(self.jwt_secret) < 32:
            errors.append("JWT_SECRET must be a non-default value of at least 32 characters")
        if self.postgres_password in {"change-me", "changeme", "password"}:
            errors.append("POSTGRES_PASSWORD must not use a development default")
        if self.bootstrap_admin_password:
            errors.append("BOOTSTRAP_ADMIN_PASSWORD must be unset in production")
        if any(origin == "*" or origin.startswith("http://") for origin in self.allowed_origins):
            errors.append("ALLOWED_ORIGINS must contain explicit HTTPS origins in production")
        if errors:
            raise RuntimeError("unsafe production configuration: " + "; ".join(errors))

    @property
    def postgres_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
