"""Phase 5 API: entities, conflicts, timeline.

Real accounts created through registration, real observations, real HTTP.
Conflicts here are produced by the engine's detection path — which works while
the confidence specification is missing, because detecting disagreement needs
no formula.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_source import DataSource
from app.models.observation import Observation
from app.models.source_stream import SourceStream
from tests.factories import register

pytestmark = pytest.mark.integration

MONDAY = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


async def seed_source(
    db: AsyncSession, *, organization_id: uuid.UUID, name: str
) -> tuple[DataSource, SourceStream]:
    source = DataSource(
        organization_id=organization_id,
        name=name,
        kind="postgresql",
        config={"host": "db.example.com", "port": 5432, "database": "d", "username": "u"},
    )
    db.add(source)
    await db.flush()

    stream = SourceStream(
        organization_id=organization_id,
        data_source_id=source.id,
        schema_name="public",
        table_name=f"t_{uuid.uuid4().hex[:8]}",
        primary_key_columns=["id"],
        event_time_column="updated_at",
        event_time_semantics="observed",
    )
    db.add(stream)
    await db.flush()
    return source, stream


async def seed_observation(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    source: DataSource,
    stream: SourceStream,
    payload: dict[str, Any],
    event_time: datetime = MONDAY,
    ingested_at: datetime | None = None,
    external_id: str = "id=1",
) -> Observation:
    observation = Observation(
        organization_id=organization_id,
        source_id=source.id,
        stream_id=stream.id,
        external_id=external_id,
        payload=payload,
        event_time=event_time,
        ingested_at=ingested_at or event_time,
        event_time_semantics="observed",
        fingerprint=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        provenance={},
    )
    db.add(observation)
    await db.flush()
    return observation


async def create_entity(client: AsyncClient, account: Any, *, key: str = "LAPTOP-TEST") -> str:
    response = await client.post(
        "/api/entities",
        json={"entity_type": "asset", "natural_key": key},
        headers=account.auth_headers(),
    )
    assert response.status_code == 201, response.text
    entity_id: str = response.json()["id"]
    return entity_id


async def map_row(
    client: AsyncClient,
    account: Any,
    *,
    entity_id: str,
    stream: SourceStream,
    external_id: str = "id=1",
) -> None:
    response = await client.post(
        f"/api/entities/{entity_id}/mappings",
        json={"stream_id": str(stream.id), "external_id": external_id},
        headers=account.auth_headers(),
    )
    assert response.status_code == 201, response.text


# --- Entities --------------------------------------------------------------


async def test_create_and_list_entities(client: AsyncClient) -> None:
    account = await register(client)
    entity_id = await create_entity(client, account, key="LAPTOP-001")

    listing = await client.get("/api/entities")

    assert listing.status_code == 200
    assert [e["natural_key"] for e in listing.json()] == ["LAPTOP-001"]
    assert listing.json()[0]["id"] == entity_id


async def test_duplicate_natural_key_is_refused(client: AsyncClient) -> None:
    account = await register(client)
    await create_entity(client, account, key="LAPTOP-001")

    response = await client.post(
        "/api/entities",
        json={"entity_type": "asset", "natural_key": "LAPTOP-001"},
        headers=account.auth_headers(),
    )

    assert response.status_code == 409


async def test_mapping_is_retroactive_over_existing_observations(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Observations already ingested resolve immediately, with no re-sync."""
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="WH")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)

    before = await client.get(f"/api/entities/{entity_id}/timeline")
    assert before.json()["event_count"] == 0

    await map_row(client, account, entity_id=entity_id, stream=stream)

    after = await client.get(f"/api/entities/{entity_id}/timeline")
    assert after.json()["event_count"] == 1


async def test_mapping_a_row_twice_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    _, stream = await seed_source(db, organization_id=account.organization_id, name="WH")
    await db.commit()
    entity_id = await create_entity(client, account)

    await map_row(client, account, entity_id=entity_id, stream=stream)
    response = await client.post(
        f"/api/entities/{entity_id}/mappings",
        json={"stream_id": str(stream.id), "external_id": "id=1"},
        headers=account.auth_headers(),
    )

    assert response.status_code == 409


