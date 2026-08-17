"""Phase 9 — the reality state as a persisted, explainable, reproducible thing.

Phase 4 proved the engine calculates. Phase 5 proved it detects disagreement.
Neither proved that a reality state *survives* — because none was ever written.
This module covers the part that was missing: what lands in the database, what
it says about itself, and whether running the engine again changes it.

Three properties are load-bearing here, and each has a way of looking fine
while being broken:

**Determinism.** Same observations and same ``as_of`` must give the same state,
including the same evidence in the same order. A dict iteration order or an
unstable sort passes every casual test and then reorders on an unrelated
change.

**Bitemporal correctness.** Event time decides what is true; ingestion time
only breaks ties between statements about the same instant. Getting this
backwards makes a late-arriving backfill look like the newest truth — the
classic pipeline bug, and invisible until someone checks a historical value.

**Nothing invented.** Confidence stays absent, and when several values compete
no winner is chosen. Both are easy to "fix" with a plausible default that
nobody would ever notice was fabricated.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conflict import Conflict
from app.models.reality_state import EvidenceRole, RealityState, RealityStateEvidence, RealityStatus
from app.services.reality import recalculate_entity
from tests.factories import register
from tests.test_reality_api import (
    create_entity,
    map_row,
    seed_observation,
    seed_source,
)

MONDAY = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)
FRIDAY = MONDAY + timedelta(days=4)

#: Fixed, so nothing in this module depends on the wall clock. The engine takes
#: `as_of` as an input precisely so a calculation is reproducible.
AS_OF = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


async def states_for(db: AsyncSession, organization_id: uuid.UUID) -> list[RealityState]:
    rows = await db.scalars(
        select(RealityState)
        .where(RealityState.organization_id == organization_id)
        .order_by(RealityState.attribute)
    )
    return list(rows)


async def evidence_for(db: AsyncSession, state: RealityState) -> list[RealityStateEvidence]:
    rows = await db.scalars(
        select(RealityStateEvidence)
        .where(
            RealityStateEvidence.organization_id == state.organization_id,
            RealityStateEvidence.reality_state_id == state.id,
        )
        .order_by(RealityStateEvidence.observation_id)
    )
    return list(rows)


def snapshot(state: RealityState) -> tuple[Any, ...]:
    """Everything about a state that must not drift between identical runs."""
    return (
        state.attribute,
        state.value,
        state.value_selected,
        state.confidence,
        state.status,
        state.selection_reason,
        state.valid_from,
        state.algorithm_version,
        state.supporting_count,
        state.dissenting_count,
        state.source_count,
    )


# --- A state now exists ------------------------------------------------------


async def test_an_agreed_value_is_persisted_without_a_score(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The core Phase 9 change.

    Phase 5 wrote nothing here, so ``reality_states`` was empty in every
    deployment and the Reality page was indistinguishable from an empty
    workspace. The value follows from the observations alone — every source
    says 42 — so withholding it was withholding a fact, not a guess. Only the
    score is withheld now.
    """
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
            event_time=MONDAY,
        )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)

    result = await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    assert result.states_written == 1
    (state,) = await states_for(db, account.organization_id)

    assert state.attribute == "quantity"
    assert state.value == 42
    assert state.value_selected is True
    assert state.status == RealityStatus.CONFIRMED.value
    # The whole point: a real value, and no fabricated number beside it.
    assert state.confidence is None
    assert state.confidence_breakdown["available"] is False
    assert state.confidence_breakdown["reason"] == "specification_unavailable"
    assert state.source_count == 2


