"""Application settings, loaded from environment (or a local .env file).

Phase 0 keeps this deliberately small: a database URL, one shared auth token,
and the CORS allow-list. Nothing here knows about pipelines or graphs yet.
"""

import sys
from functools import lru_cache

from pydantic import ValidationError, model_validator
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
    # Demo retains the original shared-token API. Production uses separate
    # credentials for the external loader and human decisions.
    auth_mode: str = "demo"
    loader_service_token: str | None = None
    approver_token: str | None = None

    # Comma-separated origins. Kept as a plain string rather than list[str]:
    # pydantic-settings tries to JSON-decode complex types from env vars, so a
    # bare "http://a,http://b" would raise instead of splitting.
    cors_origins: str = "http://localhost:5173"

    # Informational only in Phase 0 — reported by /api/ping.
    app_env: str = "local"

    # Namespace for mapping_state rows (backend/services/store.py). The real
    # app — local dev and the deployed Render instance alike — uses "default"
    # unless overridden. Test/verification scripts (scripts/check_pipeline.py)
    # pass their own literal namespace explicitly rather than relying on this,
    # so a stray test run can never shadow the mapping the app itself reads.
    pipeline_env: str = "default"

    # Optional, not required, even though ARCHITECTURE.md lists it as a
    # deployment env var: Phases 0-2 must keep booting without it, and
    # services/llm.py raises a clear LLMError (caught by every LLM node,
    # which then escalates — nodes must never raise) rather than the app
    # failing to start. Becomes load-bearing once Phase 3's LLM nodes are
    # live in the deployed graph.
    groq_api_key: str | None = None

    @model_validator(mode="after")
    def validate_auth(self):
        if self.auth_mode not in {"demo", "production"}:
            raise ValueError("AUTH_MODE must be demo or production")
        if self.auth_mode == "production":
            tokens = (self.shared_token, self.loader_service_token, self.approver_token)
            if any(not token or len(token) < 24 for token in tokens) or len(set(tokens)) != 3:
                raise ValueError("production requires three distinct bearer tokens of at least 24 characters")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def _report_and_exit(exc: ValidationError) -> None:
    """Turn a config failure into something readable in a deploy log.

    Raised bare, a missing variable surfaces on Render as a pydantic traceback
    followed by an unexplained "exited with status 1". Name the variables.
    """
    missing = sorted(
        {
            str(err["loc"][0]).upper()
            for err in exc.errors()
            if err.get("type") == "missing" and err.get("loc")
        }
    )

    bar = "=" * 68
    lines = [bar, "CONFIGURATION ERROR - the service cannot start.", bar]
    if missing:
        lines.append("Missing required environment variable(s):")
        lines.extend(f"  - {name}" for name in missing)
        lines.append("")
        lines.append("Set these in the Render dashboard (Environment tab),")
        lines.append("or in a local .env file. See .env.example.")
    else:
        lines.append(str(exc))
    lines.append(bar)

    print("\n".join(["", *lines]), file=sys.stderr, flush=True)
    raise SystemExit(1) from exc


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        _report_and_exit(exc)
        raise  # unreachable; keeps the return type honest
