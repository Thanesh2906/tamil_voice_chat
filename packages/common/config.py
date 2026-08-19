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

    # LLM
    llm_base_url: str = "http://llm:11434"
    llm_model: str = "llama3.1:8b-instruct-q5_K_M"

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
    jwt_secret: str = "development-only-change-me-secret-32bytes"
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
