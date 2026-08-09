"""Dependency health probes.

Failures are reported as safe summaries. The full exception goes to the logs
(where the redaction processor scrubs it); the API response never carries a
connection string, credential or stack trace.
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
        logger.warning("health.database.error", error_type=type(exc).__name__, exc_info=True)
        return ComponentCheck(status="error", error="unavailable")


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
        logger.warning("health.redis.error", error_type=type(exc).__name__, exc_info=True)
        return ComponentCheck(status="error", error="unavailable")


async def collect_readiness(timeout_seconds: float) -> dict[str, ComponentCheck]:
    """Run all dependency probes concurrently."""
    database, redis = await asyncio.gather(
        check_database(timeout_seconds),
        check_redis(timeout_seconds),
    )
    return {"database": database, "redis": redis}
