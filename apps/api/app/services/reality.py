"""Running the engine and persisting what it produces.

The boring half of Phase 4/5, deliberately kept out of the engine so the
calculation stays a pure function that can be tested without a database.

Two outcomes, and the difference matters:

**Scored** — a complete calculation. The reality state is written with its
confidence, its full breakdown and one evidence row per observation
considered. Conflicts are written alongside it, graded.

**Unscored** — the confidence specification is unavailable. The state is
**still written**, with ``confidence`` NULL and the reason recorded, because
everything except the score follows from the observations alone: which values
were asserted, which were superseded, which failed validation, whether the
sources agree, and — when they all agree — what the value is.

Phase 5 wrote nothing in this case, on the reasoning that a reality state is a
claim with a confidence attached. The cost of that was concrete:
``reality_states`` stayed empty in every deployment, and the selection,
evidence and provenance that need no formula were unreachable. Phase 9 narrows
the withholding to the part that is actually missing. A NULL confidence with a
stated reason is not an unfalsifiable assertion — it is an accurate one.

What is still withheld: when two or more values compete, no winner is chosen.
Ranking them *is* the missing formula, so the state is CONTESTED with a NULL
value and every candidate recorded as evidence.

Recalculation is idempotent: states are replaced wholesale, conflicts are
matched on their fingerprint and updated in place. Running the engine twice
over unchanged observations leaves the database in the same state.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.engine.conflicts import UNSPECIFIED_SEVERITY
from app.engine.engine import calculate
from app.engine.selection import numeric_divergence
from app.engine.spec import (
    ALGORITHM_VERSION,
    UNAVAILABLE_SPECIFICATION,
    ConfidenceSpecification,
)
from app.engine.types import (
    ConflictFinding,
    ObservationInput,
    RealityCalculation,
    SourceAuthority,
)
from app.models.conflict import Conflict, ConflictStatus
from app.models.data_source import DataSource
from app.models.observation import Observation
from app.models.reality_state import EvidenceRole, RealityState, RealityStateEvidence
from app.services.entities import load_observations_for_entity

logger = get_logger(__name__)

#: Attributes present in payloads but describing the row rather than the thing.
#: Excluded from reality state so an entity does not acquire a "shipment_id"
#: belief that is really just the join key.
_STRUCTURAL_KEYS: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RecalculationResult:
    """What one recalculation did."""

    entity_id: uuid.UUID
    attributes_considered: int
    states_written: int
    conflicts_written: int
    #: Attributes whose state was written without a confidence score, with the
    #: specification each is blocked on. Reported rather than counted, so an
    #: operator can see *which* attributes are affected.
    unscored: tuple[tuple[str, str], ...]
    calculated_at: datetime
    #: Wall-clock duration, for the completion log. Not an engine input.
    duration_ms: int = 0

    @property
    def is_fully_unscored(self) -> bool:
        return bool(self.unscored) and len(self.unscored) == self.attributes_considered


def to_engine_input(
    observation: Observation, *, attribute: str, authority: SourceAuthority
) -> ObservationInput:
    """Project an ORM row onto the engine's flat input type.

    Reliability and quality come from the source's declared configuration, not
    from anything inferred about the data. A source that agrees with the
    majority is not thereby more reliable — it may simply be copying from the
    same upstream.

    Neither is configurable yet, so both are ``None``: undeclared. Not a
    midpoint, not a default — a number here would be an invented reliability
    value, and it would become load-bearing the moment the scoring
    specification arrived.
    """
    return ObservationInput(
        observation_id=observation.id,
        source_id=observation.source_id,
        stream_id=observation.stream_id,
        external_id=observation.external_id,
        value=observation.payload.get(attribute),
        event_time=observation.event_time,
        ingested_at=observation.ingested_at,
        event_time_semantics=observation.event_time_semantics,
        authority=authority,
        reliability=None,
        quality=None,
        validation_passed=True,
    )


def attributes_in(observations: list[Observation]) -> tuple[str, ...]:
    """Every attribute any observation states, in a stable order."""
    names: set[str] = set()
    for observation in observations:
        names.update(k for k in observation.payload if k not in _STRUCTURAL_KEYS)
    return tuple(sorted(names))


async def recalculate_entity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    as_of: datetime | None = None,
    specification: ConfidenceSpecification = UNAVAILABLE_SPECIFICATION,
) -> RecalculationResult:
    """Recompute every attribute of one entity from its observations.

    ``as_of`` defaults to now, but is an explicit parameter so a caller can
    reproduce a historical calculation exactly. The engine itself never reads a
    clock.
    """
    as_of = as_of or datetime.now(UTC)
    observations = await load_observations_for_entity(
        db, organization_id=organization_id, entity_id=entity_id
    )

    authorities = await _source_authorities(db, organization_id=organization_id)
    attributes = attributes_in(observations)

    started = time.perf_counter()
    logger.info(
        "reality.recalculation_started",
        entity_id=str(entity_id),
        organization_id=str(organization_id),
        observations=len(observations),
        attributes=len(attributes),
        as_of=as_of.isoformat(),
    )

    states_written = 0
    conflicts_written = 0
    unscored: list[tuple[str, str]] = []

    for attribute in attributes:
        inputs = tuple(
            to_engine_input(
                observation,
                attribute=attribute,
                authority=authorities.get(observation.source_id, SourceAuthority.SECONDARY),
            )
            for observation in observations
            if attribute in observation.payload
        )

        outcome = calculate(
            attribute=attribute,
            observations=inputs,
            as_of=as_of,
            specification=specification,
        )

        if outcome.confidence_unavailable is not None:
            unscored.append((attribute, outcome.confidence_unavailable.missing))

        state = await _persist_state(
            db,
            organization_id=organization_id,
            entity_id=entity_id,
            calculation=outcome,
        )
        states_written += 1
        conflicts_written += await _persist_conflicts(
            db,
            organization_id=organization_id,
            entity_id=entity_id,
            attribute=attribute,
            findings=outcome.conflicts,
            reality_state_id=state.id,
            as_of=as_of,
        )

        logger.debug(
            "reality.attribute_calculated",
            entity_id=str(entity_id),
            attribute=attribute,
            candidates=len(outcome.candidates),
            # The selected value itself is deliberately absent from the log.
            # An attribute payload is customer data, and a log sink is the one
            # place it has no reason to be.
            value_selected=outcome.value_selected,
            status=outcome.status.value,
            scored=outcome.is_scored,
            evidence=len(outcome.evidence),
            conflicts=len(outcome.conflicts),
        )

    await db.flush()
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "reality.recalculation_completed",
        entity_id=str(entity_id),
        organization_id=str(organization_id),
        observations=len(observations),
        attributes=len(attributes),
        states_written=states_written,
        conflicts_written=conflicts_written,
        unscored=len(unscored),
        duration_ms=duration_ms,
    )

    return RecalculationResult(
        entity_id=entity_id,
        attributes_considered=len(attributes),
        states_written=states_written,
        conflicts_written=conflicts_written,
        unscored=tuple(unscored),
        calculated_at=as_of,
        duration_ms=duration_ms,
    )


async def _source_authorities(
    db: AsyncSession, *, organization_id: uuid.UUID
) -> dict[uuid.UUID, SourceAuthority]:
    """Declared authority per source.

    Not yet configurable through the API, so every source reads as SECONDARY.
    Recorded here as the single place that changes when authority becomes a
    source setting, rather than being scattered through the engine.
    """
    rows = await db.scalars(select(DataSource).where(DataSource.organization_id == organization_id))
    return {source.id: SourceAuthority.SECONDARY for source in rows}


async def _persist_state(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    calculation: RealityCalculation,
) -> RealityState:
    """Write a scored state, replacing any previous one wholesale.

    Replace rather than update: a reality state is a derived snapshot, and
    merging a new calculation into an old row would leave evidence from two
    different runs side by side with no way to tell them apart.
    """
    await db.execute(
        delete(RealityState).where(
            RealityState.organization_id == organization_id,
            RealityState.entity_id == entity_id,
            RealityState.attribute == calculation.attribute,
        )
    )

    confidence = calculation.confidence
    absence = calculation.confidence_unavailable

    state = RealityState(
        organization_id=organization_id,
        entity_id=entity_id,
        attribute=calculation.attribute,
        value=calculation.value,
        # NULL, never 0.0. The breakdown carries the reason, so a consumer
        # reading a null is told why rather than left to guess whether the
        # score is missing, broken, or genuinely zero.
        confidence=confidence.score if confidence is not None else None,
        status=calculation.status.value,
        confidence_breakdown=(
            confidence.as_dict()
            if confidence is not None
            else (absence.as_dict() if absence is not None else {"available": False})
        ),
        value_selected=calculation.value_selected,
        selection_reason=calculation.selection_reason,
        valid_from=calculation.valid_from,
        calculated_at=calculation.calculated_as_of,
        algorithm_version=(
            confidence.algorithm_version if confidence is not None else ALGORITHM_VERSION
        ),
        supporting_count=calculation.supporting_count,
        dissenting_count=calculation.dissenting_count,
        source_count=calculation.source_count,
    )
    db.add(state)
    await db.flush()

    for entry in calculation.evidence:
        db.add(
            RealityStateEvidence(
                organization_id=organization_id,
                reality_state_id=state.id,
                observation_id=entry.observation.observation_id,
                role=entry.role.value,
                weight=entry.weight,
                observed_value=entry.observation.value,
                exclusion_reason=entry.exclusion_reason,
            )
        )

    return state


async def _persist_conflicts(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    attribute: str,
    findings: tuple[ConflictFinding, ...],
    reality_state_id: uuid.UUID | None,
    as_of: datetime,
) -> int:
    """Upsert conflicts by fingerprint.

    Matched on the fingerprint so re-running the engine over unchanged evidence
    updates ``last_seen_at`` rather than accumulating a duplicate row per
    calculation. A resolved conflict that reappears is deliberately *not*
    reopened here — whether it is genuinely back is a human judgement, and
    silently reopening would make the resolution meaningless.
    """
    written = 0
    for finding in findings:
        existing = await db.scalar(
            select(Conflict).where(
                Conflict.organization_id == organization_id,
                Conflict.entity_id == entity_id,
                Conflict.attribute == attribute,
                Conflict.conflict_type == finding.conflict_type,
                Conflict.fingerprint == finding.fingerprint,
            )
        )

        if existing is not None:
            existing.last_seen_at = as_of
            existing.score = finding.score
            existing.severity = finding.severity
            existing.details = finding.details
            existing.summary = finding.summary
            if reality_state_id is not None:
                existing.reality_state_id = reality_state_id
            continue

        db.add(
            Conflict(
                organization_id=organization_id,
                entity_id=entity_id,
                reality_state_id=reality_state_id,
                attribute=attribute,
                conflict_type=finding.conflict_type,
                severity=finding.severity or UNSPECIFIED_SEVERITY,
                status=ConflictStatus.OPEN.value,
                score=finding.score,
                fingerprint=finding.fingerprint,
                details=finding.details,
                summary=finding.summary,
                detected_at=as_of,
                last_seen_at=as_of,
            )
        )
        written += 1

    return written


async def detection_for_entity(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    attribute: str,
    as_of: datetime | None = None,
    specification: ConfidenceSpecification = UNAVAILABLE_SPECIFICATION,
) -> RealityCalculation | None:
    """What the evidence shows for one attribute, scored or not.

    Used by the read APIs so a caller gets the most that can honestly be said.
    Returns None only when the entity has no observation carrying the
    attribute at all — a genuine absence rather than an unscored state.
    """
    as_of = as_of or datetime.now(UTC)
    observations = await load_observations_for_entity(
        db, organization_id=organization_id, entity_id=entity_id
    )
    if not observations:
        return None

    authorities = await _source_authorities(db, organization_id=organization_id)
    inputs = tuple(
        to_engine_input(
            observation,
            attribute=attribute,
            authority=authorities.get(observation.source_id, SourceAuthority.SECONDARY),
        )
        for observation in observations
        if attribute in observation.payload
    )
    if not inputs:
        return None

    return calculate(
        attribute=attribute, observations=inputs, as_of=as_of, specification=specification
    )


def detection_as_dict(calculation: RealityCalculation) -> dict[str, Any]:
    """Render an unscored calculation in the Phase 5 detection shape.

    The ``/unscored`` endpoint is a Phase 5 contract and keeps its response
    shape. Only its source changed: the same facts now come from the reality
    calculation rather than from a separate detection-only path, so there is
    one derivation of "which values were asserted" instead of two that could
    drift apart.
    """
    divergence = numeric_divergence(calculation.candidates)
    return {
        "attribute": calculation.attribute,
        "scored": False,
        "disagreement": len(calculation.candidates) > 1,
        "divergence": str(divergence) if divergence is not None else None,
        "distinct_values": [
            {
                "value": candidate.value,
                "observation_count": len(candidate.observations),
                "sources": [str(s) for s in candidate.source_ids],
            }
            for candidate in calculation.candidates
        ],
        "excluded": [
            {
                "observation_id": str(entry.observation.observation_id),
                "reason": entry.exclusion_reason or "excluded",
            }
            for entry in calculation.evidence
            if entry.role is EvidenceRole.EXCLUDED
        ],
    }


@dataclass(frozen=True, slots=True)
class HistoricalAttribute:
    """One attribute as it stood at a chosen moment."""

    attribute: str
    status: str
    value: Any
    value_selected: bool
    confidence: Decimal | None
    confidence_available: bool
    selection_reason: str
    supporting_count: int
    dissenting_count: int
    source_count: int
    candidate_count: int


@dataclass(frozen=True, slots=True)
class HistoricalReality:
    """What RealitySync would have said about an entity at ``known_at``."""

    entity_id: uuid.UUID
    known_at: datetime
    #: Records that existed by then. The reason a past answer can differ from
    #: today's even though no source changed its mind: some of them had simply
    #: not arrived yet.
    observations_known: int
    #: Records that exist now but had not been ingested by ``known_at``.
    observations_since: int
    attributes: tuple[HistoricalAttribute, ...]


async def reality_as_of(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
    known_at: datetime,
    specification: ConfidenceSpecification = UNAVAILABLE_SPECIFICATION,
) -> HistoricalReality:
    """Recompute an entity as it stood when we only knew what we knew then.

    **Writes nothing.** A time-travel query that persisted its result would
    overwrite the present with the past, which is a spectacular way to lose
    the current state — so this returns the calculation and stops. It is a
    ``GET`` for that reason.

    Two cutoffs are in play and they are not the same one. ``known_at`` is
    passed to the loader as the *ingestion* cutoff, so the engine sees only
    what had arrived by then; it is also passed to the engine as ``as_of``, so
    ages and freshness are measured from that moment rather than from now.
    Using today's clock over yesterday's records would produce an answer that
    never existed.
    """
    observations = await load_observations_for_entity(
        db,
        organization_id=organization_id,
        entity_id=entity_id,
        known_at=known_at,
    )
    total_now = len(
        await load_observations_for_entity(db, organization_id=organization_id, entity_id=entity_id)
    )

    authorities = await _source_authorities(db, organization_id=organization_id)

    results: list[HistoricalAttribute] = []
    for attribute in attributes_in(observations):
        inputs = tuple(
            to_engine_input(
                observation,
                attribute=attribute,
                authority=authorities.get(observation.source_id, SourceAuthority.SECONDARY),
            )
            for observation in observations
            if attribute in observation.payload
        )
        outcome = calculate(
            attribute=attribute,
            observations=inputs,
            as_of=known_at,
            specification=specification,
        )
        results.append(
            HistoricalAttribute(
                attribute=attribute,
                status=outcome.status.value,
                value=outcome.value,
                value_selected=outcome.value_selected,
                # `.score`, not the wrapper: a NULL here must mean "no score
                # exists", never "the object was missing an attribute".
                confidence=(outcome.confidence.score if outcome.confidence is not None else None),
                confidence_available=outcome.confidence is not None,
                selection_reason=outcome.selection_reason,
                supporting_count=outcome.supporting_count,
                dissenting_count=outcome.dissenting_count,
                source_count=outcome.source_count,
                candidate_count=len(outcome.candidates),
            )
        )

    logger.info(
        "reality.as_of_queried",
        entity_id=str(entity_id),
        organization_id=str(organization_id),
        known_at=known_at.isoformat(),
        observations_known=len(observations),
        observations_since=total_now - len(observations),
    )

    return HistoricalReality(
        entity_id=entity_id,
        known_at=known_at,
        observations_known=len(observations),
        observations_since=total_now - len(observations),
        attributes=tuple(results),
    )
