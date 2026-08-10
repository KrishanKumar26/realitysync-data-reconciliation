"""Canonical value normalisation.

Turns driver-native values into a JSON-safe canonical form. Two properties
matter, and both are load-bearing:

**Determinism.** The same database value must always produce the same
canonical form, in this process and in one running a year from now. Fingerprints
are computed over the result, so any variation would make an unchanged row look
changed and break idempotency.

**Lossless enough to be honest.** ``NUMERIC(10,3)`` becomes the string
``"12.500"``, not the float ``12.5``. A float cannot represent every decimal
exactly, and silently rounding a customer's financial figure to fit a binary
float is the kind of quiet corruption this product exists to detect, not commit.
Trailing zeros are preserved because scale is information: a source that says
``12.500`` is claiming milligram precision.

This lives in the ingestion layer rather than in a connector, so every
connector produces identical observations for identical values.
"""

from __future__ import annotations

import base64
import datetime as dt
import ipaddress
import uuid
from decimal import Decimal
from enum import Enum
from typing import Any

#: Deeper than this and the value is almost certainly not a business fact.
#: Bounds a pathological nested JSONB document.
_MAX_DEPTH = 12


def normalize_value(value: Any, *, _depth: int = 0) -> Any:
    """Return the canonical JSON-safe form of `value`."""
    if _depth > _MAX_DEPTH:
        return str(value)

    # None / bool before int: bool is a subclass of int in Python, and
    # normalising True to 1 would lose the distinction between a boolean
    # column and an integer one.
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        # Kept as a float. PostgreSQL's own float8 is binary, so the value was
        # already approximate before it reached us; converting to a string here
        # would imply a precision the source never had.
        return value

    if isinstance(value, Decimal):
        # str() preserves scale and never uses scientific notation for the
        # ranges databases produce. NaN and Infinity are not valid JSON, so
        # they become their string spellings rather than corrupting the row.
        if value.is_nan() or value.is_infinite():
            return str(value)
        return str(value)

    if isinstance(value, str):
        return value

    if isinstance(value, (bytes, bytearray, memoryview)):
        # Base64 rather than a hex or lossy text decode: bytea can hold
        # anything, including invalid UTF-8.
        return base64.b64encode(bytes(value)).decode("ascii")

    # datetime before date: datetime is a subclass of date.
    if isinstance(value, dt.datetime):
        return _normalize_datetime(value)

    if isinstance(value, dt.date):
        return value.isoformat()

    if isinstance(value, dt.time):
        return value.isoformat()

    if isinstance(value, dt.timedelta):
        # Total seconds, not a locale-dependent rendering of "3 days".
        return f"{value.total_seconds()}s"

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return str(value)

    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return str(value)

    if isinstance(value, Enum):
        return normalize_value(value.value, _depth=_depth + 1)

    if isinstance(value, dict):
        # Keys sorted so two dicts with the same content serialise identically
        # regardless of insertion order.
        return {
            str(key): normalize_value(item, _depth=_depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }

    if isinstance(value, (list, tuple)):
        # Order is preserved: in an array column it is data, not incidental.
        return [normalize_value(item, _depth=_depth + 1) for item in value]

    if isinstance(value, (set, frozenset)):
        # A set has no order, so one is imposed to keep the output stable.
        return sorted(normalize_value(item, _depth=_depth + 1) for item in value)

    # Ranges, composite types, vendor extensions. str() is the honest fallback
    # and stays deterministic for a given driver version, which is recorded in
    # each observation's provenance.
    return str(value)


def _normalize_datetime(value: dt.datetime) -> str:
    """ISO-8601 in UTC, always with an offset.

    A naive timestamp is assumed to be UTC. PostgreSQL's ``timestamp without
    time zone`` carries no offset, and guessing a local zone would silently
    shift every event by hours — assuming UTC is at least uniform, documented,
    and recorded in the stream's event-time semantics.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC).isoformat()


def normalize_row(values: dict[str, Any]) -> dict[str, Any]:
    """Normalise a whole row, keyed by column name."""
    return {
        str(column): normalize_value(value)
        for column, value in sorted(values.items(), key=lambda pair: str(pair[0]))
    }
