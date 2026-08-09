"""Redis client lifecycle.

Phase 1 establishes connectivity only. Phase 0 §20 limits Redis to three
non-authoritative uses — rate limiting, SSE pub/sub and one-time realtime
tickets — none of which are implemented yet.

Redis is never authoritative: losing it must degrade the product, never
corrupt it.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Return the process-wide Redis client, creating it on first use."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_connect_timeout_seconds,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    """Close the client and reset the singleton. Called on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
