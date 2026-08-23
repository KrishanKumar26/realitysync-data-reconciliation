"""Rate limiting.

Phase 2 established the seam; Phase 7 supplies the Redis-backed implementation
it was waiting for. The call sites in ``app/api/routes/auth.py`` are unchanged —
that was the point of defining the interface first.

**The limiter fails open.** If Redis is unavailable, requests are allowed. This
follows directly from the architecture's Redis rule:

    Redis unavailable means degraded, never broken, never wrong.

A limiter that denies everything when its own datastore blips has converted a
degradation into a total outage — and on the login endpoint specifically, it
would lock every customer out of the product because a cache restarted. The
failure is recorded and surfaced on the operational status endpoint, so a
degraded limiter is visible rather than silent.

The window is a **sliding** one, implemented with a Redis sorted set of attempt
timestamps. A fixed window is cheaper but allows a burst of ``2x max`` across a
boundary: ten attempts at 4:59 and ten more at 5:01 pass a five-minute fixed
window. For a credential-stuffing defence that doubling is the whole attack.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """How many attempts are allowed in a window, and under what key."""

    name: str
    max_attempts: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitVerdict:
    allowed: bool
    remaining: int
    retry_after_seconds: int | None = None
    #: True when the limiter could not reach Redis and allowed the request
    #: rather than denying it. Surfaced so "we are not currently limiting" is
    #: an observable fact instead of an assumption.
    degraded: bool = False


class RateLimiter(Protocol):
    """Interface every limiter implementation satisfies."""

    async def check(self, policy: RateLimitPolicy, identity: str) -> RateLimitVerdict:
        """Consume one attempt for `identity` under `policy`."""
        ...


class NullRateLimiter:
    """Allows every request.

    Retained for tests and for deployments that terminate rate limiting at the
    edge. Named so it can never be mistaken for enforcement.
    """

    async def check(self, policy: RateLimitPolicy, identity: str) -> RateLimitVerdict:
        return RateLimitVerdict(allowed=True, remaining=policy.max_attempts)


class RedisRateLimiter:
    """Sliding-window limiter backed by a Redis sorted set.

    One key per (policy, identity). Members are unique per attempt so two
    requests in the same millisecond both count — using the timestamp alone as
    the member would silently collapse them, and a burst is exactly what this
    exists to catch.

    The whole check is one pipeline: drop expired entries, count what remains,
    add this attempt, reset the TTL. Four round trips would leave windows for
    interleaved requests to both read a stale count.
    """

    def __init__(self, redis: Redis, *, key_prefix: str = "rs:rl") -> None:
        self._redis = redis
        self._prefix = key_prefix

    def _key(self, policy: RateLimitPolicy, identity: str) -> str:
        return f"{self._prefix}:{policy.name}:{identity}"

    async def check(self, policy: RateLimitPolicy, identity: str) -> RateLimitVerdict:
        key = self._key(policy, identity)
        now = time.time()
        window_start = now - policy.window_seconds

        try:
            pipeline = self._redis.pipeline(transaction=True)
            # Evict attempts that have aged out of the window.
            pipeline.zremrangebyscore(key, 0, window_start)
            # Count what is left *before* recording this attempt, so the
            # comparison is "how many have already happened".
            pipeline.zcard(key)
            # Unique member per attempt; the score is the timestamp.
            pipeline.zadd(key, {f"{now}:{uuid.uuid4().hex[:12]}": now})
            # Expire the key once the window has fully passed, so an identity
            # that stops attempting leaves nothing behind.
            pipeline.expire(key, policy.window_seconds + 1)
            results = await pipeline.execute()

        except Exception as exc:
            # Fail open. See the module docstring: denying every request
            # because a cache is unreachable turns a degradation into an
            # outage, and Redis holds nothing authoritative here.
            logger.warning(
                "rate_limit.degraded",
                policy=policy.name,
                error_type=type(exc).__name__,
            )
            return RateLimitVerdict(allowed=True, remaining=policy.max_attempts, degraded=True)

        prior_attempts = int(results[1])
        allowed = prior_attempts < policy.max_attempts
        remaining = max(policy.max_attempts - prior_attempts - 1, 0)

        if not allowed:
            # The window clears when the oldest attempt in it ages out.
            retry_after = await self._retry_after(key, policy, now)
            logger.info(
                "rate_limit.exceeded",
                policy=policy.name,
                attempts=prior_attempts,
                retry_after_seconds=retry_after,
            )
            return RateLimitVerdict(allowed=False, remaining=0, retry_after_seconds=retry_after)

        return RateLimitVerdict(allowed=True, remaining=remaining)

    async def _retry_after(self, key: str, policy: RateLimitPolicy, now: float) -> int:
        """Seconds until the oldest attempt leaves the window.

        Honest rather than a flat "try again in five minutes": the caller is
        told when capacity actually returns.
        """
        try:
            oldest = await self._redis.zrange(key, 0, 0, withscores=True)
        except Exception:
            return policy.window_seconds

        if not oldest:
            return 1
        # (member, score) pairs; only the score matters here.
        score = float(oldest[0][1])
        return max(int(policy.window_seconds - (now - score)) + 1, 1)


_limiter: RateLimiter = NullRateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Return the active limiter."""
    return _limiter


