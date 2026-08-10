"""Overview dashboard: aggregates, activity, and honest absence.

Every number the dashboard reports is a real count from a real table. The one
value it cannot compute — reality confidence — must be reported as unavailable
rather than as zero, and several tests exist purely to hold that line.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conflict import Conflict
from app.models.data_source import DataSource, SourceStatus
from app.models.entity import Entity, EntityMapping
from app.models.observation import Observation
from app.models.source_stream import SourceStream
from app.models.sync_run import SyncRun, SyncStatus
from app.services.dashboard import build_dashboard, recent_activity
from tests.factories import register

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
LAST_MONTH = NOW - timedelta(days=45)


async def add_source(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    name: str,
    status: SourceStatus = SourceStatus.CONNECTED,
    last_error: str | None = None,
) -> DataSource:
    source = DataSource(
        organization_id=organization_id,
        name=name,
        kind="postgresql",
        status=status.value,
        config={"host": "db.example.com", "port": 5432, "database": "d", "username": "u"},
        last_connected_at=NOW if status is SourceStatus.CONNECTED else None,
        last_error=last_error,
        last_error_at=NOW if last_error else None,
    )
    db.add(source)
    await db.flush()
    return source


async def add_stream(
    db: AsyncSession, *, organization_id: uuid.UUID, source: DataSource, enabled: bool = True
) -> SourceStream:
    stream = SourceStream(
        organization_id=organization_id,
        data_source_id=source.id,
        schema_name="public",
        table_name=f"t_{uuid.uuid4().hex[:8]}",
        primary_key_columns=["id"],
        event_time_column="updated_at",
        event_time_semantics="observed",
        enabled=enabled,
    )
    db.add(stream)
    await db.flush()
    return stream


async def add_observation(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source: DataSource,
    stream: SourceStream,
    payload: dict[str, Any] | None = None,
    ingested_at: datetime = YESTERDAY,
    external_id: str = "id=1",
) -> Observation:
    observation = Observation(
        organization_id=organization_id,
        source_id=source.id,
        stream_id=stream.id,
        external_id=external_id,
        payload=payload or {"quantity": 42},
        event_time=ingested_at,
        ingested_at=ingested_at,
        event_time_semantics="observed",
        fingerprint=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        provenance={},
    )
    db.add(observation)
    await db.flush()
    return observation


async def add_sync_run(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source: DataSource,
    status: SyncStatus = SyncStatus.COMPLETED,
    started_at: datetime = YESTERDAY,
    rows_created: int = 2,
) -> SyncRun:
    run = SyncRun(
        organization_id=organization_id,
        source_id=source.id,
        status=status.value,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=1),
        rows_seen=rows_created,
        rows_created=rows_created,
        rows_skipped=0,
        idempotency_key=uuid.uuid4().hex,
    )
    db.add(run)
    await db.flush()
    return run


async def add_conflict(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity: Entity,
    severity: str = "unspecified",
    status: str = "open",
    detected_at: datetime = YESTERDAY,
) -> Conflict:
    conflict = Conflict(
        organization_id=organization_id,
        entity_id=entity.id,
        attribute="quantity",
        conflict_type="value_conflict",
        severity=severity,
        status=status,
        score=None,
        fingerprint=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        details={},
        summary="Sources disagree.",
        detected_at=detected_at,
        last_seen_at=detected_at,
    )
    db.add(conflict)
    await db.flush()
    return conflict


async def add_entity(db: AsyncSession, *, organization_id: uuid.UUID) -> Entity:
    entity = Entity(
        organization_id=organization_id,
        entity_type="asset",
        natural_key=f"A-{uuid.uuid4().hex[:8]}",
    )
    db.add(entity)
    await db.flush()
    return entity


# --- Empty state -----------------------------------------------------------


async def test_a_new_workspace_reports_empty_rather_than_zeroes(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Nothing connected is a different situation from connected-but-quiet.

    The interface shows onboarding for the first and real zeroes for the
    second, so the distinction has to survive into the response.
    """
    account = await register(client)

    dashboard = await build_dashboard(db, organization_id=account.organization_id, now=NOW)

    assert dashboard.is_empty is True
    assert dashboard.sources.total == 0
    assert dashboard.ingestion.observation_count == 0
    # Registration itself is real activity, so the feed is not empty — it
    # carries the workspace creation and nothing else.
    assert [item.summary for item in dashboard.activity] == ["Created the workspace"]