async def test_competing_values_select_nothing(client: AsyncClient, db: AsyncSession) -> None:
    """Ranking *is* the missing formula, so no winner is chosen.

    Returning the alphabetically-first candidate would produce a state
    indistinguishable from a real verdict, which is the failure mode this whole
    approach exists to avoid.
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

    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)

    assert state.status == RealityStatus.CONTESTED.value
    assert state.value is None
    assert state.value_selected is False
    assert state.confidence is None
    # The reason must say a value was withheld and why — in whatever words the
    # engine currently uses. Both halves matter: "nothing was picked" without a
    # cause reads as a failure, and a cause without the outcome reads as trivia.
    assert "nothing was picked" in state.selection_reason
    assert "no agreed way" in state.selection_reason

    # Both competing values survive as evidence, so nothing is lost by
    # declining to pick one.
    entries = await evidence_for(db, state)
    assert {e.observed_value for e in entries} == {42, 57}
    # Neither is labelled supporting or dissenting: those are defined relative
    # to a selection, and there is none.
    assert {e.role for e in entries} == {EvidenceRole.CONSIDERED.value}


async def test_no_observations_gives_no_state_rather_than_a_guess(
    client: AsyncClient, db: AsyncSession
) -> None:
    """An entity nobody has observed produces nothing, not an UNKNOWN row."""
    account = await register(client)
    entity_id = await create_entity(client, account)

    result = await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    assert result.attributes_considered == 0
    assert result.states_written == 0
    assert await states_for(db, account.organization_id) == []


# --- Determinism and idempotency ---------------------------------------------


async def test_recalculating_twice_produces_an_identical_state(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Same observations, same as_of, same answer — byte for byte."""
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42, "status": "in_transit"},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    async def run() -> list[tuple[Any, ...]]:
        await recalculate_entity(
            db,
            organization_id=account.organization_id,
            entity_id=uuid.UUID(entity_id),
            as_of=AS_OF,
        )
        await db.commit()
        return [snapshot(s) for s in await states_for(db, account.organization_id)]

    first = await run()
    second = await run()

    assert first == second
    assert len(first) == 2, "one state per attribute"


async def test_recalculation_leaves_no_duplicate_states_or_evidence(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Replacement, not accumulation.

    A state is a derived snapshot. Appending a second one per run would leave
    two contradictory beliefs in the table with nothing to say which is
    current, and the evidence rows would multiply with them.
    """
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    for _ in range(3):
        await recalculate_entity(
            db,
            organization_id=account.organization_id,
            entity_id=uuid.UUID(entity_id),
            as_of=AS_OF,
        )
        await db.commit()

    states = await states_for(db, account.organization_id)
    assert len(states) == 1

    evidence_count = await db.scalar(
        select(func.count())
        .select_from(RealityStateEvidence)
        .where(RealityStateEvidence.organization_id == account.organization_id)
    )
    assert evidence_count == 1, "evidence must be replaced with its state, not accumulated"


async def test_evidence_always_matches_the_observations_used(
    client: AsyncClient, db: AsyncSession
) -> None:
    """No orphaned evidence pointing at a previous run's inputs."""
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    first = await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
        event_time=MONDAY,
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)
    assert {e.observation_id for e in await evidence_for(db, state)} == {first.id}

    # A newer observation arrives from the same source.
    second = await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 99},
        event_time=TUESDAY,
    )
    await db.commit()

    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)
    entries = {e.observation_id: e for e in await evidence_for(db, state)}

    # Both are present — the superseded one is evidence of what was looked at,
    # not something to hide — and each carries its actual role.
    assert set(entries) == {first.id, second.id}
    assert entries[second.id].role == EvidenceRole.SUPPORTING.value
    assert entries[first.id].role == EvidenceRole.EXCLUDED.value
    assert entries[first.id].exclusion_reason == "superseded_by_newer_observation_from_same_source"
    assert state.value == 99


# --- Bitemporal correctness ---------------------------------------------------


async def test_a_newer_event_arriving_later_wins(client: AsyncClient, db: AsyncSession) -> None:
    """The ordinary case: newer news, delivered later."""
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")

    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 1},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 2},
        event_time=TUESDAY,
        ingested_at=TUESDAY,
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)
    assert state.value == 2
    assert state.valid_from == TUESDAY


async def test_an_older_event_arriving_later_does_not_win(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The bug this whole design exists to prevent.

    A backfill delivered today describing last Monday must not displace
    Tuesday's reading. Ordering by arrival would make every late correction
    look like the newest truth — and it would look completely normal.
    """
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")

    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 2},
        event_time=TUESDAY,
        ingested_at=TUESDAY,
    )
    # Describes Monday, arrives Friday.
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 1},
        event_time=MONDAY,
        ingested_at=FRIDAY,
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)
    assert state.value == 2, "a late-arriving older event displaced current truth"
    assert state.valid_from == TUESDAY


async def test_the_same_event_time_is_broken_by_ingestion_time(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A correction of the same instant is the newer belief.

    Event time alone is ambiguous when a source restates one moment, so
    ingestion time breaks that tie — and only that tie.
    """
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")

    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 1},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 2},
        event_time=MONDAY,
        ingested_at=WEDNESDAY,
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)
    assert state.value == 2


