"""Phase 12 — performance and scale.

An N+1 is invisible in every ordinary test. The response is correct, the suite
is green, and the only symptom is that a page which was fine with five rows
takes four seconds with five hundred. It is found by counting queries, not by
reading code, and it comes back the moment someone adds a field that needs one
more lookup per row.

So these tests count. Each one runs an endpoint at two different data volumes
and asserts the query count did not change. A route that issues one query per
row fails here and nowhere else.

They deliberately assert *invariance*, not a specific number. Pinning an exact
count would turn every legitimate refactor into a failing test, and the number
itself is not the property worth protecting — its independence from the row
count is.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_engine
from tests.factories import register
from tests.test_reality_api import (
    create_entity,
    map_row,
    seed_observation,
    seed_source,
)


@asynccontextmanager
async def counting_queries() -> AsyncIterator[list[str]]:
    """Record every SQL statement executed inside the block.

    Hooked at the engine rather than the session, so it sees lazy loads too —
    which is where an N+1 usually hides.
    """
    statements: list[str] = []
    engine = get_engine().sync_engine

    def _record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        normalised = " ".join(statement.split())
        # Transaction bookkeeping is not per-row work, and the test harness
        # emits savepoints unevenly between runs. Counting them would make the
        # comparison noisy in exactly the direction that hides a real N+1.
        if normalised.split(" ", 1)[0].upper() in {
            "SAVEPOINT",
            "RELEASE",
            "ROLLBACK",
            "BEGIN",
            "COMMIT",
        }:
            return
        statements.append(normalised)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


async def assert_query_count_is_independent_of_row_count(
    *,
    build: Callable[[int], Any],
    call: Callable[[], Any],
    small: int = 3,
    large: int = 12,
) -> None:
    """Run `call` after building `small` rows, then after building `large`.

    The absolute count is not asserted — only that it did not grow. A route
    that issues one query per row will show a difference of roughly
    ``large - small``.
    """
    await build(small)
    async with counting_queries() as first:
        await call()
    small_count = len(first)

    await build(large - small)
    async with counting_queries() as second:
        await call()
    large_count = len(second)

    # `<=`, not `==`. Growth is the defect; a *smaller* second count is benign
    # and happens for real reasons — the ORM identity map serves the session
    # and user lookups from memory on a repeat call. Requiring exact equality
    # would fail on that, and a flaky performance test gets deleted rather than
    # investigated.
    assert large_count <= small_count, (
        f"query count grew from {small_count} to {large_count} when the row count "
        f"went from {small} to {large}. That is an N+1: the endpoint issues work "
        f"per row rather than per request.\n"
        f"Statements on the larger run:\n  " + "\n  ".join(sorted(set(second))[:8])
    )


# --- The two N+1s found and fixed in Phase 12 -------------------------------


async def test_listing_data_sources_does_not_scale_with_source_count(
    client: AsyncClient, db: AsyncSession
) -> None:
    """N+1 FOUND AND FIXED IN PHASE 12.

    ``_source_response`` ran two counts per source — one for streams, one for
    observations — so the endpoint cost 2N+4 queries. Measured at 52 queries for
    24 sources before the fix, 6 after.
    """
    account = await register(client)

    async def build(count: int) -> None:
        for _ in range(count):
            await seed_source(
                db,
                organization_id=account.organization_id,
                name=f"src-{uuid.uuid4().hex[:8]}",
            )
        await db.commit()

    async def call() -> None:
        response = await client.get("/api/data-sources", headers=account.auth_headers())
        assert response.status_code == 200

    await assert_query_count_is_independent_of_row_count(build=build, call=call)


async def test_listing_entities_does_not_scale_with_entity_count(
    client: AsyncClient, db: AsyncSession
) -> None:
    """N+1 FOUND AND FIXED IN PHASE 12.

    ``list_entities`` called ``count_observations`` once per entity, costing
    N+4 queries. The mapping count was already a correlated subquery; the
    observation count was not.
    """
    account = await register(client)

    async def build(count: int) -> None:
        for _ in range(count):
            await create_entity(client, account, key=f"E-{uuid.uuid4().hex[:8]}")

    async def call() -> None:
        response = await client.get("/api/entities", headers=account.auth_headers())
        assert response.status_code == 200

    await assert_query_count_is_independent_of_row_count(build=build, call=call)


async def test_the_dashboard_does_not_scale_with_source_count(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The dashboard is a fixed set of aggregates and must stay that way.

    It was already flat when measured; this keeps it flat. A per-source lookup
    added here would be the most expensive N+1 in the product, because the
    dashboard is the first page every user loads.
    """
    account = await register(client)

    async def build(count: int) -> None:
        for _ in range(count):
            source, stream = await seed_source(
                db,
                organization_id=account.organization_id,
                name=f"src-{uuid.uuid4().hex[:8]}",
            )
            await seed_observation(
                db,
                organization_id=account.organization_id,
                source=source,
                stream=stream,
                payload={"quantity": 1},
            )
        await db.commit()

    async def call() -> None:
        response = await client.get("/api/dashboard", headers=account.auth_headers())
        assert response.status_code == 200

    await assert_query_count_is_independent_of_row_count(build=build, call=call)


async def test_the_activity_feed_does_not_scale_with_event_count(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)

    async def build(count: int) -> None:
        for _ in range(count):
            await create_entity(client, account, key=f"A-{uuid.uuid4().hex[:8]}")

    async def call() -> None:
        response = await client.get("/api/activity", headers=account.auth_headers())
        assert response.status_code == 200

    await assert_query_count_is_independent_of_row_count(build=build, call=call)


