"""Readiness endpoint — dependency reporting and status codes."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.schemas.health import ComponentCheck


def _patch_checks(monkeypatch, *, database: str, redis: str) -> None:
    async def fake_collect(_timeout: float) -> dict[str, ComponentCheck]:
        return {
            "database": ComponentCheck(status=database, latency_ms=1.0),  # type: ignore[arg-type]
            "redis": ComponentCheck(status=redis, latency_ms=1.0),  # type: ignore[arg-type]
        }

    monkeypatch.setattr("app.api.routes.health.collect_readiness", fake_collect)


async def test_ready_when_all_dependencies_ok(client: AsyncClient, monkeypatch) -> None:
    _patch_checks(monkeypatch, database="ok", redis="ok")

    response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["redis"] == "ok"


@pytest.mark.parametrize(
    ("database", "redis"),
    [("error", "ok"), ("ok", "error"), ("error", "error")],
)
async def test_not_ready_returns_503(
    client: AsyncClient, monkeypatch, database: str, redis: str
) -> None:
    _patch_checks(monkeypatch, database=database, redis=redis)

    response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["database"] == database
    assert body["redis"] == redis


async def test_readiness_reports_per_component_detail(client: AsyncClient, monkeypatch) -> None:
    _patch_checks(monkeypatch, database="ok", redis="error")

    body = (await client.get("/ready")).json()

    assert set(body["checks"]) == {"database", "redis"}
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "error"


async def test_readiness_failure_leaks_no_connection_details(
    client: AsyncClient, monkeypatch
) -> None:
    """A failed probe must not surface a DSN, host or credential."""

    async def failing_collect(_timeout: float) -> dict[str, ComponentCheck]:
        return {
            "database": ComponentCheck(status="error", error="unavailable"),
            "redis": ComponentCheck(status="error", error="unavailable"),
        }

    monkeypatch.setattr("app.api.routes.health.collect_readiness", failing_collect)

    raw = (await client.get("/ready")).text.lower()

    for forbidden in ("password", "postgresql://", "postgresql+psycopg", "redis://", "@localhost"):
        assert forbidden not in raw
