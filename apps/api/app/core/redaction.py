"""Secret redaction for logs and error payloads.

Phase 0 §22 defines an explicit never-log list. This module is the single
enforcement point: it is installed as a structlog processor at the root of the
logging pipeline, so no module can bypass it by logging directly.

Redaction happens two ways:
  1. By key name  — any mapping key that looks sensitive has its value replaced.
  2. By pattern   — connection strings and bearer tokens embedded in free text.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

#: Substrings that mark a mapping key as sensitive (matched case-insensitively).
SENSITIVE_KEY_PARTS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "credential",
        "dsn",
        "connection_string",
        "database_url",
        "redis_url",
        "private_key",
        "ca_cert",
        "client_key",
        "cookie",
        "session",
        "ticket",
        "signature",
    }
)

_DSN_PATTERN = re.compile(r"(?P<prefix>[a-zA-Z][a-zA-Z0-9+.-]*://[^:/\s]+:)[^@/\s]+@")
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_PEM_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S
)

#: Free-text patterns that must never survive into a log record.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host  ->  scheme://user:***@host
    (_DSN_PATTERN, r"\g<prefix>" + REDACTED + "@"),
    # Authorization: Bearer <token>
    (_BEARER_PATTERN, r"\1" + REDACTED),
    # PEM private key blocks
    (_PEM_PATTERN, REDACTED),
)

_MAX_DEPTH = 6


def is_sensitive_key(key: str) -> bool:
    """Return True when a mapping key should have its value redacted."""
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scrub_text(value: str) -> str:
    """Remove secret-looking substrings from free text."""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact a value by key name and by pattern."""
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_sensitive_key(key):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact(item, _depth + 1)
        return redacted
    if isinstance(value, (list, tuple)):
        rebuilt = [redact(item, _depth + 1) for item in value]
        return type(value)(rebuilt) if isinstance(value, tuple) else rebuilt
    return value


def redaction_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor applying :func:`redact` to the whole event."""
    result = redact(event_dict)
    assert isinstance(result, dict)
    return result