# --- The batched counts must still be correct -------------------------------


async def test_batched_counts_match_what_they_replaced(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Fewer queries is worthless if the numbers changed.

    Each source gets a different number of streams and observations, so a
    batching bug that returned one source's count for another — or dropped a
    source with no rows — shows up as a mismatch rather than passing by
    coincidence.
    """
    from app.api.routes.data_sources import _counts_by_source

    account = await register(client)
    expected: dict[uuid.UUID, tuple[int, int]] = {}

    for index in range(4):
        source, stream = await seed_source(
            db, organization_id=account.organization_id, name=f"src-{index}"
        )
        for observation in range(index):
            await seed_observation(
                db,
                organization_id=account.organization_id,
                source=source,
                stream=stream,
                payload={"quantity": observation},
                external_id=f"id={observation}",
            )
        expected[source.id] = (1, index)
    await db.commit()

    counts = await _counts_by_source(
        db, organization_id=account.organization_id, source_ids=list(expected)
    )

    assert counts == expected

    # A source with nothing attached must be present with zero, not missing —
    # it is absent from a grouped result, and dropping it would make the list
    # endpoint omit the source entirely. Built bare rather than through
    # seed_source, which always attaches a stream.
    from app.models.data_source import DataSource

    empty = DataSource(
        organization_id=account.organization_id,
        name="empty",
        kind="postgresql",
        config={"host": "h", "port": 5432, "database": "d", "username": "u"},
    )
    db.add(empty)
    await db.commit()

    assert (
        await _counts_by_source(db, organization_id=account.organization_id, source_ids=[empty.id])
    ) == {empty.id: (0, 0)}


async def test_batched_entity_counts_match_per_entity_counts(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The batched version must agree with the single-entity one it replaced."""
    from app.services.entities import count_observations, count_observations_by_entity

    account = await register(client)
    entity_ids: list[uuid.UUID] = []

    for index in range(3):
        source, stream = await seed_source(
            db, organization_id=account.organization_id, name=f"s-{index}"
        )
        for observation in range(index + 1):
            await seed_observation(
                db,
                organization_id=account.organization_id,
                source=source,
                stream=stream,
                payload={"quantity": observation},
                external_id=f"id={observation}",
            )
        await db.commit()

        entity_id = await create_entity(client, account, key=f"ENT-{index}")
        for observation in range(index + 1):
            await map_row(
                client,
                account,
                entity_id=entity_id,
                stream=stream,
                external_id=f"id={observation}",
            )
        entity_ids.append(uuid.UUID(entity_id))

    batched = await count_observations_by_entity(
        db, organization_id=account.organization_id, entity_ids=entity_ids
    )
    for entity_id in entity_ids:
        one_at_a_time = await count_observations(
            db, organization_id=account.organization_id, entity_id=entity_id
        )
        assert batched.get(entity_id, 0) == one_at_a_time


async def test_batched_counts_never_cross_a_tenant(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """A grouped aggregate is exactly where a tenant filter gets forgotten."""
    from app.api.routes.data_sources import _counts_by_source

    first = await register(client)
    second = await register(anonymous_client)

    a_source, _ = await seed_source(db, organization_id=first.organization_id, name="A")
    b_source, b_stream = await seed_source(db, organization_id=second.organization_id, name="B")
    for _ in range(3):
        await seed_observation(
            db,
            organization_id=second.organization_id,
            source=b_source,
            stream=b_stream,
            payload={"quantity": 1},
            external_id=f"id={uuid.uuid4().hex[:6]}",
        )
    await db.commit()

    # Asking for B's source id while scoped to A must yield nothing of B's.
    counts = await _counts_by_source(
        db,
        organization_id=first.organization_id,
        source_ids=[a_source.id, b_source.id],
    )

    assert counts[b_source.id] == (0, 0), "a grouped aggregate leaked another tenant's rows"
    assert counts[a_source.id] == (1, 0)


# --- Bounded responses -------------------------------------------------------


async def test_list_endpoints_cap_their_result_size(client: AsyncClient, db: AsyncSession) -> None:
    """An unbounded list is a scale problem waiting for a large tenant.

    These are caps, not cursors. Real pagination belongs with the phase that
    needs it; a ceiling is what stops one request returning an entire table.
    """
    account = await register(client)
    for index in range(6):
        await seed_source(db, organization_id=account.organization_id, name=f"cap-{index}")
    await db.commit()

    capped = await client.get("/api/data-sources?limit=2", headers=account.auth_headers())
    assert capped.status_code == 200
    assert len(capped.json()) == 2

    # And the cap is validated rather than trusted.
    assert (
        await client.get("/api/data-sources?limit=0", headers=account.auth_headers())
    ).status_code == 422
    assert (
        await client.get("/api/data-sources?limit=99999", headers=account.auth_headers())
    ).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/entities?limit=1",
        "/api/data-sources?limit=1",
        "/api/conflicts?limit=1",
    ],
)
async def test_every_capped_list_honours_its_limit(
    client: AsyncClient, db: AsyncSession, path: str
) -> None:
    account = await register(client)
    for index in range(3):
        await seed_source(db, organization_id=account.organization_id, name=f"m-{index}")
        await create_entity(client, account, key=f"M-{index}")
    await db.commit()

    response = await client.get(path, headers=account.auth_headers())

    assert response.status_code == 200
    assert len(response.json()) <= 1
