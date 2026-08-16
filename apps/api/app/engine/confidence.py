"""Confidence scoring — the confirmed structure.

Implements exactly what is confirmed:

    w_o     = R_source x Freshness x Quality
    Ceiling = 1 - product(1 - R_source)   over distinct supporting sources, cap 0.99
    Base    = 0.40*reliability + 0.30*freshness + 0.15*quality + 0.15*agreement
    Score   = 100 x Ceiling x Base x coverage x staleness x impossible x late
              bounded to 0..99

Every sub-formula the structure calls into — freshness, quality, agreement,
the reliability table, the four penalties — comes from an injected
:class:`~app.engine.spec.ConfidenceSpecification`. The shipped implementation
raises, so with no specification present this module produces
:class:`ConfidenceUnavailable` rather than a number.

Arithmetic is ``Decimal`` end to end. Float would make the same calculation
differ in the last digit across platforms, and "deterministic and reproducible"
would quietly become "reproducible on this machine".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.engine.spec import (
    CEILING_CAP,
    CONFIDENCE_PRECISION,
    MAX_CONFIDENCE,
    MIN_CONFIDENCE,
    MISSING_SPECIFICATIONS,
    PENALTY_NAMES,
    WEIGHT_AGREEMENT,
    WEIGHT_FRESHNESS,
    WEIGHT_QUALITY,
    WEIGHT_RELIABILITY,
    ConfidenceSpecification,
    SpecificationUnavailableError,
)
from app.engine.types import (
    Candidate,
    ConfidenceResult,
    FactorBreakdown,
    ObservationInput,
    PenaltyBreakdown,
)

#: Working precision for intermediate terms.
FACTOR_PRECISION = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class ConfidenceUnavailable:
    """Confidence could not be computed, and why.

    Returned instead of a score. Persisted as ``confidence = NULL`` with this
    detail in the breakdown, so a state carries an honest absence rather than a
    fabricated number — and so every affected row is findable once the
    specification lands.
    """

    missing: str
    detail: str
    outstanding: tuple[tuple[str, str], ...] = MISSING_SPECIFICATIONS

    def as_dict(self) -> dict[str, object]:
        return {
            "available": False,
            "blocked_on": self.missing,
            "detail": self.detail,
            "missing_specifications": [
                {"name": name, "description": description} for name, description in self.outstanding
            ],
            "confirmed_structure": {
                "weight": "w_o = R_source x Freshness x Quality",
                "ceiling": "1 - product(1 - R_source), capped at 0.99",
                "base": ("0.40*reliability + 0.30*freshness + 0.15*quality + 0.15*agreement"),
                "score": (
                    "100 x ceiling x base x coverage x staleness x impossible x late, bounded 0-99"
                ),
            },
        }


def compute_ceiling(reliabilities: tuple[Decimal, ...]) -> Decimal:
    """``1 - product(1 - R)`` over distinct supporting sources, capped at 0.99.

    Independent corroboration: each additional source closes part of the
    remaining doubt rather than adding linearly. Two sources at 0.6 give 0.84,
    not 1.2 — which is why the term can approach, but never reach, certainty.

    Reliabilities are per *source*, deduplicated by the caller. Counting one
    source twice would fabricate corroboration from a single voice.
    """
    remaining = Decimal(1)
    for reliability in reliabilities:
        bounded = _clamp(reliability, Decimal(0), Decimal(1))
        remaining *= Decimal(1) - bounded

    ceiling = Decimal(1) - remaining
    return min(ceiling, CEILING_CAP).quantize(FACTOR_PRECISION)


def observation_weight(
    observation: ObservationInput,
    *,
    age_hours: Decimal,
    specification: ConfidenceSpecification,
) -> Decimal:
    """``w_o = R_source x Freshness x Quality``.

    Confirmed. Raises if freshness or quality is unspecified, and also if the
    observation carries no declared reliability or quality - an undeclared
    input is not a zero and not a midpoint, it is a missing specification, and
    substituting a number here would be inventing the reliability table.
    """
    # The formulas are consulted first, deliberately. An undeclared reliability
    # is a configuration gap an operator could close today; a missing freshness
    # curve cannot be closed by anyone without the specification. When both
    # block, reporting the formula is the more useful answer, because
    # configuring reliability would not unblock anything on its own.
    freshness = specification.freshness(age_hours)

    if observation.reliability is None:
        raise SpecificationUnavailableError(
            "reliability_table",
            "No reliability is declared for this source, and the table that "
            "would supply one per authority level is unavailable.",
        )
    if observation.quality is None:
        raise SpecificationUnavailableError(
            "quality",
            "No quality is declared for this observation, and its derivation is unavailable.",
        )

    quality = specification.quality(observation.quality, observation.validation_passed)
    weight = observation.reliability * freshness * quality
    return _clamp(weight, Decimal(0), Decimal(1)).quantize(FACTOR_PRECISION)


def age_hours(observation: ObservationInput, *, as_of: datetime) -> Decimal:
    """Age in hours, measured on the **event-time** axis.

    Freshness is a claim about how recently something was *true*, not how
    recently we happened to hear about it. Measuring from ingestion would make
    a backfilled year-old record look brand new the moment it arrived.

    Never negative: an observation with a future event time is treated as
    current rather than as impossibly fresh.
    """
    delta = as_of - observation.event_time
    hours = Decimal(delta.total_seconds()) / Decimal(3600)
    return max(hours, Decimal(0)).quantize(FACTOR_PRECISION)


def compute_base(
    *,
    reliability: Decimal,
    freshness: Decimal,
    quality: Decimal,
    agreement: Decimal,
) -> tuple[Decimal, tuple[FactorBreakdown, ...]]:
    """``0.40*reliability + 0.30*freshness + 0.15*quality + 0.15*agreement``.

    Confirmed, from the Phase 4 brief. Returns the value and every term's
    contribution, so the total can be checked by hand.
    """
    factors = (
        FactorBreakdown("reliability", _clamp01(reliability), WEIGHT_RELIABILITY),
        FactorBreakdown("freshness", _clamp01(freshness), WEIGHT_FRESHNESS),
        FactorBreakdown("quality", _clamp01(quality), WEIGHT_QUALITY),
        FactorBreakdown("agreement", _clamp01(agreement), WEIGHT_AGREEMENT),
    )
    base = sum((f.contribution for f in factors), start=Decimal(0))
    return base.quantize(FACTOR_PRECISION), factors


def apply_penalties(
    base_score: Decimal,
    *,
    context: dict[str, object],
    specification: ConfidenceSpecification,
) -> tuple[Decimal, tuple[PenaltyBreakdown, ...]]:
    """Apply ``coverage x staleness x impossible x late``.

    The names are confirmed; their triggers and multipliers are not, so each is
    requested from the specification and any one being unspecified fails the
    whole calculation. Applying three of four would silently produce a score
    that is wrong by an unknown factor.
    """
    penalties: list[PenaltyBreakdown] = []
    score = base_score

    for name in PENALTY_NAMES:
        multiplier = _clamp01(specification.penalty(name, context))
        penalties.append(PenaltyBreakdown(name=name, multiplier=multiplier))
        score *= multiplier

    return score.quantize(FACTOR_PRECISION), tuple(penalties)


def calculate_confidence(
    *,
    winner: Candidate,
    all_candidates: tuple[Candidate, ...],
    weights: dict[str, Decimal],
    freshness_values: tuple[Decimal, ...],
    quality_values: tuple[Decimal, ...],
    context: dict[str, object],
    specification: ConfidenceSpecification,
) -> ConfidenceResult | ConfidenceUnavailable:
    """Produce a confidence score, or an honest statement that it cannot be.

    ``weights`` maps ``str(source_id) -> reliability`` for the winner's
    distinct supporting sources, so the ceiling cannot double-count a source
    that contributed several observations.
    """
    try:
        ceiling = compute_ceiling(tuple(weights[key] for key in sorted(weights)))

        mean_reliability = _mean(tuple(weights[key] for key in sorted(weights)))
        mean_freshness = _mean(freshness_values)
        mean_quality = _mean(quality_values)
        agreement = specification.agreement(winner.share, len(all_candidates))

        base, factors = compute_base(
            reliability=mean_reliability,
            freshness=mean_freshness,
            quality=mean_quality,
            agreement=agreement,
        )

        penalised, penalties = apply_penalties(
            ceiling * base, context=context, specification=specification
        )

        score = _clamp(
            (penalised * 100).quantize(CONFIDENCE_PRECISION),
            MIN_CONFIDENCE,
            MAX_CONFIDENCE,
        )

    except SpecificationUnavailableError as exc:
        return ConfidenceUnavailable(missing=exc.missing, detail=str(exc))

    from app.engine.spec import ALGORITHM_VERSION

    return ConfidenceResult(
        score=score,
        ceiling=ceiling,
        base=base,
        factors=factors,
        penalties=penalties,
        algorithm_version=ALGORITHM_VERSION,
    )


# --- helpers ---------------------------------------------------------------


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _clamp01(value: Decimal) -> Decimal:
    return _clamp(value, Decimal(0), Decimal(1))


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        return Decimal(0)
    return (sum(values, start=Decimal(0)) / Decimal(len(values))).quantize(FACTOR_PRECISION)
