"""The Reality Engine.

One entry point: :func:`calculate`. Given observations and a point in time it
always returns a :class:`~app.engine.types.RealityCalculation` — there is no
"blocked" outcome any more, because being unable to score is not the same as
being unable to say anything.

Phase 5 returned a blocked result whenever the confidence specification was
unavailable, and persistence wrote nothing. The reasoning was that a reality
state is a claim with a confidence attached, so half of one would be an
unfalsifiable assertion. The cost was that ``reality_states`` was empty in
every deployment and the selection, evidence and provenance that need no
formula could not be reached at all.

Phase 9 narrows the withholding to what is genuinely missing. The calculation
now carries two independently optional things:

``confidence``  absent when the scoring specification is unavailable. Absent,
                never zero: a zero is a score, and claiming one asserts what
                the missing formula would have produced.
``value``       absent when no value could be selected — either because there
                is no usable evidence (UNKNOWN) or because several values
                compete and ranking them is precisely the missing formula
                (CONTESTED).

Everything else — which values were asserted, which observations were
superseded, which failed validation, whether the sources agree — is
categorical, follows from the observations alone, and is always produced.

Determinism holds throughout. ``as_of`` is an argument, never a clock read;
every collection is iterated in an explicitly sorted order; all arithmetic is
``Decimal``. Run it twice on the same inputs and it returns the same answer.
"""

from __future__ import annotations

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
from app.engine.detection import group_unranked
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
    ConfidenceAbsence,
    ConflictFinding,
    EvidenceEntry,
    ObservationInput,
    RealityCalculation,
)
from app.models.reality_state import EvidenceRole, RealityStatus


def calculate(
    *,
    attribute: str,
    observations: tuple[ObservationInput, ...],
    as_of: datetime,
    specification: ConfidenceSpecification,
) -> RealityCalculation:
    """Derive the reality state for one attribute of one entity.

    Total: every input produces a state. What varies is how much of it could be
    established, and each absence says why.
    """
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
            specification.quality(_declared(o.quality, "quality"), o.validation_passed)
            for o in eligible
        )
    except SpecificationUnavailableError as exc:
        # Scoring is blocked. Almost everything else is not.
        #
        # Phase 5 treated this as total: nothing was written, so reality_states
        # stayed empty and the selection, evidence and provenance that need no
        # formula were unreachable. Phase 9 separates the two. Which distinct
        # values exist, which observations were superseded, which failed
        # validation, and whether the sources agree are all categorical facts
        # about the evidence. Only "how sure are we" — and, when the sources
        # disagree, "which one wins" — require the missing weights.
        return _unscored(
            attribute=attribute,
            eligible=eligible,
            excluded=(*superseded, *invalid),
            as_of=as_of,
            specification=specification,
            absence=ConfidenceAbsence(
                missing=exc.missing, detail=str(exc), outstanding=MISSING_SPECIFICATIONS
            ),
        )

    # 4. Group into candidates. The returned order is the ranking, and it is
    #    total — see selection._candidate_sort_key.
    candidates = build_candidates(weighted)
    winner = candidates[0]

    margin = selection_margin(candidates)
    divergence = numeric_divergence(candidates)

    # 5. Confidence over the winner's distinct sources.
    winner_reliability = {
        str(o.source_id): _declared(o.reliability, "reliability_table") for o in winner.observations
    }
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
    evidence = _build_evidence(winner=winner, weighted=weighted, excluded=(*superseded, *invalid))
    conflicts = _detect_conflicts(
        attribute=attribute,
        candidates=candidates,
        margin=margin,
        divergence=divergence,
        specification=specification,
    )
    status = _status(candidates=candidates, conflicts=conflicts)

    # Weighting succeeded, so the selection is sound even if the final scoring
    # formula is not available. Selection and scoring are separate questions
    # and only the second one is blocked here.
    scored = None if isinstance(confidence, ConfidenceUnavailable) else confidence
    absence = (
        ConfidenceAbsence(
            missing=confidence.missing,
            detail=confidence.detail,
            outstanding=MISSING_SPECIFICATIONS,
        )
        if isinstance(confidence, ConfidenceUnavailable)
        else None
    )

    return RealityCalculation(
        attribute=attribute,
        value=winner.value,
        status=status,
        confidence=scored,
        confidence_unavailable=absence,
        value_selected=True,
        selection_reason=_reason(winner=winner, candidates=candidates, margin=margin),
        candidates=candidates,
        evidence=evidence,
        conflicts=conflicts,
        # Valid from when the winning value was most recently asserted to be
        # true — an event-time fact, not a calculation-time one.
        valid_from=winner.latest_event_time,
        calculated_as_of=as_of,
    )


