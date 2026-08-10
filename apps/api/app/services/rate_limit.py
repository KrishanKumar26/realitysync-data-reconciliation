"""Rate limiting seam.

Phase 2 establishes the interface and the call sites; the Redis-backed sliding
window belongs to the phase that owns Redis rate limiting. Defining the seam
now means that phase changes one binding rather than editing every route that
needs protecting — and, more importantly, it means the login path already has
the hook in place instead of needing one retrofitted under pressure.

:class:`NullRateLimiter` allows everything and says so in its name. It is not a
partial implementation pretending to be a real one: a limiter that silently
allowed traffic while looking like it was enforcing something would be worse
than no limiter at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


class RateLimiter(Protocol):
    """Interface every limiter implementation satisfies."""

    async def check(self, policy: RateLimitPolicy, identity: str) -> RateLimitVerdict:
        """Consume one attempt for `identity` under `policy`."""
        ...


class NullRateLimiter:
    """Allows every request. The Phase 2 binding."""

    async def check(self, policy: RateLimitPolicy, identity: str) -> RateLimitVerdict:
        return RateLimitVerdict(allowed=True, remaining=policy.max_attempts)


_limiter: RateLimiter = NullRateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Return the active limiter. Swapped for the Redis implementation later."""
    return _limiter


def set_rate_limiter(limiter: RateLimiter) -> None:
    """Replace the active limiter. Used by the later phase and by tests."""
    global _limiter
    _limiter = limiter


#: Policies are declared here rather than at call sites so the whole limiting
#: posture is reviewable in one place.
LOGIN_POLICY = RateLimitPolicy(name="auth.login", max_attempts=10, window_seconds=300)
REGISTRATION_POLICY = RateLimitPolicy(name="auth.register", max_attempts=5, window_seconds=3600)
