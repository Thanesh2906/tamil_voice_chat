"""Centralized settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
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

    # Auth
    jwt_secret: str = "change-me-32-bytes-or-more"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 2_592_000

    # CORS
    allowed_origins: List[str] = Field(
        default_factory=lambda: ["http://localhost:8080", "http://localhost:5173"]
    )

    # Audio
    audio_sample_rate: int = 16_000
    vad_aggressiveness: int = 2

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