async def test_ingestion_order_does_not_change_the_outcome(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Out-of-order arrival must reach the same state as in-order arrival.

    Same facts, inserted in the opposite sequence. Any dependence on insertion
    order — a stable sort relying on it, a max() over an unordered set — shows
    up here and nowhere else.
    """
    results = []
    for reverse in (False, True):
        account = await register(client)
        source, stream = await seed_source(
            db, organization_id=account.organization_id, name="Source"
        )

        rows = [
            ({"quantity": 1}, MONDAY, MONDAY),
            ({"quantity": 2}, TUESDAY, FRIDAY),
            ({"quantity": 3}, WEDNESDAY, TUESDAY),
        ]
        for payload, event_time, ingested_at in reversed(rows) if reverse else rows:
            await seed_observation(
                db,
                organization_id=account.organization_id,
                source=source,
                stream=stream,
                payload=payload,
                event_time=event_time,
                ingested_at=ingested_at,
            )
        await db.commit()

        entity_id = await create_entity(client, account)
        await map_row(client, account, entity_id=entity_id, stream=stream)
        await recalculate_entity(
            db,
            organization_id=account.organization_id,
            entity_id=uuid.UUID(entity_id),
            as_of=AS_OF,
        )
        await db.commit()

        (state,) = await states_for(db, account.organization_id)
        results.append((state.value, state.status, state.valid_from))

    assert results[0] == results[1]
    # Wednesday is the latest *event*, regardless of when anything arrived.
    assert results[0][0] == 3


async def test_as_of_is_an_input_not_a_clock_read(client: AsyncClient, db: AsyncSession) -> None:
    """A historical calculation is reproducible.

    The engine never reads a clock, so passing the same `as_of` a year later
    must reproduce the same answer. Without this, "why did this value change"
    would have no answer.
    """
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    snapshots = []
    for as_of in (AS_OF, AS_OF + timedelta(days=365)):
        await recalculate_entity(
            db,
            organization_id=account.organization_id,
            entity_id=uuid.UUID(entity_id),
            as_of=as_of,
        )
        await db.commit()
        (state,) = await states_for(db, account.organization_id)
        snapshots.append((state.value, state.status, state.valid_from, state.calculated_at))

    # The derived belief is identical; only the stamp saying when it was
    # calculated differs.
    assert snapshots[0][:3] == snapshots[1][:3]
    assert snapshots[0][3] != snapshots[1][3]


async def test_a_late_arrival_is_incorporated_on_recalculation(
    client: AsyncClient, db: AsyncSession
) -> None:
    """New evidence changes the state; the old state does not linger."""
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)
    assert state.status == RealityStatus.CONFIRMED.value
    assert state.value == 42

    # A second source turns up and disagrees.
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=erp,
        stream=erp_stream,
        payload={"quantity": 57},
    )
    await db.commit()
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)

    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    (state,) = await states_for(db, account.organization_id)
    assert state.status == RealityStatus.CONTESTED.value
    assert state.value_selected is False, "the previous confirmed value must not persist"


# --- Provenance ---------------------------------------------------------------


async def test_every_state_is_explainable_from_the_api(
    client: AsyncClient, db: AsyncSession
) -> None:
    """An API consumer can answer "why does it say that" without the database."""
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    kept = await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 42},
        event_time=TUESDAY,
        ingested_at=WEDNESDAY,
    )
    superseded = await seed_observation(
        db,
        organization_id=account.organization_id,
        source=warehouse,
        stream=wh_stream,
        payload={"quantity": 7},
        event_time=MONDAY,
        ingested_at=MONDAY,
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    states = (
        await client.get(f"/api/entities/{entity_id}/reality", headers=account.auth_headers())
    ).json()
    assert len(states) == 1
    state = states[0]

    # The claim, and its honesty about what it does not have.
    assert state["value"] == 42
    assert state["value_selected"] is True
    assert state["confidence"] is None
    assert state["confidence_available"] is False
    assert state["algorithm_version"]
    assert state["selection_reason"]
    assert state["valid_from"]

    evidence = (
        await client.get(
            f"/api/entities/{entity_id}/reality/quantity/evidence",
            headers=account.auth_headers(),
        )
    ).json()

    by_id = {e["observation_id"]: e for e in evidence}
    assert set(by_id) == {str(kept.id), str(superseded.id)}

    # Which observation supported it, and where it came from.
    supporting = by_id[str(kept.id)]
    assert supporting["role"] == "supporting"
    assert supporting["observed_value"] == 42
    assert supporting["source_id"] == str(warehouse.id)
    assert supporting["event_time"] == TUESDAY.isoformat().replace("+00:00", "Z")
    assert supporting["ingested_at"] == WEDNESDAY.isoformat().replace("+00:00", "Z")

    # Which was set aside, and why — not silently dropped.
    excluded = by_id[str(superseded.id)]
    assert excluded["role"] == "excluded"
    assert excluded["exclusion_reason"] == "superseded_by_newer_observation_from_same_source"
    assert excluded["observed_value"] == 7


async def test_the_breakdown_says_what_is_missing_not_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A null confidence must come with a reason a person can act on."""
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    state = (
        await client.get(f"/api/entities/{entity_id}/reality", headers=account.auth_headers())
    ).json()[0]

    breakdown = state["confidence_breakdown"]
    assert breakdown["available"] is False
    assert breakdown["blocked_on"]
    assert breakdown["detail"]
    names = {entry["name"] for entry in breakdown["missing_specifications"]}
    assert "freshness" in names
    assert "conflict_score" in names


# --- Conflict integration -----------------------------------------------------


async def test_conflict_history_survives_recalculation(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Recalculating replaces the state; it must not erase the conflict record.

    A conflict is something a person may have acknowledged or resolved.
    Rebuilding a derived snapshot must not discard that work.
    """
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    for source, stream, quantity in ((warehouse, wh_stream, 42), (erp, erp_stream, 57)):
        await seed_observation(
            db,
            organization_id=account.organization_id,
            source=source,
            stream=stream,
            payload={"quantity": quantity},
        )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)

    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())
    conflicts = (await client.get("/api/conflicts", headers=account.auth_headers())).json()
    assert conflicts
    conflict_id = conflicts[0]["id"]
    detected_at = conflicts[0]["detected_at"]

    await client.patch(
        f"/api/conflicts/{conflict_id}",
        json={"status": "acknowledged"},
        headers=account.auth_headers(),
    )

    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    after = (await client.get("/api/conflicts", headers=account.auth_headers())).json()
    assert len(after) == len(conflicts), "recalculation created a duplicate conflict"
    assert after[0]["id"] == conflict_id
    assert after[0]["status"] == "acknowledged", "a human decision was overwritten"
    assert after[0]["detected_at"] == detected_at
    # Still ungraded: the conflict-score formula remains unavailable.
    assert after[0]["score"] is None
    assert after[0]["severity"] == "unspecified"


async def test_conflicts_do_not_influence_the_selected_value(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Resolving a conflict must not silently change what the system believes.

    If it could, the state would depend on the order conflicts happened to be
    processed in, and two identical deployments could disagree.
    """
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    for source, stream, quantity in ((warehouse, wh_stream, 42), (erp, erp_stream, 57)):
        await seed_observation(
            db,
            organization_id=account.organization_id,
            source=source,
            stream=stream,
            payload={"quantity": quantity},
        )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    before = (
        await client.get(f"/api/entities/{entity_id}/reality", headers=account.auth_headers())
    ).json()

    conflicts = (await client.get("/api/conflicts", headers=account.auth_headers())).json()
    await client.patch(
        f"/api/conflicts/{conflicts[0]['id']}",
        json={"status": "resolved", "resolution_note": "ERP is authoritative here"},
        headers=account.auth_headers(),
    )
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    after = (
        await client.get(f"/api/entities/{entity_id}/reality", headers=account.auth_headers())
    ).json()

    assert before[0]["value"] == after[0]["value"]
    assert before[0]["status"] == after[0]["status"]
    assert before[0]["value_selected"] == after[0]["value_selected"]


# --- Tenant isolation ---------------------------------------------------------


async def test_recalculation_cannot_reach_another_organizations_observations(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """Two tenants, identical entity keys, and no leakage between them."""
    first = await register(client)
    second = await register(anonymous_client)

    a_source, a_stream = await seed_source(db, organization_id=first.organization_id, name="A")
    b_source, b_stream = await seed_source(db, organization_id=second.organization_id, name="B")
    await seed_observation(
        db,
        organization_id=first.organization_id,
        source=a_source,
        stream=a_stream,
        payload={"quantity": 1},
    )
    await seed_observation(
        db,
        organization_id=second.organization_id,
        source=b_source,
        stream=b_stream,
        payload={"quantity": 2},
    )
    await db.commit()

    a_entity = await create_entity(client, first, key="SHARED-KEY")
    b_entity = await create_entity(anonymous_client, second, key="SHARED-KEY")
    await map_row(client, first, entity_id=a_entity, stream=a_stream)
    await map_row(anonymous_client, second, entity_id=b_entity, stream=b_stream)

    await recalculate_entity(
        db,
        organization_id=first.organization_id,
        entity_id=uuid.UUID(a_entity),
        as_of=AS_OF,
    )
    await recalculate_entity(
        db,
        organization_id=second.organization_id,
        entity_id=uuid.UUID(b_entity),
        as_of=AS_OF,
    )
    await db.commit()

    a_states = await states_for(db, first.organization_id)
    b_states = await states_for(db, second.organization_id)

    assert [s.value for s in a_states] == [1]
    assert [s.value for s in b_states] == [2]
    assert {s.organization_id for s in a_states} == {first.organization_id}
    assert {s.organization_id for s in b_states} == {second.organization_id}


async def test_states_evidence_and_conflicts_are_invisible_across_tenants(
    client: AsyncClient, anonymous_client: AsyncClient, db: AsyncSession
) -> None:
    """Every derived artefact is scoped, not only the entity."""
    first = await register(client)
    second = await register(anonymous_client)

    source, stream = await seed_source(db, organization_id=first.organization_id, name="A")
    erp, erp_stream = await seed_source(db, organization_id=first.organization_id, name="A2")
    for src, strm, quantity in ((source, stream, 42), (erp, erp_stream, 57)):
        await seed_observation(
            db,
            organization_id=first.organization_id,
            source=src,
            stream=strm,
            payload={"quantity": quantity},
        )
    await db.commit()

    entity_id = await create_entity(client, first)
    await map_row(client, first, entity_id=entity_id, stream=stream)
    await map_row(client, first, entity_id=entity_id, stream=erp_stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=first.auth_headers())

    # The other tenant sees nothing, and gets 404 rather than 403 — the
    # resource does not exist as far as they are concerned.
    for path in (
        f"/api/entities/{entity_id}",
        f"/api/entities/{entity_id}/reality",
        f"/api/entities/{entity_id}/reality/quantity/evidence",
    ):
        response = await anonymous_client.get(path, headers=second.auth_headers())
        assert response.status_code == 404, path

    recalc = await anonymous_client.post(
        f"/api/entities/{entity_id}/recalculate", headers=second.auth_headers()
    )
    assert recalc.status_code == 404

    assert (
        await anonymous_client.get("/api/conflicts", headers=second.auth_headers())
    ).json() == []


# --- API behaviour ------------------------------------------------------------


async def test_states_are_returned_in_a_stable_order(client: AsyncClient, db: AsyncSession) -> None:
    """Ordering must not depend on insertion or on the planner's mood."""
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"zebra": 1, "alpha": 2, "middle": 3},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)
    await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())

    orders = []
    for _ in range(3):
        states = (
            await client.get(f"/api/entities/{entity_id}/reality", headers=account.auth_headers())
        ).json()
        orders.append([s["attribute"] for s in states])

    assert orders[0] == orders[1] == orders[2]
    assert orders[0] == ["alpha", "middle", "zebra"]


