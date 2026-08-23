"""Timeline — bitemporal reconstruction.

The feature that makes the two time axes pay for themselves. Every test here
runs against real observations produced by the Phase 3 connector path, and none
of it depends on the missing confidence specification.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_source import DataSource
from app.models.entity import Entity, EntityMapping
from app.models.observation import Observation
from app.models.organization import Organization
from app.models.source_stream import SourceStream
from app.services.timeline import TimeAxis, attribute_history, reconstruct

pytestmark = pytest.mark.integration

MONDAY = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
TUESDAY = MONDAY + timedelta(days=1)
FRIDAY = MONDAY + timedelta(days=4)


async def build_entity(db: AsyncSession, name: str = "Timeline Org") -> tuple[Organization, Entity]:
    organization = Organization(name=name, slug=f"tl-{uuid.uuid4().hex[:10]}")
    db.add(organization)
    await db.flush()

    entity = Entity(
        organization_id=organization.id,
        entity_type="asset",
        natural_key=f"ASSET-{uuid.uuid4().hex[:6]}",
    )
    db.add(entity)
    await db.flush()
    return organization, entity


async def build_stream(
    db: AsyncSession, organization: Organization, *, name: str
) -> tuple[DataSource, SourceStream]:
    source = DataSource(
        organization_id=organization.id,
        name=name,
        kind="postgresql",
        config={"host": "db.example.com", "port": 5432, "database": "d", "username": "u"},
    )
    db.add(source)
    await db.flush()

    stream = SourceStream(
        organization_id=organization.id,
        data_source_id=source.id,
        schema_name="public",
        table_name="assets",
        primary_key_columns=["id"],
        event_time_column="updated_at",
        event_time_semantics="observed",
    )
    db.add(stream)
    await db.flush()
    return source, stream


async def observe(
    db: AsyncSession,
    *,
    organization: Organization,
    source: DataSource,
    stream: SourceStream,
    external_id: str,
    payload: dict[str, object],
    event_time: datetime,
    ingested_at: datetime,
) -> Observation:
    observation = Observation(
        organization_id=organization.id,
        source_id=source.id,
        stream_id=stream.id,
        external_id=external_id,
        payload=payload,
        event_time=event_time,
        ingested_at=ingested_at,
        event_time_semantics="observed",
        fingerprint=uuid.uuid4().hex + uuid.uuid4().hex[:32],
        provenance={},
    )
    db.add(observation)
    await db.flush()
    return observation


async def map_row(
    db: AsyncSession,
    *,
    organization: Organization,
    entity: Entity,
    stream: SourceStream,
    external_id: str,
) -> None:
    db.add(
        EntityMapping(
            organization_id=organization.id,
            entity_id=entity.id,
            stream_id=stream.id,
            external_id=external_id,
        )
    )
    await db.flush()


# --- The two axes ----------------------------------------------------------


async def test_event_time_and_knowledge_time_give_different_answers(
    db: AsyncSession,
) -> None:
    """The reason both axes exist.

    A reading taken Monday but delivered Friday is old news we just heard. At
    knowledge-time Wednesday we did not have it; at event-time Wednesday it was
    already true. A single-axis system cannot express that difference.
    """
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        # Delivered four days late.
        ingested_at=FRIDAY,
    )

    wednesday = MONDAY + timedelta(days=2)

    by_event = await reconstruct(
        db,
        organization_id=organization.id,
        entity_id=entity.id,
        axis=TimeAxis.EVENT,
        as_of_event_time=wednesday,
    )
    by_knowledge = await reconstruct(
        db,
        organization_id=organization.id,
        entity_id=entity.id,
        axis=TimeAxis.KNOWLEDGE,
        as_of_knowledge_time=wednesday,
    )

    # It was true on Wednesday...
    assert len(by_event.events) == 1
    # ...but we did not know it yet.
    assert len(by_knowledge.events) == 0


async def test_late_arrival_is_flagged_rather_than_left_to_inference(
    db: AsyncSession,
) -> None:
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=FRIDAY,
    )
    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 43},
        event_time=TUESDAY,
        ingested_at=TUESDAY,
    )

    timeline = await reconstruct(db, organization_id=organization.id, entity_id=entity.id)

    late = [e for e in timeline.events if e.arrived_late]
    assert timeline.late_arrival_count == 1
    assert late[0].lag_seconds == 4 * 24 * 3600


async def test_combining_both_axes_reconstructs_a_past_belief(db: AsyncSession) -> None:
    """ "What did we believe on Wednesday about the world on Tuesday?"

    The question an audit asks after a decision turns out wrong.
    """
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    # A correction to Tuesday's world, but it only arrived on Friday.
    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 99},
        event_time=TUESDAY,
        ingested_at=FRIDAY,
    )

    wednesday = MONDAY + timedelta(days=2)
    timeline = await reconstruct(
        db,
        organization_id=organization.id,
        entity_id=entity.id,
        as_of_event_time=wednesday,
        as_of_knowledge_time=wednesday,
    )

    values = [e.attribute_values["quantity"] for e in timeline.events]
    assert values == [42]  # the correction had not reached us yet


async def test_ordering_follows_the_requested_axis(db: AsyncSession) -> None:
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 1},
        event_time=MONDAY,
        ingested_at=FRIDAY,  # oldest event, newest arrival
    )
    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 2},
        event_time=TUESDAY,
        ingested_at=TUESDAY,
    )

    by_event = await reconstruct(
        db, organization_id=organization.id, entity_id=entity.id, axis=TimeAxis.EVENT
    )
    by_knowledge = await reconstruct(
        db, organization_id=organization.id, entity_id=entity.id, axis=TimeAxis.KNOWLEDGE
    )

    # Newest first on each axis, and the two disagree about what is newest.
    assert [e.attribute_values["quantity"] for e in by_event.events] == [2, 1]
    assert [e.attribute_values["quantity"] for e in by_knowledge.events] == [1, 2]


# --- Mapping and scoping ---------------------------------------------------


async def test_only_mapped_observations_reach_an_entity(db: AsyncSession) -> None:
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=2",
        payload={"quantity": 7},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    timeline = await reconstruct(db, organization_id=organization.id, entity_id=entity.id)

    assert [e.external_id for e in timeline.events] == ["id=1"]


async def test_mapping_resolves_observations_that_already_existed(
    db: AsyncSession,
) -> None:
    """Retroactive by construction — no re-sync, no observation rewritten."""
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )

    before = await reconstruct(db, organization_id=organization.id, entity_id=entity.id)
    assert len(before.events) == 0

    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    after = await reconstruct(db, organization_id=organization.id, entity_id=entity.id)
    assert len(after.events) == 1


async def test_a_timeline_never_crosses_an_organization(db: AsyncSession) -> None:
    """Tenant isolation, on the read path that spans the most tables."""
    org_a, entity_a = await build_entity(db, "Tenant A")
    org_b, entity_b = await build_entity(db, "Tenant B")

    source_a, stream_a = await build_stream(db, org_a, name="A Warehouse")
    source_b, stream_b = await build_stream(db, org_b, name="B Warehouse")

    await map_row(db, organization=org_a, entity=entity_a, stream=stream_a, external_id="id=1")
    await map_row(db, organization=org_b, entity=entity_b, stream=stream_b, external_id="id=1")

    await observe(
        db,
        organization=org_a,
        source=source_a,
        stream=stream_a,
        external_id="id=1",
        payload={"quantity": 111},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    await observe(
        db,
        organization=org_b,
        source=source_b,
        stream=stream_b,
        external_id="id=1",
        payload={"quantity": 222},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )

    a_timeline = await reconstruct(db, organization_id=org_a.id, entity_id=entity_a.id)
    b_timeline = await reconstruct(db, organization_id=org_b.id, entity_id=entity_b.id)

    assert [e.attribute_values["quantity"] for e in a_timeline.events] == [111]
    assert [e.attribute_values["quantity"] for e in b_timeline.events] == [222]


async def test_another_tenants_entity_id_yields_nothing(db: AsyncSession) -> None:
    """Even with a valid id from elsewhere, the tenant filter holds."""
    org_a, entity_a = await build_entity(db, "Tenant A")
    org_b, _ = await build_entity(db, "Tenant B")

    source_a, stream_a = await build_stream(db, org_a, name="A Warehouse")
    await map_row(db, organization=org_a, entity=entity_a, stream=stream_a, external_id="id=1")
    await observe(
        db,
        organization=org_a,
        source=source_a,
        stream=stream_a,
        external_id="id=1",
        payload={"quantity": 111},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )

    # Tenant B asking about tenant A's entity.
    timeline = await reconstruct(db, organization_id=org_b.id, entity_id=entity_a.id)

    assert timeline.events == ()


# --- Filtering and paging --------------------------------------------------


async def test_filtering_to_one_attribute(db: AsyncSession) -> None:
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42, "status": "in_transit"},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )

    timeline = await reconstruct(
        db, organization_id=organization.id, entity_id=entity.id, attribute="quantity"
    )

    assert timeline.events[0].attribute_values == {"quantity": 42}


async def test_truncation_is_reported_rather_than_silent(db: AsyncSession) -> None:
    """A silently truncated timeline reads as a complete history."""
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    for index in range(5):
        await observe(
            db,
            organization=organization,
            source=source,
            stream=stream,
            external_id="id=1",
            payload={"quantity": index},
            event_time=MONDAY + timedelta(hours=index),
            ingested_at=MONDAY + timedelta(hours=index),
        )

    timeline = await reconstruct(db, organization_id=organization.id, entity_id=entity.id, limit=3)

    assert len(timeline.events) == 3
    assert timeline.truncated is True


async def test_attribute_history_lists_the_values_a_source_stated(
    db: AsyncSession,
) -> None:
    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    for index, value in enumerate([42, 43, 44]):
        await observe(
            db,
            organization=organization,
            source=source,
            stream=stream,
            external_id="id=1",
            payload={"quantity": value},
            event_time=MONDAY + timedelta(hours=index),
            ingested_at=MONDAY + timedelta(hours=index),
        )

    history = await attribute_history(
        db,
        organization_id=organization.id,
        entity_id=entity.id,
        attribute="quantity",
    )

    assert history.distinct_values == (44, 43, 42)  # newest event first


# ---------------------------------------------------------------------------
# Reality as of a past moment
#
# The Timeline already answers "what was true then". This answers "what would
# this system have told you then", which is a different question wherever a
# source reported late.
# ---------------------------------------------------------------------------


async def test_a_past_answer_ignores_records_that_had_not_arrived(
    db: AsyncSession,
) -> None:
    """The whole point: today's answer and Wednesday's answer differ.

    Neither source changed its mind. One of them was simply slow, and asking
    "what did we know on Wednesday" has to reflect that.
    """
    from app.services.reality import reality_as_of

    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    # Describes Tuesday, but did not reach us until Friday.
    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 99},
        event_time=TUESDAY,
        ingested_at=FRIDAY,
    )

    wednesday = MONDAY + timedelta(days=2)
    past = await reality_as_of(
        db,
        organization_id=organization.id,
        entity_id=entity.id,
        known_at=wednesday,
    )

    assert past.observations_known == 1
    # And it says how many arrived afterwards, which is why the answer moved.
    assert past.observations_since == 1

    (quantity,) = [a for a in past.attributes if a.attribute == "quantity"]
    assert quantity.value == 42
    assert quantity.value_selected is True


async def test_the_present_sees_everything_the_past_did_not(db: AsyncSession) -> None:
    from app.services.reality import reality_as_of

    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")

    for payload, event_time, ingested_at in (
        ({"quantity": 42}, MONDAY, MONDAY),
        ({"quantity": 99}, TUESDAY, FRIDAY),
    ):
        await observe(
            db,
            organization=organization,
            source=source,
            stream=stream,
            external_id="id=1",
            payload=payload,
            event_time=event_time,
            ingested_at=ingested_at,
        )

    now = FRIDAY + timedelta(days=1)
    present = await reality_as_of(
        db, organization_id=organization.id, entity_id=entity.id, known_at=now
    )

    assert present.observations_known == 2
    assert present.observations_since == 0
    (quantity,) = [a for a in present.attributes if a.attribute == "quantity"]
    assert quantity.value == 99


async def test_a_past_query_writes_nothing(db: AsyncSession) -> None:
    """Persisting a time-travel result would overwrite the present with the past."""
    from sqlalchemy import func, select

    from app.models.reality_state import RealityState
    from app.services.reality import reality_as_of

    organization, entity = await build_entity(db)
    source, stream = await build_stream(db, organization, name="Warehouse")
    await map_row(db, organization=organization, entity=entity, stream=stream, external_id="id=1")
    await observe(
        db,
        organization=organization,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )

    await reality_as_of(
        db,
        organization_id=organization.id,
        entity_id=entity.id,
        known_at=FRIDAY,
    )

    stored = await db.scalar(
        select(func.count())
        .select_from(RealityState)
        .where(RealityState.organization_id == organization.id)
    )
    assert stored == 0


async def test_a_past_query_cannot_reach_another_tenant(db: AsyncSession) -> None:
    from app.services.reality import reality_as_of

    _, mine = await build_entity(db)
    other_org, theirs = await build_entity(db)
    source, stream = await build_stream(db, other_org, name="Theirs")
    await map_row(db, organization=other_org, entity=theirs, stream=stream, external_id="id=1")
    await observe(
        db,
        organization=other_org,
        source=source,
        stream=stream,
        external_id="id=1",
        payload={"quantity": 42},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )

    # My organization, their entity id: nothing.
    leaked = await reality_as_of(
        db,
        organization_id=mine.organization_id,
        entity_id=theirs.id,
        known_at=FRIDAY,
    )
    assert leaked.attributes == ()
    assert leaked.observations_known == 0
