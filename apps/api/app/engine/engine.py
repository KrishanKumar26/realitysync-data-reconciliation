"""The Reality Engine.

One entry point: :func:`calculate`. Given observations and a point in time it
returns either a complete :class:`~app.engine.types.RealityCalculation` or a
:class:`CalculationBlocked` explaining precisely what is missing.

There is no partial result. A reality state is a claim about the world with a
confidence attached, and half of one is not a weaker claim — it is an
unfalsifiable one. So when the specification is unavailable the engine returns
blocked, persistence writes nothing, and ``reality_states`` keeps its
invariant: every row is a complete, fully-derived belief.

Determinism holds throughout. ``as_of`` is an argument, never a clock read;
every collection is iterated in an explicitly sorted order; all arithmetic is
``Decimal``. Run it twice on the same inputs and it returns the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.engine.confidence import (
    ConfidenceUnavailable,
    age_hours,
    calculate_confidence,
    observation_weight,
)
from app.engine.conflicts import (
    detect_contested_state,
    detect_source_disagreement,
    detect_value_conflict,
)
from app.engine.selection import (
    build_candidates,
    latest_per_source,
    numeric_divergence,
    selection_margin,
)
from app.engine.spec import (
    MISSING_SPECIFICATIONS,
    ConfidenceSpecification,
    SpecificationUnavailableError,
)
from app.engine.types import (
    Candidate,
    ConflictFinding,
    EvidenceEntry,
    ObservationInput,
    RealityCalculation,
)
from app.models.reality_state import EvidenceRole, RealityStatus


@dataclass(frozen=True, slots=True)
class CalculationBlocked:
    """The engine could not produce a state, and exactly why.

    Surfaced by the API so "what is blocking this" is answerable from the
    system rather than from a person's memory.
    """

    attribute: str
    missing: str
    detail: str
    outstanding: tuple[tuple[str, str], ...] = MISSING_SPECIFICATIONS

    def as_dict(self) -> dict[str, object]:
        return {
            "blocked": True,
            "attribute": self.attribute,
            "blocked_on": self.missing,
            "detail": self.detail,
            "missing_specifications": [
                {"name": name, "description": description} for name, description in self.outstanding
            ],
        }


def calculate(
    *,
    attribute: str,
    observations: tuple[ObservationInput, ...],
    as_of: datetime,
    specification: ConfidenceSpecification,
) -> RealityCalculation | CalculationBlocked:
    """Derive the reality state for one attribute of one entity."""
    if not observations:
        # An honest absence. UNKNOWN exists precisely so "we have nothing" is
        # expressible without inventing a value, and it needs no specification.
        return _unknown(attribute=attribute, as_of=as_of)

    # 1. Bitemporal supersession — keep each source's most recent statement by
    #    EVENT time. A late-arriving backfill must not displace current truth.
    current, superseded = latest_per_source(observations)

    # 2. Validation failures are excluded from selection but kept as evidence:
    #    dropping them would hide that a source is emitting bad data.
    eligible = tuple(o for o in current if o.validation_passed)
    invalid = tuple((o, "validation_failed") for o in current if not o.validation_passed)

    if not eligible:
        return _unknown(
            attribute=attribute,
            as_of=as_of,
            excluded=(*superseded, *invalid),
        )

    # 3. Weight every eligible observation. This is the first step that needs
    #    the specification, so it is where a blocked calculation stops.
    try:
        weighted = tuple(
            (
                o,
                observation_weight(
                    o, age_hours=age_hours(o, as_of=as_of), specification=specification
                ),
            )
            for o in eligible
        )
        freshness_values = tuple(
            specification.freshness(age_hours(o, as_of=as_of)) for o in eligible
        )
        quality_values = tuple(
            specification.quality(o.quality, o.validation_passed) for o in eligible
        )
    except SpecificationUnavailableError as exc:
        return CalculationBlocked(attribute=attribute, missing=exc.missing, detail=str(exc))

    # 4. Group into candidates. The returned order is the ranking, and it is
    #    total — see selection._candidate_sort_key.
    candidates = build_candidates(weighted)
    winner = candidates[0]

    margin = selection_margin(candidates)
    divergence = numeric_divergence(candidates)

    # 5. Confidence over the winner's distinct sources.
    winner_reliability = {str(o.source_id): o.reliability for o in winner.observations}
    confidence = calculate_confidence(
        winner=winner,
        all_candidates=candidates,
        weights=winner_reliability,
        freshness_values=freshness_values,
        quality_values=quality_values,
        context={
            "margin": margin,
            "divergence": divergence,
            "candidate_count": len(candidates),
            "source_count": len({o.source_id for o in eligible}),
            "observation_count": len(eligible),
            "as_of": as_of,
        },
        specification=specification,
    )
    if isinstance(confidence, ConfidenceUnavailable):
        return CalculationBlocked(
            attribute=attribute, missing=confidence.missing, detail=confidence.detail
        )

    evidence = _build_evidence(winner=winner, weighted=weighted, excluded=(*superseded, *invalid))
    conflicts = _detect_conflicts(
        attribute=attribute,
        candidates=candidates,
        margin=margin,
        divergence=divergence,
        specification=specification,
    )
    status = _status(candidates=candidates, conflicts=conflicts)

    return RealityCalculation(
        attribute=attribute,
        value=winner.value,
        status=status,
        confidence=confidence,
        selection_reason=_reason(winner=winner, candidates=candidates, margin=margin),
        candidates=candidates,
        evidence=evidence,
        conflicts=conflicts,
        # Valid from when the winning value was most recently asserted to be
        # true — an event-time fact, not a calculation-time one.
        valid_from=winner.latest_event_time,
        calculated_as_of=as_of,
    )


def _unknown(
    *,
    attribute: str,
    as_of: datetime,
    excluded: tuple[tuple[ObservationInput, str], ...] = (),
) -> RealityCalculation:
    """A state with no usable evidence.

    Needs no specification: confidence is zero because there is nothing to be
    confident about, which is a fact rather than a computed score.
    """
    from app.engine.spec import ALGORITHM_VERSION
    from app.engine.types import ConfidenceResult

    return RealityCalculation(
        attribute=attribute,
        value=None,
        status=RealityStatus.UNKNOWN,
        confidence=ConfidenceResult(
            score=Decimal("0.0"),
            ceiling=Decimal(0),
            base=Decimal(0),
            factors=(),
            penalties=(),
            algorithm_version=ALGORITHM_VERSION,
        ),
        selection_reason="No usable observations for this attribute.",
        candidates=(),
        evidence=tuple(
            EvidenceEntry(
                observation=o,
                role=EvidenceRole.EXCLUDED,
                weight=Decimal(0),
                exclusion_reason=reason,
            )
            for o, reason in excluded
        ),
        conflicts=(),
        valid_from=as_of,
        calculated_as_of=as_of,
    )


def _build_evidence(
    *,
    winner: Candidate,
    weighted: tuple[tuple[ObservationInput, Decimal], ...],
    excluded: tuple[tuple[ObservationInput, str], ...],
) -> tuple[EvidenceEntry, ...]:
    """Every observation's role. Nothing considered is left unrecorded.

    This is what makes "no unsupported assertion" structural rather than
    aspirational: the evidence list covers the full input, including what was
    looked at and set aside.
    """
    winning_ids = {o.observation_id for o in winner.observations}

    entries = [
        EvidenceEntry(
            observation=observation,
            role=(
                EvidenceRole.SUPPORTING
                if observation.observation_id in winning_ids
                else EvidenceRole.DISSENTING
            ),
            weight=weight,
        )
        for observation, weight in weighted
    ]
    entries.extend(
        EvidenceEntry(
            observation=observation,
            role=EvidenceRole.EXCLUDED,
            weight=Decimal(0),
            exclusion_reason=reason,
        )
        for observation, reason in excluded
    )
    # Sorted so the evidence list is itself reproducible.
    return tuple(sorted(entries, key=lambda e: str(e.observation.observation_id)))


def _detect_conflicts(
    *,
    attribute: str,
    candidates: tuple[Candidate, ...],
    margin: Decimal,
    divergence: Decimal | None,
    specification: ConfidenceSpecification,
) -> tuple[ConflictFinding, ...]:
    findings = [
        detect_value_conflict(
            attribute=attribute,
            candidates=candidates,
            divergence=divergence,
            margin=margin,
            specification=specification,
        ),
        detect_source_disagreement(
            attribute=attribute, candidates=candidates, specification=specification
        ),
        detect_contested_state(
            attribute=attribute,
            candidates=candidates,
            margin=margin,
            specification=specification,
        ),
    ]
    return tuple(f for f in findings if f is not None)


def _status(
    *, candidates: tuple[Candidate, ...], conflicts: tuple[ConflictFinding, ...]
) -> RealityStatus:
    """Determine the status from what is actually known.

    Only the categorical distinctions are made here, because only those are
    specified:

    * no candidates          -> UNKNOWN
    * exactly one candidate  -> CONFIRMED   (nothing disagrees)
    * more than one          -> CONTESTED   (something disagrees)

    ``STALE`` and ``PROVISIONAL`` are deliberately unreachable. Both require a
    threshold — how old is too old, how thin is too thin — and those thresholds
    are unspecified. Choosing one would silently define product behaviour
    nobody approved, so the engine declines to reach those states rather than
    guessing where the boundary sits.
    """
    if not candidates:
        return RealityStatus.UNKNOWN
    if len(candidates) == 1 and not conflicts:
        return RealityStatus.CONFIRMED
    return RealityStatus.CONTESTED


def _reason(*, winner: Candidate, candidates: tuple[Candidate, ...], margin: Decimal) -> str:
    """Why this value won, rendered from the calculation itself.

    Generated deterministically from a template — the engine knows exactly why
    it chose. Deliberately not AI-written: an explanation that does not follow
    from the arithmetic is a story, not a reason.
    """
    sources = len(winner.source_ids)
    source_word = "source" if sources == 1 else "sources"

    if len(candidates) == 1:
        return (
            f"{sources} {source_word} agreed on this value and none disagreed "
            f"(combined weight {winner.weight})."
        )
    return (
        f"Selected from {len(candidates)} competing values. This one carried the "
        f"greatest weight ({winner.weight}) across {sources} {source_word}, "
        f"leading the runner-up by {margin} percentage points of weight share."
    )
