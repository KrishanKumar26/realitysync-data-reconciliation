"""Secret redaction.

Phase 0 §22 lists what must never be logged. These tests are the enforcement
check for that list.
"""

from __future__ import annotations

import pytest

from app.core.redaction import REDACTED, is_sensitive_key, redact, scrub_text


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "db_password",
        "secret_key",
        "api_key",
        "authorization",
        "session_token",
        "database_url",
        "redis_url",
        "connection_string",
        "ca_cert",
        "private_key",
        "cookie",
    ],
)
def test_sensitive_keys_are_detected(key: str) -> None:
    assert is_sensitive_key(key) is True


@pytest.mark.parametrize("key", ["host", "port", "database", "username", "entity_id", "status"])
def test_ordinary_keys_are_not_redacted(key: str) -> None:
    assert is_sensitive_key(key) is False


def test_mapping_values_are_redacted_by_key() -> None:
    result = redact({"username": "rs_ro", "password": "hunter2"})

    assert result == {"username": "rs_ro", "password": REDACTED}


def test_nested_structures_are_redacted() -> None:
    payload = {"source": {"config": {"host": "db.example.com", "password": "hunter2"}}}

    result = redact(payload)

    assert result["source"]["config"]["host"] == "db.example.com"
    assert result["source"]["config"]["password"] == REDACTED


def test_dsn_password_is_scrubbed_from_free_text() -> None:
    text = "could not connect to postgresql://rs_user:sup3rs3cret@db.example.com:5432/prod"

    result = scrub_text(text)

    assert "sup3rs3cret" not in result
    assert REDACTED in result
    assert "db.example.com" in result


def test_bearer_token_is_scrubbed() -> None:
    result = scrub_text("Authorization: Bearer abcdef0123456789xyz")

    assert "abcdef0123456789xyz" not in result


def test_private_key_block_is_scrubbed() -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAK\n-----END RSA PRIVATE KEY-----"

    assert "MIIEowIBAAK" not in scrub_text(pem)


def test_redaction_survives_lists_and_recursion_limit() -> None:
    payload = {"items": [{"token": "abc"}, {"safe": "value"}]}

    result = redact(payload)

    assert result["items"][0]["token"] == REDACTED
    assert result["items"][1]["safe"] == "value"
