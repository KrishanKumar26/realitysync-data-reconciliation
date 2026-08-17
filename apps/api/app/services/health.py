"""Dependency health probes.

Failures are reported as safe summaries. The full exception goes to the logs
(where the redaction processor scrubs it); the API response never carries a
connection string, credential or stack trace.

The summary is a **classification**, not a message. "unavailable" for every
possible failure is useless to the person holding the pager: a wrong password,
an unresolvable host and a firewall are three different jobs, and telling them
apart from a 503 alone means going to the logs — which is exactly what you
cannot do when the log viewer is the thing that is down.

The vocabulary below is fixed and closed. It names no host, no user, no
database and no driver text, so it distinguishes *our own* misconfiguration
without becoming a probe anyone can point at anything else.
"""

from __future__ import annotations

import asyncio
import time

from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.cache.redis import get_redis
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.schemas.health import ComponentCheck

logger = get_logger(__name__)

#: Fixed vocabulary, closed and ordered. Each entry is a set of alternatives;
#: the first entry with any match wins, so the more actionable classifications
#: come first. A driver that reports several failed attempts at once — as
#: libpq does when a host resolves to both an IPv4 and an IPv6 address — will
#: mention more than one cause, and "your password is wrong" is the one worth
#: acting on before "one of the addresses had no route".
_FAILURE_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("password authentication failed", "authentication failed"),
        "authentication_failed",
    ),
    (
        (
            "could not translate host name",
            "name or service not known",
            "nodename nor servname",
        ),
        "host_unresolvable",
    ),
    (("network is unreachable", "no route to host", "host is unreachable"), "unreachable"),
    (("connection refused",), "connection_refused"),
    (("certificate verify failed", "ssl error", "sslv3 alert"), "tls_failed"),
    (("timeout", "timed out"), "timeout"),
    (("does not exist",), "missing_database_or_role"),
)


def classify_failure(exc: BaseException) -> str:
    """One word for what went wrong, from the closed vocabulary above."""
    text_form = str(exc).lower()
    for needles, label in _FAILURE_PATTERNS:
        if any(needle in text_form for needle in needles):
            return label
    return "unavailable"


async def check_database(timeout_seconds: float) -> ComponentCheck:
    """Probe PostgreSQL with a trivial round trip."""
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            async with get_sessionmaker()() as session:
                await session.execute(text("SELECT 1"))
        elapsed_ms = (time.perf_counter() - started) * 1000
        return ComponentCheck(status="ok", latency_ms=round(elapsed_ms, 2))
    except TimeoutError:
        logger.warning("health.database.timeout", timeout_seconds=timeout_seconds)
        return ComponentCheck(status="error", error="timeout")
    except SQLAlchemyError as exc:
        reason = classify_failure(exc)
        logger.warning(
            "health.database.error",
            error_type=type(exc).__name__,
            reason=reason,
            exc_info=True,
        )
        return ComponentCheck(status="error", error=reason)


async def check_redis(timeout_seconds: float) -> ComponentCheck:
    """Probe Redis with PING."""
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            await get_redis().ping()
        elapsed_ms = (time.perf_counter() - started) * 1000
        return ComponentCheck(status="ok", latency_ms=round(elapsed_ms, 2))
    except TimeoutError:
        logger.warning("health.redis.timeout", timeout_seconds=timeout_seconds)
        return ComponentCheck(status="error", error="timeout")
    except (RedisError, OSError) as exc:
        reason = classify_failure(exc)
        logger.warning(
            "health.redis.error",
            error_type=type(exc).__name__,
            reason=reason,
            exc_info=True,
        )
        return ComponentCheck(status="error", error=reason)


async def collect_readiness(timeout_seconds: float) -> dict[str, ComponentCheck]:
    """Run all dependency probes concurrently."""
    database, redis = await asyncio.gather(
        check_database(timeout_seconds),
        check_redis(timeout_seconds),
    )
    return {"database": database, "redis": redis}