# --- Timeline --------------------------------------------------------------


async def test_timeline_reports_both_axes(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="WH")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY + timedelta(days=4),
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    response = await client.get(f"/api/entities/{entity_id}/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["axis"] == "event"
    assert body["late_arrival_count"] == 1
    event = body["events"][0]
    assert event["arrived_late"] is True
    assert event["event_time"] != event["ingested_at"]


async def test_timeline_as_of_knowledge_time_excludes_a_late_arrival(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="WH")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY + timedelta(days=4),
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    wednesday = (MONDAY + timedelta(days=2)).isoformat()
    response = await client.get(
        f"/api/entities/{entity_id}/timeline",
        params={"axis": "knowledge", "as_of_knowledge_time": wednesday},
    )

    assert response.json()["event_count"] == 0


async def test_timeline_rejects_an_unknown_axis(client: AsyncClient) -> None:
    account = await register(client)
    entity_id = await create_entity(client, account)

    response = await client.get(f"/api/entities/{entity_id}/timeline", params={"axis": "wishful"})

    assert response.status_code == 422


# --- Conflicts -------------------------------------------------------------


async def test_recalculate_detects_conflicts_while_scoring_is_blocked(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The Phase 5 payoff, with the Phase 9 correction.

    CHANGED IN PHASE 9. This previously asserted ``states_written == 0``: with
    scoring blocked, Phase 5 wrote no reality state at all. That behaviour was
    obsolete rather than wrong. Withholding the *score* is right; withholding
    the selection, evidence and provenance that need no formula was not, and it
    left ``reality_states`` empty in every deployment — the Reality page looked
    identical to an empty workspace.

    A state is now written per attribute with ``confidence`` NULL. What is
    still withheld is the *selection*: two sources disagree, ranking them
    requires the missing weights, so the state is CONTESTED with no value.

    The property this test exists for is unchanged: disagreement is found and
    recorded without any formula, and nothing is scored.
    """
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")

    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 42},
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=erp,
        stream=erp_stream,
        payload={"quantity": 57},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)

    response = await client.post(
        f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["blocked"] is True
    assert body["states_written"] == 1
    assert body["states_unscored"] == 1
    assert body["conflicts_written"] >= 1
    assert "freshness" in body["blocked_on"]
    assert any(m["name"] == "conflict_score" for m in body["missing_specifications"])

    # The state exists, says what it knows, and scores nothing.
    states = await client.get(f"/api/entities/{entity_id}/reality", headers=account.auth_headers())
    written = states.json()
    assert len(written) == 1
    assert written[0]["attribute"] == "quantity"
    assert written[0]["status"] == "contested"
    assert written[0]["confidence"] is None
    assert written[0]["confidence_available"] is False
    assert written[0]["value_selected"] is False
    assert written[0]["value"] is None


async def test_a_detected_conflict_carries_the_facts_not_a_grade(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 42},
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=erp,
        stream=erp_stream,
        payload={"quantity": 57},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    conflicts = (await client.get("/api/conflicts")).json()
    value_conflict = next(c for c in conflicts if c["conflict_type"] == "value_conflict")

    # Facts, established without any formula.
    assert value_conflict["details"]["divergence"] == "15"
    assert {v["value"] for v in value_conflict["details"]["competing_values"]} == {42, 57}
    # Grading, which needs the missing specification.
    assert value_conflict["score"] is None
    assert value_conflict["severity"] == "unspecified"


async def test_recalculation_is_idempotent(client: AsyncClient, db: AsyncSession) -> None:
    """Re-running updates in place rather than accumulating duplicates."""
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 42},
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=erp,
        stream=erp_stream,
        payload={"quantity": 57},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)

    for _ in range(3):
        await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    conflicts = (await client.get("/api/conflicts")).json()
    fingerprints = [c["id"] for c in conflicts]
    assert len(fingerprints) == len(set(fingerprints))
    # One value_conflict and one source_disagreement, not three of each.
    assert len([c for c in conflicts if c["conflict_type"] == "value_conflict"]) == 1


async def test_agreeing_sources_produce_no_conflict(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    for source, stream in ((warehouse, wh_stream), (erp, erp_stream)):
        await seed_observation(
            db,
            organization_id=account.organization_id,
            source=source,
            stream=stream,
            payload={"quantity": 42},
        )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    assert (await client.get("/api/conflicts")).json() == []


# --- Conflict lifecycle ----------------------------------------------------


async def _one_conflict(client: AsyncClient, db: AsyncSession, account: Any) -> dict[str, Any]:
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 42},
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=erp,
        stream=erp_stream,
        payload={"quantity": 57},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())
    conflict: dict[str, Any] = (await client.get("/api/conflicts")).json()[0]
    return conflict


@pytest.mark.parametrize("target", ["acknowledged", "resolved", "dismissed"])
async def test_a_conflict_can_be_moved_through_its_lifecycle(
    client: AsyncClient, db: AsyncSession, target: str
) -> None:
    account = await register(client)
    conflict = await _one_conflict(client, db, account)

    response = await client.patch(
        f"/api/conflicts/{conflict['id']}",
        json={"status": target, "note": "Checked with the warehouse team."},
        headers=account.auth_headers(),
    )

    assert response.status_code == 200
    assert response.json()["status"] == target
    assert response.json()["resolution_note"] == "Checked with the warehouse team."


async def test_resolving_records_who_and_when(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    conflict = await _one_conflict(client, db, account)

    resolved = await client.patch(
        f"/api/conflicts/{conflict['id']}",
        json={"status": "resolved"},
        headers=account.auth_headers(),
    )

    assert resolved.json()["resolved_at"] is not None


async def test_acknowledging_does_not_mark_it_resolved(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    conflict = await _one_conflict(client, db, account)

    acknowledged = await client.patch(
        f"/api/conflicts/{conflict['id']}",
        json={"status": "acknowledged"},
        headers=account.auth_headers(),
    )

    assert acknowledged.json()["resolved_at"] is None


async def test_resolving_a_conflict_does_not_alter_reality_state(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The one-way dependency, asserted through the API.

    Resolution is a human annotation. It must not feed back into what the
    engine believes, or the state would depend on the order conflicts were
    processed.
    """
    account = await register(client)
    conflict = await _one_conflict(client, db, account)
    entity_id = conflict["entity_id"]

    before = (await client.get(f"/api/entities/{entity_id}/reality")).json()
    await client.patch(
        f"/api/conflicts/{conflict['id']}",
        json={"status": "resolved"},
        headers=account.auth_headers(),
    )
    after = (await client.get(f"/api/entities/{entity_id}/reality")).json()

    assert before == after


async def test_a_resolved_conflict_is_not_silently_reopened(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Reopening is a human judgement, not something recalculation decides."""
    account = await register(client)
    conflict = await _one_conflict(client, db, account)
    entity_id = conflict["entity_id"]

    await client.patch(
        f"/api/conflicts/{conflict['id']}",
        json={"status": "resolved"},
        headers=account.auth_headers(),
    )
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    refreshed = (await client.get(f"/api/conflicts/{conflict['id']}")).json()
    assert refreshed["status"] == "resolved"


async def test_conflicts_can_be_filtered_by_status(client: AsyncClient, db: AsyncSession) -> None:
    account = await register(client)
    conflict = await _one_conflict(client, db, account)

    await client.patch(
        f"/api/conflicts/{conflict['id']}",
        json={"status": "resolved"},
        headers=account.auth_headers(),
    )

    open_conflicts = (await client.get("/api/conflicts", params={"status": "open"})).json()
    resolved = (await client.get("/api/conflicts", params={"status": "resolved"})).json()

    assert conflict["id"] not in [c["id"] for c in open_conflicts]
    assert conflict["id"] in [c["id"] for c in resolved]


# --- Unscored attribute ----------------------------------------------------


async def test_unscored_endpoint_reports_values_without_a_verdict(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The honest fallback: what the sources say, with no winner named."""
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 42},
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=erp,
        stream=erp_stream,
        payload={"quantity": 57},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)

    response = await client.get(f"/api/entities/{entity_id}/attributes/quantity/unscored")

    assert response.status_code == 200
    body = response.json()
    assert body["scored"] is False
    assert body["disagreement"] is True
    assert body["divergence"] == "15"
    assert {v["value"] for v in body["distinct_values"]} == {42, 57}
    # No verdict anywhere in the payload.
    assert "value" not in body
    assert "confidence" not in body


# --- Authentication and tenancy --------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/entities"),
        ("POST", "/api/entities"),
        ("GET", "/api/conflicts"),
        ("GET", "/api/entities/{id}"),
        ("GET", "/api/entities/{id}/timeline"),
        ("GET", "/api/entities/{id}/reality"),
        ("POST", "/api/entities/{id}/recalculate"),
        ("DELETE", "/api/entities/{id}"),
    ],
)
async def test_every_route_rejects_anonymous_callers(
    client: AsyncClient, anonymous_client: AsyncClient, method: str, path: str
) -> None:
    account = await register(client)
    entity_id = await create_entity(client, account)

    response = await anonymous_client.request(method, path.format(id=entity_id), json={})

    assert response.status_code == 401