def set_rate_limiter(limiter: RateLimiter) -> None:
    """Replace the active limiter. Used at startup and by tests."""
    global _limiter
    _limiter = limiter


def configure_rate_limiting(*, enabled: bool, redis: Redis | None = None) -> RateLimiter:
    """Install the limiter for this process.

    Called from the application lifespan. Returns what was installed so startup
    can log which posture is active — "rate limiting is on" should be a fact in
    the log, not something inferred from configuration.
    """
    limiter: RateLimiter = (
        RedisRateLimiter(redis) if enabled and redis is not None else NullRateLimiter()
    )
    set_rate_limiter(limiter)
    logger.info(
        "rate_limit.configured",
        implementation=type(limiter).__name__,
        enforcing=isinstance(limiter, RedisRateLimiter),
    )
    return limiter


#: Policies are declared here rather than at call sites so the whole limiting
#: posture is reviewable in one place.


def login_policy(settings: Settings) -> RateLimitPolicy:
    """The sign-in limit, from configuration.

    Built from settings rather than hardcoded because
    ``LOGIN_RATE_LIMIT_ATTEMPTS`` and ``LOGIN_RATE_LIMIT_WINDOW_SECONDS`` have
    existed since Phase 2. A configuration key that quietly does nothing is
    worse than no key at all: it tells an operator they have a control they do
    not have.
    """
    return RateLimitPolicy(
        name="auth.login",
        max_attempts=settings.login_rate_limit_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )


#: Registration has no configuration key and does not need one: the limit
#: exists to slow bulk account creation, and there is no deployment-specific
#: reason to widen it.
REGISTRATION_POLICY = RateLimitPolicy(name="auth.register", max_attempts=5, window_seconds=3600)

#: Tighter than registration. A reset request costs the sender nothing and
#: costs the account's owner an unwanted message, so a loose limit turns the
#: form into a way to mail-bomb someone. Keyed on the address, not the IP:
#: limiting by IP would let a distributed caller keep hammering one inbox.
PASSWORD_RESET_REQUEST_POLICY = RateLimitPolicy(
    name="auth.reset_request", max_attempts=3, window_seconds=3600
)

#: Guessing a 256-bit token is not a realistic attack, but an unlimited
#: endpoint that does a database lookup per call is a cheap way to load the
#: server. Keyed on IP, since a guesser has no address to key on.
PASSWORD_RESET_CONFIRM_POLICY = RateLimitPolicy(
    name="auth.reset_confirm", max_attempts=10, window_seconds=3600
)
