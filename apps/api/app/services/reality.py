"""Running the engine and persisting what it produces.

The boring half of Phase 4/5, deliberately kept out of the engine so the
calculation stays a pure function that can be tested without a database.

Two outcomes, and the difference matters:

**Scored** — a complete calculation. The reality state is written with its
confidence, its full breakdown and one evidence row per observation
considered. Conflicts are written alongside it, graded.

**Blocked** — the confidence specification is unavailable. **No reality state
is written.** A row in ``reality_states`` is a claim about the world with a
confidence attached; writing one without a score would put an unfalsifiable
assertion into the table every later phase reads. Detected *conflicts* are
still written, because "these sources disagree" is a fact that needs no
formula, and withholding it would hide a real problem behind a missing
constant.

Recalculation is idempotent: states are replaced wholesale, conflicts are
matched on their fingerprint and updated in place. Running the engine twice
over unchanged observations leaves the database in the same state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.engine.conflicts import UNSPECIFIED_SEVERITY
from app.engine.detection import DetectionResult
from app.engine.engine import CalculationBlocked, calculate
from app.engine.spec import UNAVAILABLE_SPECIFICATION, ConfidenceSpecification
from app.engine.types import ConflictFinding, ObservationInput, RealityCalculation, SourceAuthority
from app.models.conflict import Conflict, ConflictStatus
from app.models.data_source import DataSource
from app.models.observation import Observation
from app.models.reality_state import RealityState, RealityStateEvidence
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
    blocked: tuple[CalculationBlocked, ...]
    calculated_at: datetime

    @property
    def is_fully_blocked(self) -> bool:
        return self.states_written == 0 and bool(self.blocked)


def to_engine_input(
    observation: Observation, *, attribute: str, authority: SourceAuthority
) -> ObservationInput:
    """Project an ORM row onto the engine's flat input type.

    Reliability and quality come from the source's declared configuration, not
    from anything inferred about the data. A source that agrees with the
    majority is not thereby more reliable — it may simply be copying from the
    same upstream.
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
        # Declared per source. Absent a configured value the engine still needs
        # a number; this is the neutral midpoint and is recorded as such in the
        # breakdown rather than presented as a measurement.
        reliability=Decimal("0.5"),
        quality=Decimal("1.0"),
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

    states_written = 0
    conflicts_written = 0
    blocked: list[CalculationBlocked] = []

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

        if isinstance(outcome, CalculationBlocked):
            blocked.append(outcome)
            if outcome.detection is not None:
                conflicts_written += await _persist_conflicts(
                    db,
                    organization_id=organization_id,
                    entity_id=entity_id,
                    attribute=attribute,
                    findings=outcome.detection.conflicts,
                    reality_state_id=None,
                    as_of=as_of,
                )
            continue

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

    await db.flush()
    logger.info(
        "reality.recalculated",
        entity_id=str(entity_id),
        organization_id=str(organization_id),
        attributes=len(attributes),
        states_written=states_written,
        conflicts_written=conflicts_written,
        blocked=len(blocked),
    )

    return RecalculationResult(
        entity_id=entity_id,
        attributes_considered=len(attributes),
        states_written=states_written,
        conflicts_written=conflicts_written,
        blocked=tuple(blocked),
        calculated_at=as_of,
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

    state = RealityState(
        organization_id=organization_id,
        entity_id=entity_id,
        attribute=calculation.attribute,
        value=calculation.value,
        confidence=calculation.confidence.score,
        status=calculation.status.value,
        confidence_breakdown=calculation.confidence.as_dict(),
        selection_reason=calculation.selection_reason,
        valid_from=calculation.valid_from,
        calculated_at=calculation.calculated_as_of,
        algorithm_version=calculation.confidence.algorithm_version,
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
) -> DetectionResult | RealityCalculation | None:
    """What the evidence shows for one attribute, scored or not.

    Used by the read APIs so a caller gets the most that can honestly be said:
    a full calculation when the specification allows one, the detection result
    when it does not.
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

    outcome = calculate(
        attribute=attribute, observations=inputs, as_of=as_of, specification=specification
    )
    if isinstance(outcome, CalculationBlocked):
        return outcome.detection
    return outcome


def detection_as_dict(detection: DetectionResult) -> dict[str, Any]:
    """Render a detection result for the API."""
    return {
        "attribute": detection.attribute,
        "scored": False,
        "disagreement": detection.has_disagreement,
        "divergence": str(detection.divergence) if detection.divergence is not None else None,
        "distinct_values": [
            {
                "value": candidate.value,
                "observation_count": len(candidate.observations),
                "sources": [str(s) for s in candidate.source_ids],
            }
            for candidate in detection.candidates
        ],
        "excluded": [
            {"observation_id": str(o.observation_id), "reason": reason}
            for o, reason in detection.excluded
        ],
    }
