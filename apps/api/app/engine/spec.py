"""Confidence specification: what is confirmed, and what is missing.

The Phase 0 specification is unrecoverable. An exhaustive search — every branch,
tag, ref, stash, reflog entry, dangling object and committed file, plus project
docs and the surrounding filesystem — found no confidence specification and no
LAPTOP-001 scenario.

So this module holds exactly two things, and keeps them rigorously apart:

**CONFIRMED** — the formula structure, recoverable from the Phase 4 brief and
from ``docs/architecture.md`` as first committed in Phase 1. Implemented.

**MISSING** — the sub-formulas the structure calls into. Declared as an
interface with no implementation. :data:`UNAVAILABLE_SPECIFICATION` raises
:class:`SpecificationUnavailableError` naming precisely which input is absent.

There is deliberately no default, no fallback and no "reasonable guess". A
plausible-looking confidence score is worse than no score: it would be stored,
displayed, believed, and built upon, and nothing about it would look wrong.
This product exists to stop exactly that, so it refuses.

An earlier revision of this file carried nine provisional constants. They have
been removed. One of them was demonstrably wrong: the recovered Phase 1 text
names the penalties ``coverage``, ``staleness``, ``impossible``, ``late``,
while the invented set was ``single_source``, ``staleness``,
``validation_failure``, ``contested`` — only one of four overlapped. That is a
fair measure of how much a confident-sounding guess can miss by.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

# ===========================================================================
# CONFIRMED — implemented in app/engine/confidence.py
# ===========================================================================

#: Base factor weights, from the Phase 4 brief. These supersede the ordering
#: recorded in docs/architecture.md; see the supersession note there.
#:
#:     Base = 0.40*reliability + 0.30*freshness + 0.15*quality + 0.15*agreement
WEIGHT_RELIABILITY = Decimal("0.40")
WEIGHT_FRESHNESS = Decimal("0.30")
WEIGHT_QUALITY = Decimal("0.15")
WEIGHT_AGREEMENT = Decimal("0.15")

#: Ceiling = 1 - product(1 - R_source) over distinct supporting sources, capped here.
#: No finite set of observations justifies certainty.
#:
#: Confirmed twice: docs/architecture.md caps the ceiling at 0.99, and the
#: recovered Phase 0 decision record states "Maximum confidence = 99%"
#: independently. See docs/phase-0-recovery.md.
CEILING_CAP = Decimal("0.99")

#: Recovered in Phase 10 from the Phase 0 decision record:
#:
#:     "Keep recorded event-time semantics without applying an automatic
#:      freshness discount in MVP."
#:
#: A real specification decision, and currently unenforceable: there is no
#: freshness curve, so there is no discount to withhold. Recorded here so that
#: whoever implements freshness does not invent a penalty for `recorded`
#: semantics that Phase 0 explicitly declined to apply. It is a warning and
#: root-cause signal in MVP, not a scoring input.
RECORDED_SEMANTICS_TAKES_NO_FRESHNESS_DISCOUNT = True

#: Score = 100 x Ceiling x Base x coverage x staleness x impossible x late,
#: bounded to this range.
MAX_CONFIDENCE = Decimal("99.0")
MIN_CONFIDENCE = Decimal("0.0")

#: Stored as NUMERIC(4,1) and compared exactly, so quantisation happens once,
#: here, rather than wherever a score is rendered.
CONFIDENCE_PRECISION = Decimal("0.1")

#: The four penalty terms, in the order recorded in Phase 1. Names are
#: confirmed; the trigger conditions and multipliers are not.
PENALTY_NAMES: tuple[str, ...] = ("coverage", "staleness", "impossible", "late")

#: Recorded on every reality state. The `-unspecified` suffix is load-bearing:
#: any state produced without the sub-formulas is identifiable at a glance and
#: can be found and recalculated once the specification arrives.
ALGORITHM_VERSION = "reality-engine/1.0.0-unspecified"


def _validate_weights() -> None:
    """Fail at import if the confirmed weights stop summing to 1.

    A silent drift here would rescale every score in the product without
    anything appearing to break.
    """
    total = WEIGHT_RELIABILITY + WEIGHT_FRESHNESS + WEIGHT_QUALITY + WEIGHT_AGREEMENT
    if total != Decimal("1.00"):
        raise ValueError(f"Base factor weights must sum to 1.00; got {total}")


_validate_weights()


# ===========================================================================
# MISSING — interface only, no implementation
# ===========================================================================


class SpecificationUnavailableError(NotImplementedError):
    """A confidence sub-formula was needed and is not specified.

    Raised rather than defaulted. The caller records the absence honestly —
    ``confidence`` is stored NULL with the missing inputs listed — instead of
    substituting a number nobody approved.
    """

    def __init__(self, missing: str, detail: str = "") -> None:
        message = (
            f"Confidence sub-formula '{missing}' is not specified. "
            "The Phase 0 specification is unrecoverable; see app/engine/spec.py."
        )
        super().__init__(f"{message} {detail}".strip())
        self.missing = missing


#: Every input required before a confidence score can be produced. Surfaced by
#: the API and stored on any state that could not be scored, so "what is
#: blocking this" is answerable from the data rather than from a person.
MISSING_SPECIFICATIONS: tuple[tuple[str, str], ...] = (
    (
        "freshness",
        "Decay curve and constant mapping observation age to 0..1. Partially "
        "constrained: Phase 0 confirmed that 'recorded' event-time semantics "
        "takes no automatic freshness discount in MVP. The curve itself is "
        "still unknown.",
    ),
    (
        "quality",
        "Derivation of the 0..1 quality factor, or confirmation it is a source-declared input.",
    ),
    ("agreement", "Derivation of the 0..1 agreement factor from competing candidate weights."),
    (
        "reliability_table",
        "R_source per authority level, or confirmation reliability is configured per source.",
    ),
    ("penalty_coverage", "Trigger condition and multiplier for the coverage penalty."),
    ("penalty_staleness", "Trigger condition and multiplier for the staleness penalty."),
    ("penalty_impossible", "Trigger condition and multiplier for the impossible penalty."),
    ("penalty_late", "Trigger condition and multiplier for the late penalty."),
    ("conflict_score", "Formula producing the 0..1 conflict score (golden expects 0.594)."),
    (
        "margin_definition",
        "Confirmation that margin is the winner-runner-up weight-share gap in "
        "percentage points (golden expects 0.78%).",
    ),
    (
        "severity_thresholds",
        "Conflict-score boundaries mapping to low/medium/high/critical (0.594 must map to HIGH).",
    ),
    (
        "laptop_001_scenario",
        "Per-observation inputs: source, authority, reliability, value, "
        "event_time, ingested_at, quality, validation.",
    ),
)


@runtime_checkable
class ConfidenceSpecification(Protocol):
    """The sub-formulas the confirmed structure calls into.

    Injected rather than imported, so the missing pieces can be supplied later
    without touching the engine. Every method here corresponds to one entry in
    :data:`MISSING_SPECIFICATIONS`.
    """

    def freshness(self, age_hours: Decimal) -> Decimal:
        """0..1 freshness for an observation of the given age."""
        ...

    def quality(self, declared_quality: Decimal, validation_passed: bool) -> Decimal:
        """0..1 quality factor for one observation."""
        ...

    def agreement(self, winning_share: Decimal, candidate_count: int) -> Decimal:
        """0..1 agreement factor across competing candidates."""
        ...

    def reliability_for_authority(self, authority: str) -> Decimal:
        """R_source implied by a declared authority level."""
        ...

    def penalty(self, name: str, context: dict[str, object]) -> Decimal:
        """Multiplier for one of :data:`PENALTY_NAMES`."""
        ...

    def conflict_score(self, context: dict[str, object]) -> Decimal:
        """0..1 conflict score."""
        ...

    def severity_for_score(self, score: Decimal) -> str:
        """Severity label for a conflict score."""
        ...

    def contested_margin_threshold(self) -> Decimal:
        """Margin at or below which a state is contested rather than confirmed."""
        ...


class _UnavailableSpecification:
    """The shipped specification: every sub-formula raises.

    Not a stub to be filled in casually — it is the honest state of the system.
    Until the Phase 0 values arrive, RealitySync cannot compute a confidence
    score, and it says so rather than producing one.
    """

    def freshness(self, age_hours: Decimal) -> Decimal:
        raise SpecificationUnavailableError("freshness")

    def quality(self, declared_quality: Decimal, validation_passed: bool) -> Decimal:
        raise SpecificationUnavailableError("quality")

    def agreement(self, winning_share: Decimal, candidate_count: int) -> Decimal:
        raise SpecificationUnavailableError("agreement")

    def reliability_for_authority(self, authority: str) -> Decimal:
        raise SpecificationUnavailableError("reliability_table")

    def penalty(self, name: str, context: dict[str, object]) -> Decimal:
        raise SpecificationUnavailableError(f"penalty_{name}")

    def conflict_score(self, context: dict[str, object]) -> Decimal:
        raise SpecificationUnavailableError("conflict_score")

    def severity_for_score(self, score: Decimal) -> str:
        raise SpecificationUnavailableError("severity_thresholds")

    def contested_margin_threshold(self) -> Decimal:
        raise SpecificationUnavailableError("margin_definition")

    def __repr__(self) -> str:
        return "<UnavailableSpecification: Phase 0 confidence spec not recoverable>"


#: The specification the application runs with. Replacing this is the entire
#: task once the Phase 0 numbers are available.
UNAVAILABLE_SPECIFICATION: ConfidenceSpecification = _UnavailableSpecification()
