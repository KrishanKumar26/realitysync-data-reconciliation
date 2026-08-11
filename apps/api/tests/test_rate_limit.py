"""Rate limiting and operational status.

Two properties matter more than the counting itself, and both are tested here
against a real Redis rather than a stub:

1. It denies when it should. A limiter that miscounts protects nothing.
2. **It fails open.** When Redis is unreachable, requests are allowed. This is
   deliberate and load-bearing: the architecture's Redis rule is "unavailable
   means degraded, never broken, never wrong", and a limiter that denied
   everything during a cache blip would lock every customer out of the product.

The fail-open test uses a client pointed at a closed port instead of a mock, so
it exercises the real exception path a real outage produces.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.services.rate_limit import (
    NullRateLimiter,
    RateLimitPolicy,
    RedisRateLimiter,
    configure_rate_limiting,
    get_rate_limiter,
    login_policy,
    set_rate_limiter,
)
from tests.factories import DEFAULT_PASSWORD, register, unique_email


@pytest.fixture
async def redis_client(settings) -> Redis:  # type: ignore[no-untyped-def]
    """A dedicated client, so closing it cannot disturb the app singleton.

    Async, and deliberately not marked for anyio: the suite runs under
    pytest-asyncio in auto mode, and mixing the two runners puts the fixture on
    a different event loop from the test. The client then fails every command
    with "Event loop is closed" — which the limiter correctly reads as an
    outage and fails open on, quietly turning the counting tests into
    assertions about nothing.
    """
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception:  # pragma: no cover - depends on the environment
        pytest.skip("Redis is not reachable; rate limiting cannot be tested.")
    yield client
    await client.aclose()


@pytest.fixture
def unreachable_redis() -> Redis:
    """A client that cannot connect.

    Port 1 with a short timeout: a real connection failure, not a patched one.
    """
    return Redis.from_url(
        "redis://127.0.0.1:1/0",
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def _policy(max_attempts: int = 3, window_seconds: int = 60) -> RateLimitPolicy:
    return RateLimitPolicy(
        # Unique name per policy instance, so keys never collide between tests.
        name=f"test.{uuid.uuid4().hex[:12]}",
        max_attempts=max_attempts,
        window_seconds=window_seconds,
    )


# --- Counting ---------------------------------------------------------------


async def test_allows_up_to_the_limit_then_denies(redis_client: Redis) -> None:
    limiter = RedisRateLimiter(redis_client)
    policy = _policy(max_attempts=3)

    verdicts = [await limiter.check(policy, "identity-a") for _ in range(4)]

    assert [v.allowed for v in verdicts] == [True, True, True, False]
    # Remaining counts down and stops at zero rather than going negative.
    assert [v.remaining for v in verdicts] == [2, 1, 0, 0]


async def test_denial_reports_when_capacity_returns(redis_client: Redis) -> None:
    limiter = RedisRateLimiter(redis_client)
    policy = _policy(max_attempts=1, window_seconds=60)

    await limiter.check(policy, "identity-b")
    denied = await limiter.check(policy, "identity-b")

    assert denied.allowed is False
    # Bounded by the window and never zero: a Retry-After of 0 invites an
    # immediate retry that would also be denied.
    assert denied.retry_after_seconds is not None
    assert 0 < denied.retry_after_seconds <= policy.window_seconds + 1


async def test_identities_are_counted_separately(redis_client: Redis) -> None:
    """One user exhausting their budget must not lock out another."""
    limiter = RedisRateLimiter(redis_client)
    policy = _policy(max_attempts=2)

    assert (await limiter.check(policy, "identity-c")).allowed
    assert (await limiter.check(policy, "identity-c")).allowed
    assert not (await limiter.check(policy, "identity-c")).allowed

    assert (await limiter.check(policy, "identity-d")).allowed


async def test_policies_are_counted_separately(redis_client: Redis) -> None:
    limiter = RedisRateLimiter(redis_client)
    login = _policy(max_attempts=1)
    register_policy = _policy(max_attempts=1)

    assert (await limiter.check(login, "shared")).allowed
    assert not (await limiter.check(login, "shared")).allowed
    # Same identity, different policy: an exhausted login budget must not
    # consume the registration budget.
    assert (await limiter.check(register_policy, "shared")).allowed


async def test_window_key_expires(redis_client: Redis) -> None:
    """Keys carry a TTL, so an identity that stops attempting leaves nothing."""
    limiter = RedisRateLimiter(redis_client)
    policy = _policy(max_attempts=5, window_seconds=30)

    await limiter.check(policy, "identity-e")

    ttl = await redis_client.ttl(f"rs:rl:{policy.name}:identity-e")
    assert 0 < ttl <= policy.window_seconds + 1


# --- Degradation ------------------------------------------------------------


async def test_fails_open_when_redis_is_unreachable(unreachable_redis: Redis) -> None:
    """The property the whole design turns on."""
    limiter = RedisRateLimiter(unreachable_redis)
    policy = _policy(max_attempts=1)

    verdicts = [await limiter.check(policy, "identity-f") for _ in range(3)]

    assert all(v.allowed for v in verdicts), (
        "The limiter denied a request while Redis was down. That converts a "
        "cache degradation into a total login outage."
    )
    # The allowance is reported as degraded rather than passed off as a normal
    # decision, so "we are not currently limiting" stays observable.
    assert all(v.degraded for v in verdicts)

    await unreachable_redis.aclose()


async def test_normal_verdicts_are_not_marked_degraded(redis_client: Redis) -> None:
    verdict = await RedisRateLimiter(redis_client).check(_policy(), "identity-g")
    assert verdict.degraded is False


# --- Configuration ----------------------------------------------------------


async def test_login_policy_follows_configuration(settings) -> None:  # type: ignore[no-untyped-def]
    """The configuration keys are wired, not decorative.

    LOGIN_RATE_LIMIT_ATTEMPTS and LOGIN_RATE_LIMIT_WINDOW_SECONDS have existed
    since Phase 2. A key that quietly does nothing tells an operator they have
    a control they do not have.
    """
    policy = login_policy(settings)
    assert policy.max_attempts == settings.login_rate_limit_attempts
    assert policy.window_seconds == settings.login_rate_limit_window_seconds


async def test_configure_installs_the_redis_limiter(redis_client: Redis) -> None:
    previous = get_rate_limiter()
    try:
        limiter = configure_rate_limiting(enabled=True, redis=redis_client)
        assert isinstance(limiter, RedisRateLimiter)
        assert get_rate_limiter() is limiter
    finally:
        set_rate_limiter(previous)


async def test_configure_falls_back_when_disabled(redis_client: Redis) -> None:
    previous = get_rate_limiter()
    try:
        assert isinstance(
            configure_rate_limiting(enabled=False, redis=redis_client), NullRateLimiter
        )
        # Enabled but with no client is the same posture: nothing to count with.
        assert isinstance(configure_rate_limiting(enabled=True, redis=None), NullRateLimiter)
    finally:
        set_rate_limiter(previous)


# --- HTTP behaviour ---------------------------------------------------------


async def test_login_returns_429_with_retry_headers(
    client: AsyncClient, redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The endpoint surfaces the limit in the headers clients act on."""
    # A one-attempt policy, so the test does not have to perform ten Argon2
    # verifications to reach the limit. Patched on the route module, because
    # routes/auth.py imports the resolver by name.
    from app.api.routes import auth as auth_routes

    previous_limiter = get_rate_limiter()
    # Built once and closed over. Calling _policy() inside the lambda would
    # mint a fresh policy name per request, so every attempt would land under a
    # different Redis key and nothing would ever be limited.
    policy = _policy(max_attempts=1, window_seconds=300)
    monkeypatch.setattr(auth_routes, "login_policy", lambda _s: policy)
    set_rate_limiter(RedisRateLimiter(redis_client))
    try:
        payload = {"email": unique_email(), "password": DEFAULT_PASSWORD}
        first = await client.post("/api/auth/login", json=payload)
        second = await client.post("/api/auth/login", json=payload)
    finally:
        set_rate_limiter(previous_limiter)

    # Credentials are wrong either way; what differs is which failure it is.
    assert first.status_code == 401
    assert second.status_code == 429

    assert int(second.headers["Retry-After"]) > 0
    assert second.headers["X-RateLimit-Limit"] == "1"
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert second.headers["X-RateLimit-Window"] == "300"

    # The refusal must not leak whether the account exists.
    assert "exist" not in second.text.lower()


