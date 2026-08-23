"""Application settings, loaded from environment (or a local .env file).

Phase 0 keeps this deliberately small: a database URL, one shared auth token,
and the CORS allow-list. Nothing here knows about pipelines or graphs yet.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Neon Postgres. Include ?sslmode=require.
    database_url: str

    # Single shared bearer token guarding every /api/* route.
    shared_token: str

    # Comma-separated origins. Kept as a plain string rather than list[str]:
    # pydantic-settings tries to JSON-decode complex types from env vars, so a
    # bare "http://a,http://b" would raise instead of splitting.
    cors_origins: str = "http://localhost:5173"

    # Informational only in Phase 0 — recorded so /api/ping can report it.
    app_env: str = "local"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
