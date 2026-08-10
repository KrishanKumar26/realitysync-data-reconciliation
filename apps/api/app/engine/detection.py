"""Detection without scoring.

The part of the engine that works while the Phase 0 confidence specification is
missing.

Two questions look similar and are not:

* **Do these sources disagree?** Categorical. Group the surviving observations
  by canonical value; more than one group is a disagreement. No constant is
  needed to see it.
* **Which one is right, and how confident are we?** Requires the weighting
  formula, which is unrecoverable.

Phase 5 delivers the first and refuses the second. A conflict reported here
carries the competing values, the sources behind each, and the numeric
divergence — all facts — with ``score`` NULL and severity ``unspecified``.

Candidates produced here are **unranked and unweighted**. Every weight and
share is zero, and the order is the canonical value key: alphabetical, not
meaningful. Presenting the first as a winner would be exactly the unfounded
selection this module exists to avoid, so nothing downstream may treat it as
one — :class:`DetectionResult` has no ``value`` field to tempt anyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.engine.conflicts import detect_source_disagreement, detect_value_conflict
from app.engine.selection import numeric_divergence, value_key
from app.engine.spec import ConfidenceSpecification
from app.engine.types import Candidate, ConflictFinding, ObservationInput

#: Every candidate produced here carries this weight. Zero rather than None so
#: the shared Candidate type is reusable, and so any arithmetic that wrongly
#: treats these as ranked produces an obviously degenerate result rather than a
#: plausible one.
UNWEIGHTED = Decimal(0)


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """What can be established about an attribute without the scoring formula.

    Deliberately has no ``value``, no ``confidence`` and no ``status``. Those
    are conclusions; this type carries only observations of the evidence.
    """

    attribute: str
    #: Distinct asserted values, ordered by canonical key. Not a ranking.
    candidates: tuple[Candidate, ...]
    conflicts: tuple[ConflictFinding, ...]
    #: Observations set aside before grouping, with the reason. Reported rather
    #: than dropped, so the record shows what was looked at.
    excluded: tuple[tuple[ObservationInput, str], ...]
    #: Absolute difference between the two most-asserted values, when numeric.
    divergence: Decimal | None

    @property
    def has_disagreement(self) -> bool:
        return len(self.candidates) > 1

    @property
    def distinct_values(self) -> tuple[object, ...]:
        return tuple(candidate.value for candidate in self.candidates)


def group_unranked(
    observations: tuple[ObservationInput, ...],
) -> tuple[Candidate, ...]:
    """Group observations by canonical value, without weighting them.

    Ordered by canonical key so the output is reproducible. That order carries
    no claim about which value is more likely — establishing that is what the
    missing specification is for.
    """
    grouped: dict[str, list[ObservationInput]] = {}
    for observation in observations:
        grouped.setdefault(value_key(observation.value), []).append(observation)

    return tuple(
        Candidate(
            value=grouped[key][0].value,
            value_key=key,
            weight=UNWEIGHTED,
            share=UNWEIGHTED,
            observations=tuple(sorted(grouped[key], key=lambda o: str(o.observation_id))),
        )
        for key in sorted(grouped)
    )


def detect_without_scoring(
    *,
    attribute: str,
    eligible: tuple[ObservationInput, ...],
    excluded: tuple[tuple[ObservationInput, str], ...],
    specification: ConfidenceSpecification,
) -> DetectionResult:
    """Establish what the evidence shows, without deciding what is true."""
    candidates = group_unranked(eligible)
    divergence = numeric_divergence(candidates) if len(candidates) > 1 else None

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

    # No evidence roles are assigned. "Supporting" and "dissenting" are defined
    # relative to a selected value, and no value has been selected — labelling
    # observations either way would smuggle in a verdict the engine has not
    # reached. The candidates carry their own observations; that is the whole
    # of what is known.
    return DetectionResult(
        attribute=attribute,
        candidates=candidates,
        conflicts=tuple(f for f in findings if f is not None),
        excluded=excluded,
        divergence=divergence,
    )
