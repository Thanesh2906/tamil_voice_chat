from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

UNSAFE_SECRETS = {"change-me", "changeme", "changeme123", "secret", "dev"}


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str = "development"
    jwt_secret: str = "development-only-secret-change-me"
    database_path: Path = Path("data/jarvis.db")
    allowed_rag_roots: tuple[Path, ...] = (Path("workspace"),)
    cors_origins: tuple[str, ...] = ("http://localhost:8080",)
    access_token_seconds: int = 900
    refresh_token_seconds: int = 2_592_000
    max_audio_queue_frames: int = 128
    max_audio_bytes: int = 10 * 1024 * 1024
    llm_url: str = "http://llm:11434"
    stt_url: str = "http://stt:8001"
    tts_url: str = "http://tts:8002"
    rag_url: str = "http://rag:8003"
    monitoring_url: str = "http://monitoring:8004"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            environment=os.getenv("JARVIS_ENV", "development").lower(),
            jwt_secret=os.getenv("JARVIS_JWT_SECRET", "development-only-secret-change-me"),
            database_path=Path(os.getenv("JARVIS_DATABASE_PATH", "data/jarvis.db")),
            allowed_rag_roots=tuple(
                Path(p).expanduser()
                for p in _csv(os.getenv("JARVIS_ALLOWED_RAG_ROOTS", "workspace"))
            ),
            cors_origins=_csv(os.getenv("JARVIS_CORS_ORIGINS", "http://localhost:8080")),
            llm_url=os.getenv("JARVIS_LLM_URL", "http://llm:11434"),
            stt_url=os.getenv("JARVIS_STT_URL", "http://stt:8001"),
            tts_url=os.getenv("JARVIS_TTS_URL", "http://tts:8002"),
            rag_url=os.getenv("JARVIS_RAG_URL", "http://rag:8003"),
            monitoring_url=os.getenv("JARVIS_MONITORING_URL", "http://monitoring:8004"),
        )

    def validate(self) -> None:
        if self.environment in {"production", "prod"}:
            normalized = self.jwt_secret.strip().lower()
            if (
                normalized in UNSAFE_SECRETS
                or "change-me" in normalized
                or len(self.jwt_secret) < 32
            ):
                raise RuntimeError(
                    "Production requires a non-default JWT secret of at least 32 characters"
                )
            if any(origin == "*" or origin.startswith("http://") for origin in self.cors_origins):
                raise RuntimeError("Production CORS origins must be explicit HTTPS origins")
