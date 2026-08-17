"""Conflict detection.

Two layers, kept apart because one is specified and the other is not:

**Detection is categorical and deterministic.** Whether sources disagree is a
fact about the evidence: more than one distinct value among the surviving
observations *is* a disagreement. No constant is needed to observe it, and this
layer works today.

**Grading is not specified.** The 0..1 conflict score and its mapping to
low/medium/high/critical come from the Phase 0 specification, which is
unrecoverable. Those are requested from the injected specification and are
absent until it arrives.

So the engine can always say *that* sources disagree, and — once the
specification lands — *how much it matters*. It never guesses the second.

Conflicts never alter the selected value. The dependency runs one way: the
engine selects by the approved rules, then records that the selection was
contested. If resolving a conflict could change a value, the state would
depend on the order conflicts were processed and would stop being reproducible
from observations alone.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from app.engine.spec import (
    ConfidenceSpecification,
    SpecificationUnavailableError,
)
from app.engine.types import Candidate, ConflictFinding
from app.ingestion.fingerprint import canonical_json
from app.models.conflict import ConflictType


def conflict_fingerprint(
    *, attribute: str, candidates: tuple[Candidate, ...], conflict_type: str
) -> str:
    """Deterministic identity of one disagreement.

    Over the competing values and the sources asserting them — not over
    weights, timestamps or scores. Re-running the engine on unchanged evidence
    must produce the same fingerprint so the conflict updates in place rather
    than accumulating a duplicate row on every calculation. A genuinely
    different disagreement produces a different fingerprint.
    """
    document = {
        "attribute": attribute,
        "type": conflict_type,
        "candidates": [
            {
                "value": candidate.value,
                "sources": [str(s) for s in candidate.source_ids],
            }
            for candidate in candidates
        ],
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def detect_value_conflict(
    *,
    attribute: str,
    candidates: tuple[Candidate, ...],
    divergence: Decimal | None,
    margin: Decimal,
    specification: ConfidenceSpecification,
) -> ConflictFinding | None:
    """A value conflict: two or more sources assert different values.

    Detection needs no specification. Scoring and severity do, and when they
    are unavailable the finding is still returned — with ``score`` unset and
    severity recorded as unspecified — because "these sources disagree" is
    useful on its own and suppressing it would hide a real problem behind a
    missing constant.
    """
    if len(candidates) < 2:
        return None

    details: dict[str, Any] = {
        "competing_values": [
            {
                "value": candidate.value,
                "weight": str(candidate.weight),
                "share": str(candidate.share),
                "sources": [str(s) for s in candidate.source_ids],
                "observation_count": len(candidate.observations),
            }
            for candidate in candidates
        ],
        "margin_percentage_points": str(margin),
        "divergence": str(divergence) if divergence is not None else None,
        "divergence_units": "attribute units" if divergence is not None else None,
    }

    context: dict[str, Any] = {
        "margin": margin,
        "divergence": divergence,
        "candidate_count": len(candidates),
        "winning_share": candidates[0].share,
        "runner_up_share": candidates[1].share,
    }

    score, severity, graded = _grade(context, specification)
    if not graded:
        details["grading"] = {
            "available": False,
            "reason": "conflict_score and severity_thresholds are unspecified",
        }

    winner, runner_up = candidates[0], candidates[1]
    if graded:
        summary = (
            f"{len(candidates)} distinct values for '{attribute}'. "
            f"{winner.value!r} leads {runner_up.value!r} by {margin} percentage points"
            + (f", diverging by {divergence} units" if divergence is not None else "")
            + "."
        )
    else:
        # Without the weighting specification every candidate carries the same
        # weight, so `margin` is zero — and "leads by 0 percentage points" is a
        # claim of leadership the evidence does not support. Ranking is exactly
        # the thing that is unavailable, so the summary says so rather than
        # dressing a zero up as a result.
        summary = (
            f"The sources gave {len(candidates)} different answers for '{attribute}'"
            + (f", {divergence} apart" if divergence is not None else "")
            + ". Neither one is treated as more correct, because there is no "
            "agreed way to decide which source to trust more."
        )

    return ConflictFinding(
        conflict_type=ConflictType.VALUE_CONFLICT.value,
        severity=severity,
        score=score,
        summary=summary,
        fingerprint=conflict_fingerprint(
            attribute=attribute,
            candidates=candidates,
            conflict_type=ConflictType.VALUE_CONFLICT.value,
        ),
        details=details,
    )


def detect_source_disagreement(
    *,
    attribute: str,
    candidates: tuple[Candidate, ...],
    specification: ConfidenceSpecification,
) -> ConflictFinding | None:
    """Distinct sources asserting distinct values — a systemic signal.

    Different from a plain value conflict: here each competing value is backed
    by a *different* source, which points at a broken integration or a clock
    skew rather than one bad row. Detected when at least two candidates have
    disjoint source sets.
    """
    if len(candidates) < 2:
        return None

    source_sets = [set(candidate.source_ids) for candidate in candidates]
    disjoint = any(
        not source_sets[i] & source_sets[j]
        for i in range(len(source_sets))
        for j in range(i + 1, len(source_sets))
    )
    if not disjoint:
        return None

    details: dict[str, Any] = {
        "source_positions": [
            {
                "value": candidate.value,
                "sources": [str(s) for s in candidate.source_ids],
            }
            for candidate in candidates
        ]
    }

    context: dict[str, Any] = {
        "candidate_count": len(candidates),
        "disjoint_sources": True,
        "winning_share": candidates[0].share,
        "runner_up_share": candidates[1].share,
        "margin": candidates[0].share - candidates[1].share,
        "divergence": None,
    }

    score, severity, graded = _grade(context, specification)
    if not graded:
        details["grading"] = {"available": False}

    return ConflictFinding(
        conflict_type=ConflictType.SOURCE_DISAGREEMENT.value,
        severity=severity,
        score=score,
        summary=(
            f"Two systems disagree about '{attribute}'. When separate systems "
            "disagree, the cause is usually how they are set up or synced, not one "
            "bad row."
        ),
        fingerprint=conflict_fingerprint(
            attribute=attribute,
            candidates=candidates,
            conflict_type=ConflictType.SOURCE_DISAGREEMENT.value,
        ),
        details=details,
    )


def detect_contested_state(
    *,
    attribute: str,
    candidates: tuple[Candidate, ...],
    margin: Decimal,
    specification: ConfidenceSpecification,
) -> ConflictFinding | None:
    """The winning margin is too thin for the selection to be decisive.

    Requires the contested-margin threshold, which is unspecified. Returns None
    rather than picking a boundary: "contested" is a judgement about how close
    is too close, and inventing that number would silently define product
    behaviour that nobody approved.
    """
    if len(candidates) < 2:
        return None

    try:
        threshold = specification.contested_margin_threshold()
    except SpecificationUnavailableError:
        return None

    if margin > threshold:
        return None

    context: dict[str, Any] = {
        "margin": margin,
        "threshold": threshold,
        "candidate_count": len(candidates),
        "winning_share": candidates[0].share,
        "runner_up_share": candidates[1].share,
        "divergence": None,
    }
    score, severity, _ = _grade(context, specification)

    return ConflictFinding(
        conflict_type=ConflictType.CONTESTED_STATE.value,
        severity=severity,
        score=score,
        summary=(
            f"The selected value for '{attribute}' leads by only {margin} "
            f"percentage points, at or below the {threshold} threshold."
        ),
        fingerprint=conflict_fingerprint(
            attribute=attribute,
            candidates=candidates,
            conflict_type=ConflictType.CONTESTED_STATE.value,
        ),
        details={"margin_percentage_points": str(margin), "threshold": str(threshold)},
    )


#: Recorded when grading is unavailable, so a severity column is never silently
#: filled with a plausible-looking level.
UNSPECIFIED_SEVERITY = "unspecified"


def _grade(
    context: dict[str, Any], specification: ConfidenceSpecification
) -> tuple[Decimal | None, str, bool]:
    """Score and severity, or an explicit absence."""
    try:
        score = specification.conflict_score(context)
        return score, specification.severity_for_score(score), True
    except SpecificationUnavailableError:
        return None, UNSPECIFIED_SEVERITY, False
