"""A test double for the missing confidence specification.

**This is not a candidate implementation and must never become one.**

The Phase 0 confidence specification is unrecoverable. This double exists so
the engine's *mechanics* — grouping, supersession, tie-breaking, evidence
assembly, conflict detection, determinism — can be exercised. Its numbers are
deliberately trivial and obviously artificial:

* ``freshness`` returns a constant, so age cannot influence any ranking
* ``quality`` returns the declared value unchanged
* ``agreement`` returns a constant
* every penalty returns 1 (no-op)

Because they are constants, no test here can accidentally assert something
about the approved curve. A test that passes with a constant freshness proves
the *structure* is right, and proves nothing about the formula — which is
exactly the separation required while the real values are missing.

If you find yourself tempted to tune a number in this file so a test produces a
particular confidence, stop: that is the reverse-engineering this project
explicitly refuses.
"""

from __future__ import annotations

from decimal import Decimal


class MechanicalSpecification:
    """Constant-valued stand-in. Exercises structure, asserts nothing about values."""

    def __init__(
        self,
        *,
        freshness: Decimal = Decimal("1.0"),
        agreement: Decimal = Decimal("1.0"),
        penalty_multiplier: Decimal = Decimal("1.0"),
        conflict_score: Decimal | None = None,
        contested_threshold: Decimal | None = None,
    ) -> None:
        self._freshness = freshness
        self._agreement = agreement
        self._penalty = penalty_multiplier
        self._conflict_score = conflict_score
        self._contested_threshold = contested_threshold

    def freshness(self, age_hours: Decimal) -> Decimal:
        # Constant: age deliberately cannot influence weight, so no test can
        # come to depend on a decay curve that has not been specified.
        return self._freshness

    def quality(self, declared_quality: Decimal, validation_passed: bool) -> Decimal:
        return declared_quality if validation_passed else Decimal(0)

    def agreement(self, winning_share: Decimal, candidate_count: int) -> Decimal:
        return self._agreement

    def reliability_for_authority(self, authority: str) -> Decimal:
        # Reliability is an explicit per-observation input in these tests, so
        # this is never consulted.
        raise NotImplementedError("reliability is supplied per observation in tests")

    def penalty(self, name: str, context: dict[str, object]) -> Decimal:
        return self._penalty

    def conflict_score(self, context: dict[str, object]) -> Decimal:
        if self._conflict_score is None:
            from app.engine.spec import SpecificationUnavailableError

            raise SpecificationUnavailableError("conflict_score")
        return self._conflict_score

    def severity_for_score(self, score: Decimal) -> str:
        if self._conflict_score is None:
            from app.engine.spec import SpecificationUnavailableError

            raise SpecificationUnavailableError("severity_thresholds")
        return "high"

    def contested_margin_threshold(self) -> Decimal:
        if self._contested_threshold is None:
            from app.engine.spec import SpecificationUnavailableError

            raise SpecificationUnavailableError("margin_definition")
        return self._contested_threshold
