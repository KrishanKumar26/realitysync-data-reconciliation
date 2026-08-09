"""Configuration validation.

Configuration is the foundation's security boundary: a misconfigured CORS
allowlist or an unset production secret is a real vulnerability, so these
rules are asserted rather than assumed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_PROD_BASE = {
    "environment": "production",
    "secret_key": "a-real-secret-value",
    "cookie_secure": True,
    "cors_origins": ["https://app.example.com"],
}


def test_cors_origins_accepts_comma_separated_string() -> None:
    settings = Settings(cors_origins="http://a.test, http://b.test")  # type: ignore[arg-type]

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_wildcard_cors_origin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="explicit allowlist"):
        Settings(cors_origins=["*"])


def test_production_requires_explicit_secret_key() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**{**_PROD_BASE, "secret_key": "dev-only-insecure-change-me"})  # type: ignore[arg-type]


def test_production_requires_secure_cookies() -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(**{**_PROD_BASE, "cookie_secure": False})  # type: ignore[arg-type]


def test_production_rejects_insecure_cors_origin() -> None:
    with pytest.raises(ValidationError, match="https"):
        Settings(**{**_PROD_BASE, "cors_origins": ["http://app.example.com"]})  # type: ignore[arg-type]


def test_samesite_none_requires_secure() -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(cookie_samesite="none", cookie_secure=False)


def test_samesite_none_allowed_when_secure() -> None:
    settings = Settings(cookie_samesite="none", cookie_secure=True)

    assert settings.cookie_samesite == "none"


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL"):
        Settings(log_level="chatty")


def test_valid_production_settings_construct() -> None:
    settings = Settings(**_PROD_BASE)  # type: ignore[arg-type]

    assert settings.is_production is True


def test_cors_origins_loads_from_dotenv_file(tmp_path, monkeypatch) -> None:
    """Regression: a plain (non-JSON) CORS_ORIGINS line must parse.

    pydantic-settings JSON-decodes complex types by default, which made
    `CORS_ORIGINS=http://localhost:3000` raise at import time before the
    field validator could split it. Only reproducible via a real .env file.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000\n"
        "DATABASE_URL=postgresql+psycopg://u:p@localhost:5432/db\n"
    )
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    settings = Settings(_env_file=str(env_file))  # type: ignore[call-arg]

    assert settings.cors_origins == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_cors_origins_loads_from_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com,https://b.example.com")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["https://a.example.com", "https://b.example.com"]