async def test_a_connected_but_quiet_workspace_is_not_empty(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    await add_source(db, organization_id=account.organization_id, name="Warehouse")

    dashboard = await build_dashboard(db, organization_id=account.organization_id, now=NOW)

    assert dashboard.is_empty is False
    assert dashboard.sources.total == 1


# --- Source health ---------------------------------------------------------


async def test_source_health_counts_each_status_separately(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    org = account.organization_id
    await add_source(db, organization_id=org, name="A connected", status=SourceStatus.CONNECTED)
    await add_source(db, organization_id=org, name="B untested", status=SourceStatus.CONFIGURED)
    await add_source(
        db,
        organization_id=org,
        name="C broken",
        status=SourceStatus.ERROR,
        last_error="The database refused the connection.",
    )

    summary = (await build_dashboard(db, organization_id=org, now=NOW)).sources

    assert summary.total == 3
    assert summary.connected == 1
    assert summary.never_tested == 1
    assert summary.errored == 1
    assert summary.needs_attention == 1


async def test_never_tested_is_not_reported_as_unhealthy(
    client: AsyncClient, db: AsyncSession
) -> None:
    """ "We have not checked" is not "it is broken"."""
    account = await register(client)
    await add_source(
        db,
        organization_id=account.organization_id,
        name="Untested",
        status=SourceStatus.CONFIGURED,
    )

    summary = (await build_dashboard(db, organization_id=account.organization_id, now=NOW)).sources

    assert summary.never_tested == 1
    assert summary.errored == 0
    assert summary.sources[0].has_never_been_tested is True
    assert summary.sources[0].is_healthy is False


async def test_source_health_carries_the_real_error_message(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    await add_source(
        db,
        organization_id=account.organization_id,
        name="Broken",
        status=SourceStatus.ERROR,
        last_error="The database refused the connection.",
    )

    summary = (await build_dashboard(db, organization_id=account.organization_id, now=NOW)).sources

    assert summary.sources[0].last_error == "The database refused the connection."
    assert summary.sources[0].last_error_at is not None


async def test_per_source_counts_come_from_real_rows(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    stream = await add_stream(db, organization_id=org, source=source)
    for index in range(3):
        await add_observation(
            db,
            organization_id=org,
            source=source,
            stream=stream,
            external_id=f"id={index}",
        )

    summary = (await build_dashboard(db, organization_id=org, now=NOW)).sources

    assert summary.sources[0].stream_count == 1
    assert summary.sources[0].observation_count == 3


# --- Ingestion -------------------------------------------------------------


async def test_ingestion_counts_are_real(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    stream = await add_stream(db, organization_id=org, source=source)
    await add_stream(db, organization_id=org, source=source, enabled=False)
    await add_observation(db, organization_id=org, source=source, stream=stream)
    await add_sync_run(db, organization_id=org, source=source)

    ingestion = (await build_dashboard(db, organization_id=org, now=NOW)).ingestion

    assert ingestion.observation_count == 1
    assert ingestion.stream_count == 2
    assert ingestion.enabled_stream_count == 1
    assert ingestion.syncs_in_window == 1
    assert ingestion.last_sync_at is not None


async def test_the_window_excludes_older_activity(client: AsyncClient, db: AsyncSession) -> None:
    """A seven-day window must not count a month-old sync."""
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    stream = await add_stream(db, organization_id=org, source=source)
    await add_observation(
        db, organization_id=org, source=source, stream=stream, ingested_at=LAST_MONTH
    )
    await add_sync_run(db, organization_id=org, source=source, started_at=LAST_MONTH)

    ingestion = (await build_dashboard(db, organization_id=org, now=NOW)).ingestion

    assert ingestion.observation_count == 1  # total is all time
    assert ingestion.observations_in_window == 0  # but the window is empty
    assert ingestion.syncs_in_window == 0


async def test_failed_syncs_are_counted_separately(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    await add_sync_run(db, organization_id=org, source=source, status=SyncStatus.COMPLETED)
    await add_sync_run(db, organization_id=org, source=source, status=SyncStatus.FAILED)

    ingestion = (await build_dashboard(db, organization_id=org, now=NOW)).ingestion

    assert ingestion.syncs_in_window == 2
    assert ingestion.failed_syncs_in_window == 1


async def test_unmapped_entities_are_reported(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    stream = await add_stream(db, organization_id=org, source=source)
    mapped = await add_entity(db, organization_id=org)
    await add_entity(db, organization_id=org)  # never mapped
    db.add(
        EntityMapping(
            organization_id=org,
            entity_id=mapped.id,
            stream_id=stream.id,
            external_id="id=1",
        )
    )
    await db.flush()

    ingestion = (await build_dashboard(db, organization_id=org, now=NOW)).ingestion

    assert ingestion.entity_count == 2
    assert ingestion.mapped_entity_count == 1
    assert ingestion.unmapped_entity_count == 1


# --- Conflicts -------------------------------------------------------------


async def test_conflicts_are_counted_by_status(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    org = account.organization_id
    entity = await add_entity(db, organization_id=org)
    await add_conflict(db, organization_id=org, entity=entity, status="open")
    await add_conflict(db, organization_id=org, entity=entity, status="acknowledged")
    await add_conflict(db, organization_id=org, entity=entity, status="resolved")

    conflicts = (await build_dashboard(db, organization_id=org, now=NOW)).conflicts

    assert conflicts.open == 1
    assert conflicts.acknowledged == 1
    assert conflicts.resolved == 1
    assert conflicts.outstanding == 2


async def test_ungraded_conflicts_are_not_folded_into_low_severity(
    client: AsyncClient, db: AsyncSession
) -> None:
    """An absent judgement must not be displayed as a mild one.

    Every conflict is ungraded while the confidence specification is missing.
    Counting them as "low" would tell an operator the disagreements are minor,
    which nothing has established.
    """
    account = await register(client)
    org = account.organization_id
    entity = await add_entity(db, organization_id=org)
    await add_conflict(db, organization_id=org, entity=entity, severity="unspecified")
    await add_conflict(db, organization_id=org, entity=entity, severity="unspecified")

    conflicts = (await build_dashboard(db, organization_id=org, now=NOW)).conflicts

    assert conflicts.ungraded == 2
    assert "unspecified" not in conflicts.by_severity
    assert conflicts.by_severity.get("low", 0) == 0


async def test_graded_conflicts_appear_in_their_bucket(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    org = account.organization_id
    entity = await add_entity(db, organization_id=org)
    await add_conflict(db, organization_id=org, entity=entity, severity="high")

    conflicts = (await build_dashboard(db, organization_id=org, now=NOW)).conflicts

    assert conflicts.by_severity == {"high": 1}
    assert conflicts.ungraded == 0


async def test_resolved_conflicts_leave_the_severity_buckets(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Severity counts describe outstanding work, not history."""
    account = await register(client)
    org = account.organization_id
    entity = await add_entity(db, organization_id=org)
    await add_conflict(db, organization_id=org, entity=entity, severity="high", status="resolved")

    conflicts = (await build_dashboard(db, organization_id=org, now=NOW)).conflicts

    assert conflicts.resolved == 1
    assert conflicts.by_severity == {}


# --- Confidence: the isolated dependency -----------------------------------


async def test_confidence_reports_unavailable_not_zero(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The line this whole phase turns on.

    A zero would render as a gauge reading "no confidence" — a claim about the
    data. The truth is that nobody has told us how to measure.
    """
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    stream = await add_stream(db, organization_id=org, source=source)
    await add_observation(db, organization_id=org, source=source, stream=stream)

    confidence = (await build_dashboard(db, organization_id=org, now=NOW)).confidence

    assert confidence.available is False
    assert confidence.average_confidence is None
    assert confidence.lowest_confidence is None
    assert confidence.highest_confidence is None
    assert confidence.scored_state_count == 0


async def test_confidence_names_what_is_blocking_it(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)

    confidence = (
        await build_dashboard(db, organization_id=account.organization_id, now=NOW)
    ).confidence

    assert confidence.blocked_reason is not None
    assert "specification" in confidence.blocked_reason
    names = {name for name, _ in confidence.missing_specifications}
    assert "freshness" in names
    assert "conflict_score" in names


async def test_the_algorithm_version_is_reported(client: AsyncClient, db: AsyncSession) -> None:
    """So a state produced without the specification is identifiable."""
    account = await register(client)

    confidence = (
        await build_dashboard(db, organization_id=account.organization_id, now=NOW)
    ).confidence

    assert "unspecified" in confidence.algorithm_version


# --- Activity --------------------------------------------------------------


async def test_activity_merges_real_events_from_several_tables(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    entity = await add_entity(db, organization_id=org)
    await add_sync_run(db, organization_id=org, source=source)
    await add_conflict(db, organization_id=org, entity=entity)

    items = await recent_activity(db, organization_id=org, since=NOW - timedelta(days=7))

    kinds = {item.kind for item in items}
    assert "sync" in kinds
    assert "conflict" in kinds
    # Registration wrote audit rows for the workspace creation.
    assert "audit" in kinds


async def test_activity_is_newest_first_and_stable(client: AsyncClient, db: AsyncSession) -> None:
    """Equal timestamps must not reorder between calls."""
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    for _ in range(3):
        await add_sync_run(db, organization_id=org, source=source, started_at=YESTERDAY)

    first = await recent_activity(db, organization_id=org, since=NOW - timedelta(days=7))
    second = await recent_activity(db, organization_id=org, since=NOW - timedelta(days=7))

    times = [item.occurred_at for item in first]
    assert times == sorted(times, reverse=True)
    assert [i.summary for i in first] == [i.summary for i in second]


async def test_a_failed_sync_is_flagged_in_the_feed(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    org = account.organization_id
    source = await add_source(db, organization_id=org, name="Warehouse")
    await add_sync_run(db, organization_id=org, source=source, status=SyncStatus.FAILED)

    items = await recent_activity(db, organization_id=org, since=NOW - timedelta(days=7))
    failure = next(i for i in items if i.kind == "sync")

    assert failure.severity == "error"
    assert "failed" in failure.summary.lower()


async def test_the_feed_excludes_security_events(client: AsyncClient, db: AsyncSession) -> None:
    """A dashboard is not the place to advertise failed logins.

    The activity feed reads an allowlist of audit actions, so authentication
    events stay in the audit trail where they belong.
    """
    account = await register(client)
    await client.post(
        "/api/auth/login",
        json={"email": account.email, "password": "definitely-not-the-password"},
    )
    await db.commit()

    items = await recent_activity(
        db, organization_id=account.organization_id, since=NOW - timedelta(days=7)
    )

    assert all("login" not in item.summary.lower() for item in items)


# --- API -------------------------------------------------------------------


async def test_dashboard_endpoint_returns_the_full_shape(client: AsyncClient) -> None:
    await register(client)

    response = await client.get("/api/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "organization_id",
        "generated_at",
        "window_days",
        "is_empty",
        "sources",
        "ingestion",
        "conflicts",
        "confidence",
        "activity",
    }
    assert body["confidence"]["available"] is False
    assert body["confidence"]["average_confidence"] is None


async def test_activity_endpoint_works_standalone(client: AsyncClient) -> None:
    await register(client)

    response = await client.get("/api/activity")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_the_window_is_bounded(client: AsyncClient) -> None:
    """An unbounded window would scan every row a workspace has ever made."""
    await register(client)

    assert (await client.get("/api/dashboard", params={"window_days": 0})).status_code == 422
    assert (await client.get("/api/dashboard", params={"window_days": 400})).status_code == 422
    assert (await client.get("/api/dashboard", params={"window_days": 30})).status_code == 200


async def test_the_activity_limit_is_bounded(client: AsyncClient) -> None:
    await register(client)

    assert (await client.get("/api/activity", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/activity", params={"limit": 5000})).status_code == 422


# --- Authentication and tenancy --------------------------------------------


@pytest.mark.parametrize("path", ["/api/dashboard", "/api/activity"])
async def test_anonymous_callers_are_rejected(anonymous_client: AsyncClient, path: str) -> None:
    assert (await anonymous_client.get(path)).status_code == 401


async def test_the_dashboard_never_counts_another_organizations_data(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """The decisive isolation test for an aggregate endpoint.

    Aggregates are where a missing tenant filter does the most damage: it does
    not leak a row, it silently inflates a number, which is far harder to spot.
    """
    alice = await register(client, organization_name="Alice Metrics")
    bob = await register(anonymous_client, organization_name="Bob Metrics")

    source = await add_source(db, organization_id=alice.organization_id, name="Alice WH")
    stream = await add_stream(db, organization_id=alice.organization_id, source=source)
    for index in range(5):
        await add_observation(
            db,
            organization_id=alice.organization_id,
            source=source,
            stream=stream,
            external_id=f"id={index}",
        )
    entity = await add_entity(db, organization_id=alice.organization_id)
    await add_conflict(db, organization_id=alice.organization_id, entity=entity)
    await db.commit()

    alice_dash = (await client.get("/api/dashboard")).json()
    bob_dash = (await anonymous_client.get("/api/dashboard")).json()

    assert alice_dash["ingestion"]["observation_count"] == 5
    assert alice_dash["sources"]["total"] == 1
    assert alice_dash["conflicts"]["open"] == 1

    # Bob sees his own workspace, and none of Alice's numbers.
    assert bob_dash["ingestion"]["observation_count"] == 0
    assert bob_dash["sources"]["total"] == 0
    assert bob_dash["conflicts"]["open"] == 0
    assert bob_dash["is_empty"] is True
    assert bob_dash["organization_id"] == str(bob.organization_id)


async def test_activity_never_crosses_an_organization(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    alice = await register(client, organization_name="Alice Activity")
    await register(anonymous_client, organization_name="Bob Activity")

    source = await add_source(db, organization_id=alice.organization_id, name="Alice WH")
    await add_sync_run(db, organization_id=alice.organization_id, source=source)
    await db.commit()

    bob_activity = (await anonymous_client.get("/api/activity")).json()

    assert all(item["kind"] != "sync" for item in bob_activity)
