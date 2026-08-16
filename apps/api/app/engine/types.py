"""Reality Engine domain types.

The engine is a **pure function of its inputs**. These types are what it takes
and what it returns; nothing here touches a database, a clock or a network.

That constraint is not stylistic. "Deterministic and reproducible" means the
same observations must produce the same state today and in a year, on any
machine. A wall-clock read inside the calculation would break that instantly —
so the current time is an *input* (``as_of``), passed in by the caller, and
appears nowhere else.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.models.reality_state import EvidenceRole, RealityStatus


class SourceAuthority(enum.StrEnum):
    """How authoritative a source is for a given attribute.

    Authority is a property of the *source*, declared by the operator — not
    something the engine infers from the data. A source that agrees with the
    majority is not thereby more reliable; it may simply be copying from the
    same upstream.
    """

    #: The system of record. What it says is definitionally true.
    AUTHORITATIVE = "authoritative"
    #: A primary operational system.
    PRIMARY = "primary"
    #: A downstream copy, cache or report.
    SECONDARY = "secondary"
    #: Manual entry, spreadsheets, anything unverified.
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class ObservationInput:
    """One observation, as the engine sees it.

    A flattened projection of the ORM row: the engine never receives a
    SQLAlchemy object, so it can be exercised with literals and cannot
    accidentally trigger a lazy load mid-calculation.
    """

    observation_id: uuid.UUID
    source_id: uuid.UUID
    stream_id: uuid.UUID
    external_id: str

    #: The asserted value, already in canonical normalised form.
    value: Any

    #: When the fact was true, per the source.
    event_time: datetime
    #: When RealitySync learned it. Deliberately separate — see the module
    #: docstring in app/models/observation.py.
    ingested_at: datetime
    event_time_semantics: str

    #: Declared authority of the source this came from.
    authority: SourceAuthority = SourceAuthority.SECONDARY

    #: 0..1. Declared per source, not inferred. ``None`` means no reliability
    #: has been declared for this source.
    #:
    #: There is deliberately no default number. A previous version defaulted to
    #: ``0.5`` - "the neutral midpoint" - which is an invented reliability value
    #: sitting in production code. It was harmless only because weighting raised
    #: before reaching it; the day the Phase 0 specification arrives it would
    #: silently become load-bearing and every unconfigured source would score as
    #: though someone had assessed it. ``None`` cannot do that: the scoring path
    #: must handle absence explicitly or fail loudly.
    reliability: Decimal | None = None

    #: 0..1. How complete and well-formed this observation is. ``None`` for the
    #: same reason as ``reliability``: its derivation is one of the missing
    #: specifications, so there is no defensible number to default to.
    quality: Decimal | None = None

    #: Whether the value passed the attribute's validation rules. A failing
    #: observation is kept as evidence and excluded from selection — dropping
    #: it would hide the fact that a source is emitting bad data.
    validation_passed: bool = True

    source_name: str = ""


@dataclass(frozen=True, slots=True)
class Candidate:
    """One distinct value asserted by at least one observation.

    ``weight`` is the sum of its supporting observations' weights, and
    ``share`` is that weight as a fraction of all candidate weight — which is
    what the winning margin is measured in.
    """

    value: Any
    #: Stable, sortable rendering of `value`, used for grouping and for
    #: deterministic tie-breaking.
    value_key: str
    weight: Decimal
    share: Decimal
    observations: tuple[ObservationInput, ...]

    @property
    def source_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(sorted({o.source_id for o in self.observations}, key=str))

    @property
    def latest_event_time(self) -> datetime:
        return max(o.event_time for o in self.observations)

    @property
    def best_authority(self) -> SourceAuthority:
        return min(
            (o.authority for o in self.observations),
            key=lambda a: _AUTHORITY_ORDER[a],
        )


#: Lower is more authoritative. Used for tie-breaking, so it must be total.
_AUTHORITY_ORDER: dict[SourceAuthority, int] = {
    SourceAuthority.AUTHORITATIVE: 0,
    SourceAuthority.PRIMARY: 1,
    SourceAuthority.SECONDARY: 2,
    SourceAuthority.UNVERIFIED: 3,
}


def authority_rank(authority: SourceAuthority) -> int:
    return _AUTHORITY_ORDER[authority]


@dataclass(frozen=True, slots=True)
class FactorBreakdown:
    """One weighted factor's contribution to the Base score."""

    name: str
    value: Decimal
    weight: Decimal

    @property
    def contribution(self) -> Decimal:
        return self.value * self.weight

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "value": str(self.value),
            "weight": str(self.weight),
            "contribution": str(self.contribution),
        }