async def test_an_entity_is_invisible_to_another_organization(
    client: AsyncClient, anonymous_client: AsyncClient
) -> None:
    alice = await register(client, organization_name="Alice Assets")
    await register(anonymous_client, organization_name="Bob Assets")

    entity_id = await create_entity(client, alice, key="ALICE-001")

    assert (await anonymous_client.get("/api/entities")).json() == []
    assert (await anonymous_client.get(f"/api/entities/{entity_id}")).status_code == 404


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", ""),
        ("GET", "/timeline"),
        ("GET", "/reality"),
        ("GET", "/mappings"),
        ("POST", "/recalculate"),
        ("DELETE", ""),
    ],
)
async def test_no_operation_reaches_another_organizations_entity(
    client: AsyncClient, anonymous_client: AsyncClient, method: str, suffix: str
) -> None:
    alice = await register(client)
    bob = await register(anonymous_client)
    entity_id = await create_entity(client, alice)

    response = await anonymous_client.request(
        method,
        f"/api/entities/{entity_id}{suffix}",
        json={} if method == "POST" else None,
        headers=bob.auth_headers(),
    )

    assert response.status_code == 404


async def test_conflicts_never_cross_an_organization(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    alice = await register(client, organization_name="Alice Conflicts")
    bob = await register(anonymous_client, organization_name="Bob Conflicts")

    await _one_conflict(client, db, alice)

    assert (await anonymous_client.get("/api/conflicts")).json() == []
    assert len((await client.get("/api/conflicts")).json()) >= 1
    # And Bob cannot reach Alice's conflict by id.
    alice_conflict = (await client.get("/api/conflicts")).json()[0]
    denied = await anonymous_client.get(f"/api/conflicts/{alice_conflict['id']}")
    assert denied.status_code == 404
    denied_patch = await anonymous_client.patch(
        f"/api/conflicts/{alice_conflict['id']}",
        json={"status": "dismissed"},
        headers=bob.auth_headers(),
    )
    assert denied_patch.status_code == 404


async def test_state_changing_routes_require_a_csrf_token(client: AsyncClient) -> None:
    await register(client)

    response = await client.post(
        "/api/entities", json={"entity_type": "asset", "natural_key": "NO-CSRF"}
    )

    assert response.status_code == 403


async def test_a_viewer_cannot_create_an_entity(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    from app.models.membership import Membership

    owner = await register(client, organization_name="Role Org")
    viewer = await register(anonymous_client)
    db.add(Membership(user_id=viewer.user_id, organization_id=owner.organization_id, role="viewer"))
    await db.commit()

    viewer_csrf = anonymous_client.cookies.get("rs_csrf") or ""
    await anonymous_client.post(
        "/api/auth/organization",
        json={"organization_id": str(owner.organization_id)},
        headers={"X-CSRF-Token": viewer_csrf},
    )

    response = await anonymous_client.post(
        "/api/entities",
        json={"entity_type": "asset", "natural_key": "VIEWER-001"},
        headers={"X-CSRF-Token": viewer_csrf},
    )

    assert response.status_code == 403