async def test_evidence_for_an_unknown_attribute_is_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    entity_id = await create_entity(client, account)

    response = await client.get(
        f"/api/entities/{entity_id}/reality/nonexistent/evidence",
        headers=account.auth_headers(),
    )
    assert response.status_code == 404


async def test_recalculation_reports_what_it_could_not_score(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The response names the affected attributes, not just a count."""
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42, "status": "in_transit"},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    body = (
        await client.post(f"/api/entities/{entity_id}/recalculate", headers=account.auth_headers())
    ).json()

    assert body["states_written"] == 2
    assert body["states_unscored"] == 2
    assert {row["attribute"] for row in body["unscored_attributes"]} == {"quantity", "status"}
    assert all(row["blocked_on"] for row in body["unscored_attributes"])


# --- Dashboard integration ------------------------------------------------------


async def test_the_dashboard_does_not_report_confidence_as_available(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The regression Phase 9 could have introduced.

    The confidence panel counted every reality state as a scored one, which was
    true until states began to be written with a NULL confidence. Left alone it
    would have reported ``available: true`` alongside a null average — a panel
    claiming to show confidence while showing none, which is precisely the
    fabricated metric it exists to prevent.
    """
    from app.services.dashboard import build_dashboard

    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    # A state now exists...
    assert len(await states_for(db, account.organization_id)) == 1

    dashboard = await build_dashboard(db, organization_id=account.organization_id)

    # ...and confidence is still, correctly, unavailable.
    assert dashboard.confidence.available is False
    assert dashboard.confidence.scored_state_count == 0
    assert dashboard.confidence.average_confidence is None
    assert dashboard.confidence.blocked_reason


# --- Failure behaviour ---------------------------------------------------------


async def test_an_unmapped_entity_yields_no_state(client: AsyncClient, db: AsyncSession) -> None:
    """Observations exist, but none belong to this entity.

    A missing mapping must produce nothing rather than a state derived from
    whatever happened to be nearby.
    """
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    # Deliberately no map_row call.

    result = await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    assert result.states_written == 0
    assert await states_for(db, account.organization_id) == []


async def test_an_attribute_missing_from_one_source_is_not_treated_as_null(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Silence is not an assertion.

    A source that never mentions an attribute has not claimed it is null, and
    counting it as a competing "null" candidate would manufacture a conflict
    out of an absence.
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
        payload={"quantity": 42, "location": "A1"},
    )
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=erp,
        stream=erp_stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()

    states = {s.attribute: s for s in await states_for(db, account.organization_id)}

    assert states["location"].value == "A1"
    assert states["location"].status == RealityStatus.CONFIRMED.value
    assert states["location"].source_count == 1, "a silent source was counted as asserting null"


async def test_a_failed_recalculation_leaves_nothing_half_written(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The state and its evidence commit together or not at all.

    Evidence written without its state would be unreachable rows; a state
    written without evidence would be an unexplainable claim, which is the one
    thing this table must never contain.
    """
    account = await register(client)
    source, stream = await seed_source(db, organization_id=account.organization_id, name="Source")
    await seed_observation(
        db,
        organization_id=account.organization_id,
        source=source,
        stream=stream,
        payload={"quantity": 42},
    )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=stream)

    savepoint = await db.begin_nested()
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await savepoint.rollback()

    assert await states_for(db, account.organization_id) == []
    orphans = await db.scalar(
        select(func.count())
        .select_from(RealityStateEvidence)
        .where(RealityStateEvidence.organization_id == account.organization_id)
    )
    assert orphans == 0

    # And the entity is still recalculable afterwards.
    await recalculate_entity(
        db,
        organization_id=account.organization_id,
        entity_id=uuid.UUID(entity_id),
        as_of=AS_OF,
    )
    await db.commit()
    assert len(await states_for(db, account.organization_id)) == 1


async def test_conflicts_are_not_duplicated_by_repeated_detection(
    client: AsyncClient, db: AsyncSession
) -> None:
    account = await register(client)
    warehouse, wh_stream = await seed_source(
        db, organization_id=account.organization_id, name="Warehouse"
    )
    erp, erp_stream = await seed_source(db, organization_id=account.organization_id, name="ERP")
    for src, strm, quantity in ((warehouse, wh_stream, 42), (erp, erp_stream, 57)):
        await seed_observation(
            db,
            organization_id=account.organization_id,
            source=src,
            stream=strm,
            payload={"quantity": quantity},
        )
    await db.commit()

    entity_id = await create_entity(client, account)
    await map_row(client, account, entity_id=entity_id, stream=wh_stream)
    await map_row(client, account, entity_id=entity_id, stream=erp_stream)

    counts = []
    for _ in range(3):
        await recalculate_entity(
            db,
            organization_id=account.organization_id,
            entity_id=uuid.UUID(entity_id),
            as_of=AS_OF,
        )
        await db.commit()
        counts.append(
            await db.scalar(
                select(func.count())
                .select_from(Conflict)
                .where(Conflict.organization_id == account.organization_id)
            )
        )

    assert counts[0] == counts[1] == counts[2]
    assert counts[0] >= 1