def _unscored(
    *,
    attribute: str,
    eligible: tuple[ObservationInput, ...],
    excluded: tuple[tuple[ObservationInput, str], ...],
    as_of: datetime,
    specification: ConfidenceSpecification,
    absence: ConfidenceAbsence,
) -> RealityCalculation:
    """A reality state derived without the scoring formula.

    Everything here follows from the observations and the declared
    configuration alone. Nothing is weighted, nothing is ranked, and no number
    is invented.

    **Selection.** A value is selected only when exactly one distinct value was
    asserted. Then there is nothing to rank: every surviving observation agrees,
    and the value follows from the evidence rather than from a formula. With two
    or more competing values, ranking *is* the missing specification — so the
    state is CONTESTED, ``value`` is None, and every candidate is recorded.
    Returning the alphabetically-first candidate would be a fabricated verdict
    wearing the clothes of a real one.

    **Evidence roles.** Assigned only when a value was selected, because
    "supporting" and "dissenting" are defined relative to a selection. When
    nothing is selected every eligible observation is recorded as a candidate
    member with no role asserted — which is exactly what is known.
    """
    candidates = group_unranked(eligible)
    divergence = numeric_divergence(candidates) if len(candidates) > 1 else None

    selected = len(candidates) == 1
    winner = candidates[0] if selected else None

    if selected and winner is not None:
        status = RealityStatus.CONFIRMED
        sources = len(winner.source_ids)
        reason = (
            f"{sources} {'source' if sources == 1 else 'sources'} reported this value "
            f"and none reported anything different, so this is the value. "
            f"No score is shown because there is no agreed way to calculate one yet."
        )
        valid_from = winner.latest_event_time
    else:
        status = RealityStatus.CONTESTED
        reason = (
            f"The sources gave {len(candidates)} different answers. Picking one would "
            f"mean trusting a source more than the others, and there is no agreed way "
            f"to decide that yet — so nothing was picked. All the answers are kept "
            f"below, with which source said what."
        )
        # The earliest moment any competing claim was made. Using the latest
        # would imply the newest candidate had been preferred.
        valid_from = min(o.event_time for o in eligible)

    evidence = _unscored_evidence(eligible=eligible, winner=winner, excluded=excluded)

    findings = [
        detect_value_conflict(
            attribute=attribute,
            candidates=candidates,
            divergence=divergence,
            # No weights, so no share gap exists to measure. Zero is the honest
            # reading: nothing is known to lead anything.
            margin=Decimal(0),
            specification=specification,
        ),
        detect_source_disagreement(
            attribute=attribute, candidates=candidates, specification=specification
        ),
    ]

    return RealityCalculation(
        attribute=attribute,
        value=winner.value if winner is not None else None,
        status=status,
        confidence=None,
        confidence_unavailable=absence,
        value_selected=selected,
        selection_reason=reason,
        candidates=candidates,
        evidence=evidence,
        conflicts=tuple(f for f in findings if f is not None),
        valid_from=valid_from,
        calculated_as_of=as_of,
    )


def _unscored_evidence(
    *,
    eligible: tuple[ObservationInput, ...],
    winner: Candidate | None,
    excluded: tuple[tuple[ObservationInput, str], ...],
) -> tuple[EvidenceEntry, ...]:
    """Evidence for an unscored state.

    Weight is zero throughout and means "not weighted", not "weighed and found
    worthless". The distinction is carried by the state's confidence being
    absent rather than by the number.
    """
    winning_ids = {o.observation_id for o in winner.observations} if winner is not None else set()

    entries = [
        EvidenceEntry(
            observation=observation,
            role=(
                EvidenceRole.SUPPORTING
                if observation.observation_id in winning_ids
                # No selection was made, so nothing can be dissenting *from* it.
                # Recorded as considered, with the reason stated.
                else (EvidenceRole.DISSENTING if winner is not None else EvidenceRole.CONSIDERED)
            ),
            weight=Decimal(0),
        )
        for observation in eligible
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
    return tuple(sorted(entries, key=lambda e: str(e.observation.observation_id)))


def _declared(value: Decimal | None, missing: str) -> Decimal:
    """Return a declared engine input, or say which specification is missing.

    Undeclared is not zero and not a midpoint. Both would be numbers nobody
    chose, and the whole point of the blocked path is that no such number
    exists in the system.
    """
    if value is None:
        raise SpecificationUnavailableError(
            missing, f"No {missing} is declared, and its specification is unavailable."
        )
    return value


def _unknown(
    *,
    attribute: str,
    as_of: datetime,
    excluded: tuple[tuple[ObservationInput, str], ...] = (),
) -> RealityCalculation:
    """A state with no usable evidence.

    Confidence is **absent**, not zero. An earlier version wrote 0.0 here on
    the reasoning that there is nothing to be confident about — but 0.0 is a
    score, and asserting one means asserting what the missing formula would
    have produced for an empty evidence set. Nobody knows that. "No score
    exists" and "the score is zero" are different claims, and only the first
    is true.
    """
    return RealityCalculation(
        attribute=attribute,
        value=None,
        status=RealityStatus.UNKNOWN,
        confidence=None,
        confidence_unavailable=ConfidenceAbsence(
            missing="confidence_formula",
            detail=(
                "No usable observation exists for this attribute, and the "
                "specification that would say what confidence an empty evidence "
                "set carries is unavailable."
            ),
            outstanding=MISSING_SPECIFICATIONS,
        ),
        value_selected=False,
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