@dataclass(frozen=True, slots=True)
class PenaltyBreakdown:
    """One multiplicative penalty applied after the Base score."""

    name: str
    multiplier: Decimal
    reason: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "multiplier": str(self.multiplier),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ConfidenceResult:
    """A confidence score and everything that produced it.

    Every term is retained so the number can be recomputed by hand from the
    stored breakdown. A score without its derivation is an assertion, and this
    product exists to not make those.
    """

    score: Decimal
    ceiling: Decimal
    base: Decimal
    factors: tuple[FactorBreakdown, ...]
    penalties: tuple[PenaltyBreakdown, ...]
    algorithm_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "score": str(self.score),
            "ceiling": str(self.ceiling),
            "base": str(self.base),
            "factors": [f.as_dict() for f in self.factors],
            "penalties": [p.as_dict() for p in self.penalties],
            "formula": "score = 100 x ceiling x base x product(penalties), capped at 99",
        }


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """One observation's role in the outcome."""

    observation: ObservationInput
    role: EvidenceRole
    weight: Decimal
    exclusion_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ConflictFinding:
    """A disagreement the engine detected. Never alters the selected value.

    ``score`` is None when the conflict-score formula is unspecified. The
    finding is still reported: "these sources disagree" is useful on its own,
    and withholding it because a constant is missing would hide a real problem.
    """

    conflict_type: str
    severity: str
    score: Decimal | None
    summary: str
    fingerprint: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfidenceAbsence:
    """Why a state carries no confidence score.

    Recorded on the state itself rather than inferred from a null, so an API
    consumer is told *what* is missing instead of being left to guess whether
    the field is absent, broken, or genuinely zero.
    """

    #: Identifier of the missing specification, from MISSING_SPECIFICATIONS.
    missing: str
    detail: str
    #: Everything still outstanding, so one blocked state answers "what would
    #: it take to unblock this" completely.
    outstanding: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": False,
            "reason": "specification_unavailable",
            "blocked_on": self.missing,
            "detail": self.detail,
            "missing_specifications": [
                {"name": name, "description": description} for name, description in self.outstanding
            ],
        }


@dataclass(frozen=True, slots=True)
class RealityCalculation:
    """The engine's output for one (entity, attribute).

    Returned rather than written: persistence is a separate, boring step, and
    keeping it out of the calculation is what allows the engine to be tested
    without a database.

    Two fields are deliberately optional, and they are optional for *different*
    reasons. Conflating them would be the easiest way to turn "we do not know"
    into a claim.

    ``confidence`` is ``None`` when the scoring specification is unavailable.
    Not zero - zero is a score, and asserting one would be inventing the very
    formula that is missing.

    ``value`` is ``None`` in two distinguishable situations, told apart by
    ``status``:

    * ``UNKNOWN``   - no usable observation exists. An honest absence.
    * ``CONTESTED`` - observations exist and disagree, and choosing between
      them requires the weighting formula. The candidates are all recorded;
      what is missing is the rule for ranking them. Picking the
      alphabetically-first would be a fabricated verdict.
    """

    attribute: str
    value: Any
    status: RealityStatus
    #: None when the specification is unavailable. See the class docstring.
    confidence: ConfidenceResult | None
    selection_reason: str
    candidates: tuple[Candidate, ...]
    evidence: tuple[EvidenceEntry, ...]
    conflicts: tuple[ConflictFinding, ...]
    valid_from: datetime
    calculated_as_of: datetime
    #: Why no confidence exists, when it does not. Machine-readable so the API
    #: can report the blockage rather than an empty field.
    confidence_unavailable: ConfidenceAbsence | None = None
    #: True when `value` is the engine's selection. False means no value could
    #: be selected - see the class docstring for which of the two cases applies.
    value_selected: bool = True

    @property
    def is_scored(self) -> bool:
        return self.confidence is not None

    @property
    def supporting_count(self) -> int:
        return sum(1 for e in self.evidence if e.role is EvidenceRole.SUPPORTING)

    @property
    def dissenting_count(self) -> int:
        return sum(1 for e in self.evidence if e.role is EvidenceRole.DISSENTING)

    @property
    def source_count(self) -> int:
        return len({e.observation.source_id for e in self.evidence})