async def test_limited_login_does_not_reveal_account_existence(
    client: AsyncClient, redis_client: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429 says nothing about the credentials it declined to check."""
    from app.api.routes import auth as auth_routes

    previous_limiter = get_rate_limiter()
    policy = _policy(max_attempts=0, window_seconds=300)
    monkeypatch.setattr(auth_routes, "login_policy", lambda _s: policy)
    set_rate_limiter(RedisRateLimiter(redis_client))
    try:
        real = await client.post(
            "/api/auth/login",
            json={"email": unique_email("known"), "password": DEFAULT_PASSWORD},
        )
        fake = await client.post(
            "/api/auth/login",
            json={"email": unique_email("unknown"), "password": DEFAULT_PASSWORD},
        )
    finally:
        set_rate_limiter(previous_limiter)

    assert real.status_code == fake.status_code == 429
    assert real.json()["error"]["message"] == fake.json()["error"]["message"]


# --- Operational status -----------------------------------------------------


async def test_status_requires_authentication(client: AsyncClient) -> None:
    """Degradation detail is operator information, not public information."""
    response = await client.get("/api/system/status")
    assert response.status_code == 401


async def test_status_reports_components(client: AsyncClient) -> None:
    await register(client, email=unique_email(), password=DEFAULT_PASSWORD)

    response = await client.get("/api/system/status")
    assert response.status_code == 200

    body = response.json()
    components = {c["name"]: c for c in body["components"]}
    assert {"rate_limiting", "redis", "reality_engine"} <= set(components)

    # Confidence scoring is blocked, so the engine is degraded and the
    # deployment is degraded. Reporting "operational" here would be exactly the
    # unverified green light this product exists to eliminate.
    assert components["reality_engine"]["state"] == "degraded"
    assert "unavailable" in components["reality_engine"]["detail"].lower()
    assert body["status"] == "degraded"


async def test_status_reports_rate_limiting_as_degraded_when_redis_is_down(
    client: AsyncClient,
    unreachable_redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state describes what the limiter is doing, not how it was set up.

    An installed limiter with no reachable Redis allows every request. Calling
    that "operational" would be the one misleading line in a response an
    operator reads precisely during an outage.
    """
    await register(client, email=unique_email(), password=DEFAULT_PASSWORD)

    previous = get_rate_limiter()
    set_rate_limiter(RedisRateLimiter(unreachable_redis))
    monkeypatch.setattr("app.api.routes.system.get_redis", lambda: unreachable_redis, raising=True)
    try:
        body = (await client.get("/api/system/status")).json()
    finally:
        set_rate_limiter(previous)
        await unreachable_redis.aclose()

    components = {c["name"]: c for c in body["components"]}
    assert components["redis"]["state"] == "degraded"
    assert components["rate_limiting"]["state"] == "degraded"
    assert "not being counted" in components["rate_limiting"]["detail"]
    assert body["status"] == "degraded"


async def test_status_leaks_no_secrets(client: AsyncClient, settings) -> None:  # type: ignore[no-untyped-def]
    await register(client, email=unique_email(), password=DEFAULT_PASSWORD)

    body = (await client.get("/api/system/status")).text

    for secret in (
        settings.secret_key,
        settings.credential_encryption_key,
        settings.database_url,
        settings.redis_url,
    ):
        assert secret not in body
