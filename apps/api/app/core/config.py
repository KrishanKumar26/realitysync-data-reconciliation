"""Application configuration.

All configuration is environment-driven (Phase 0 §24). Nothing here has a
production-safe default: secrets must be supplied by the environment, and the
application refuses to start in production if they are missing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]

#: Published development key. Named so the production check reads clearly and
#: so nobody has to guess which literal is the unsafe one.
_DEV_CREDENTIAL_KEY = "cmVhbGl0eXN5bmMtZGV2ZWxvcG1lbnQta2V5LW9ubHk="


class Settings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Identity ---------------------------------------------------------
    app_name: str = "RealitySync API"
    api_version: str = "0.1.0"
    environment: Environment = "development"
    log_level: str = "INFO"

    # --- Datastores -------------------------------------------------------
    # SQLAlchemy async URL. psycopg3 is the driver for both the application
    # database and (later) the PostgreSQL connector, so there is one driver
    # to reason about.
    database_url: str = "postgresql+psycopg://realitysync:realitysync@localhost:5432/realitysync"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_recycle_seconds: int = 1800
    database_connect_timeout_seconds: int = 5

    redis_url: str = "redis://localhost:6379/0"
    redis_connect_timeout_seconds: int = 3

    # --- Public URLs ------------------------------------------------------
    api_base_url: str = "http://localhost:8000"
    web_base_url: str = "http://localhost:3000"

    # --- CORS -------------------------------------------------------------
    # Explicit origin allowlist. "*" is rejected: the API uses credentialed
    # requests, and wildcard + credentials is both invalid and unsafe.
    #
    # NoDecode suppresses pydantic-settings' default JSON decoding for complex
    # types. Without it, a plain `CORS_ORIGINS=http://localhost:3000` line in a
    # .env file raises before _split_cors_origins ever runs, because the raw
    # value is not valid JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Cookies ----------------------------------------------------------
    # Environment-driven so the staging (cross-site) and production
    # (same-site, shared parent domain) configurations differ only by config.
    cookie_name: str = "rs_session"
    cookie_domain: str | None = None
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    #: Companion readable cookie carrying the CSRF token. Deliberately NOT
    #: HttpOnly — the browser must read it to echo it in a request header.
    #: It is useless on its own: it authenticates nothing.
    csrf_cookie_name: str = "rs_csrf"
    csrf_header_name: str = "X-CSRF-Token"

    # --- Sessions ---------------------------------------------------------
    #: Absolute ceiling. A session dies at issued_at + lifetime regardless of
    #: activity, so a stolen cookie has a bounded useful life.
    session_lifetime_seconds: int = 60 * 60 * 24 * 14  # 14 days
    #: Idle timeout. A session unused for this long is rejected.
    session_idle_timeout_seconds: int = 60 * 60 * 24  # 24 hours
    #: Minimum interval between last_seen_at writes, so an active session does
    #: not cause a database write on every single request.
    session_touch_interval_seconds: int = 60

    # --- Password policy --------------------------------------------------
    password_min_length: int = 12
    password_max_length: int = 256

    # --- Argon2id parameters ---------------------------------------------
    # Defaults follow the OWASP Password Storage Cheat Sheet recommendation
    # (19 MiB, t=2, p=1). Configurable because the right cost depends on the
    # deployment's CPU budget, and tests need a cheap setting.
    argon2_time_cost: int = 2
    argon2_memory_cost_kib: int = 19456
    argon2_parallelism: int = 1

    # --- Rate limiting ----------------------------------------------------
    #: Redis-backed sliding window. Disable only when rate limiting is
    #: terminated at the edge (a CDN or gateway); disabling it here with
    #: nothing in front leaves the login endpoint unprotected.
    rate_limiting_enabled: bool = True
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300

    # --- Credential encryption -------------------------------------------
    # Base64 AES-256 key protecting source credentials at rest. The default is
    # a published, well-known value: it exists so `docker compose up` works out
    # of the box, and production refuses to start with it.
    #   Generate one with:
    #     python -c "from app.core.encryption import generate_key; print(generate_key())"
    credential_encryption_key: str = _DEV_CREDENTIAL_KEY

    #: Decrypt-only keys retired by rotation, as "version:base64" pairs.
    #: Records carry the version that produced them, so old credentials keep
    #: working while they are re-encrypted in the background.
    credential_encryption_previous_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    credential_encryption_key_version: int = 1

    # --- Connector defaults ----------------------------------------------
    #: Applied to every outbound connection to a customer database, so an
    #: unreachable host fails in seconds rather than holding a worker.
    connector_connect_timeout_seconds: int = 10
    connector_statement_timeout_seconds: int = 30
    #: Ceiling on rows read in a single sync. Bounds memory and run time
    #: against a table far larger than expected.
    connector_max_rows_per_sync: int = 50_000
    connector_fetch_batch_size: int = 1_000

    # --- Secrets ----------------------------------------------------------
    # Placeholder default; _enforce_production_hardening rejects it in production.
    secret_key: str = "dev-only-insecure-change-me"  # noqa: S105

    # --- Health -----------------------------------------------------------
    readiness_timeout_seconds: float = 3.0

    @field_validator("cors_origins", "credential_encryption_previous_keys", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept a comma-separated string from the environment.

        Same NoDecode reasoning as cors_origins: pydantic-settings would
        otherwise try to JSON-decode a complex type before this runs.
        """
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError(
                "CORS_ORIGINS must be an explicit allowlist; '*' is not permitted "
                "because the API uses credentialed requests."
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return upper

    @model_validator(mode="after")
    def _enforce_production_hardening(self) -> Settings:
        """Fail fast rather than run production with development defaults."""
        if self.environment == "production":
            if self.secret_key == "dev-only-insecure-change-me":  # noqa: S105
                raise ValueError("SECRET_KEY must be set explicitly in production")
            if self.credential_encryption_key == _DEV_CREDENTIAL_KEY:
                raise ValueError(
                    "CREDENTIAL_ENCRYPTION_KEY must be set explicitly in production; "
                    "the default is a published value and would leave every stored "
                    "source credential readable by anyone with the source code."
                )
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if any(origin.startswith("http://") for origin in self.cors_origins):
                raise ValueError("CORS_ORIGINS must use https:// in production")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        if self.session_idle_timeout_seconds > self.session_lifetime_seconds:
            raise ValueError(
                "SESSION_IDLE_TIMEOUT_SECONDS cannot exceed SESSION_LIFETIME_SECONDS; "
                "the idle timeout would never be reached."
            )
        if self.password_min_length < 8:
            raise ValueError("PASSWORD_MIN_LENGTH must be at least 8")
        if self.password_min_length > self.password_max_length:
            raise ValueError("PASSWORD_MIN_LENGTH cannot exceed PASSWORD_MAX_LENGTH")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
