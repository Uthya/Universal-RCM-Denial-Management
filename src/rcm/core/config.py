"""Application settings loaded from .env via pydantic-settings.

No production defaults exist for DATABASE_URL or JWT_SECRET_KEY — missing
values raise at import time. This was a v1 mistake (hardcoded default URL)
that silently pointed migrations at the wrong DB.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- required ---------------------------------------------------------
    DATABASE_URL: str
    JWT_SECRET_KEY: str

    # ---- JWT --------------------------------------------------------------
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ---- HTTP -------------------------------------------------------------
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    DEBUG: bool = False

    # ---- parser -----------------------------------------------------------
    PARSER_VERSION: str = "v2.0.0"
    MAX_UPLOAD_SIZE_MB: int = 50

    # ---- ML ---------------------------------------------------------------
    ML_ARTIFACTS_BASE: str = "src/rcm/ml/artifacts"
    MIN_TRAINING_SIZE: int = 1000
    PRECISION_FLOOR: float = 0.85
    LOW_PROB_CUTOFF: float = 0.05

    # ---- background jobs --------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"

    # ---- RAG (off by default until provider is built) ---------------------
    RAG_ENABLED: bool = False
    LLM_PROVIDER: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    EMBEDDING_PROVIDER: str = ""
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    PGVECTOR_DIMENSIONS: int = 1024

    # ---- logging ----------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"  # 'json' or 'console'

    # ---- validators -------------------------------------------------------
    @field_validator("DATABASE_URL")
    @classmethod
    def _must_be_async_dsn(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver: "
                "postgresql+asyncpg://USER:PASS@HOST:PORT/DB"
            )
        return v

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _reject_blank_secret(cls, v: str) -> str:
        if not v or len(v) < 16:
            raise ValueError("JWT_SECRET_KEY must be at least 16 characters")
        return v

    @field_validator("PRECISION_FLOOR", "LOW_PROB_CUTOFF")
    @classmethod
    def _zero_to_one(cls, v: float) -> float:
        if not 0.0 < v < 1.0:
            raise ValueError(f"Expected value in (0,1), got {v}")
        return v

    # ---- helpers ----------------------------------------------------------
    @property
    def artifacts_dir(self) -> Path:
        return Path(self.ML_ARTIFACTS_BASE)

    def sync_database_url(self) -> str:
        """Sync DSN for tooling that can't use asyncpg (e.g. plain psycopg)."""
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

    def database_url_redacted(self) -> str:
        """Safe-to-log DSN with password masked."""
        url = self.DATABASE_URL
        if "@" not in url:
            return url
        proto_user, host_part = url.split("@", 1)
        if ":" not in proto_user.split("//", 1)[1]:
            return url
        proto, userpass = proto_user.split("//", 1)
        user, _ = userpass.split(":", 1)
        return f"{proto}//{user}:***@{host_part}"


settings = Settings()  # type: ignore[call-arg]
