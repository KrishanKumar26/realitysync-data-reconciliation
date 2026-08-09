"""Liveness endpoint."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"]
    assert body["version"]
    assert body["environment"]


async def test_health_does_not_touch_dependencies(client: AsyncClient, monkeypatch) -> None:
    """Liveness must not consult PostgreSQL or Redis.

    The platform health check polls this endpoint; if it probed dependencies,
    a Redis outage would trigger a restart loop.
    """

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("liveness probe must not touch dependencies")

    monkeypatch.setattr("app.db.session.get_sessionmaker", _explode)
    monkeypatch.setattr("app.cache.redis.get_redis", _explode)

    response = await client.get("/health")
    assert response.status_code == 200


async def test_health_echoes_request_id(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "req_from_client"})

    assert response.headers["X-Request-ID"] == "req_from_client"


async def test_health_generates_request_id_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health")

    request_id = response.headers["X-Request-ID"]
    assert request_id.startswith("req_")


async def test_hostile_request_id_is_replaced(client: AsyncClient) -> None:
    """An oversized or non-printable inbound id must not reach logs or headers."""
    response = await client.get("/health", headers={"X-Request-ID": "x" * 500})

    assert response.headers["X-Request-ID"] != "x" * 500
    assert response.headers["X-Request-ID"].startswith("req_")
