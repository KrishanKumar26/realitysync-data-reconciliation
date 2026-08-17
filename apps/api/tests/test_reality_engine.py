"""Reality Engine — behaviour whose specification is known.

Everything asserted here follows from a confirmed rule: the Ceiling formula,
the Base weights, the 0-99 bound, bitemporal supersession, deterministic
tie-breaking, evidence completeness, and categorical conflict detection.

Nothing here asserts a confidence *value* against the approved formula. The
sub-formulas are unrecoverable, so a test claiming "confidence is 71.0" would
be asserting against a guess. The golden test is written and skipped instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.engine.confidence import compute_base, compute_ceiling
from app.engine.engine import calculate
from app.engine.selection import (
    build_candidates,
    latest_per_source,
    numeric_divergence,
    selection_margin,
    value_key,
)
from app.engine.spec import (
    CEILING_CAP,
    MAX_CONFIDENCE,
    MISSING_SPECIFICATIONS,
    UNAVAILABLE_SPECIFICATION,
    WEIGHT_AGREEMENT,
    WEIGHT_FRESHNESS,
    WEIGHT_QUALITY,
    WEIGHT_RELIABILITY,
    SpecificationUnavailableError,
)
from app.engine.types import ObservationInput, SourceAuthority
from app.models.reality_state import EvidenceRole, RealityStatus
from tests.engine_spec_double import MechanicalSpecification

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)

SOURCE_A = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
SOURCE_B = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
SOURCE_C = uuid.UUID("cccccccc-0000-0000-0000-000000000003")
STREAM = uuid.UUID("11111111-0000-0000-0000-000000000001")


def observation(
    *,
    source: uuid.UUID,
    value: object,
    event_time: datetime = T0,
    ingested_at: datetime | None = None,
    reliability: str = "0.6",
    quality: str = "1.0",
    authority: SourceAuthority = SourceAuthority.SECONDARY,
    validation_passed: bool = True,
    observation_id: uuid.UUID | None = None,
) -> ObservationInput:
    return ObservationInput(
        observation_id=observation_id or uuid.uuid4(),
        source_id=source,
        stream_id=STREAM,
        external_id="record_id=1",
        value=value,
        event_time=event_time,
        ingested_at=ingested_at or event_time,
        event_time_semantics="observed",
        authority=authority,
        reliability=Decimal(reliability),
        quality=Decimal(quality),
        validation_passed=validation_passed,
    )


def run(observations, spec=None, as_of=NOW, attribute="quantity"):  # type: ignore[no-untyped-def]
    return calculate(
        attribute=attribute,
        observations=tuple(observations),
        as_of=as_of,
        specification=spec or MechanicalSpecification(),
    )


# --- The unavailable specification -----------------------------------------


def test_engine_refuses_to_score_without_a_specification() -> None:
    """No fallback, no plausible default.

    A guessed confidence would be stored, displayed and believed, and nothing
    about it would look wrong. The engine returns blocked instead.
    """
    # CHANGED IN PHASE 9. This test previously asserted that the engine
    # returned a CalculationBlocked and produced nothing at all. That behaviour
    # was obsolete rather than wrong: withholding the *score* is correct, but
    # Phase 5 also withheld the selection, evidence and provenance that need no
    # formula, which left reality_states empty in every deployment.
    #
    # The property under test is unchanged and is what still matters: no
    # confidence is invented. It is now asserted as an absent score with a
    # stated reason rather than as an absent result.
    result = run(
        [observation(source=SOURCE_A, value=42)],
        spec=UNAVAILABLE_SPECIFICATION,
    )

    assert result.confidence is None
    assert result.confidence_unavailable is not None
    assert result.confidence_unavailable.missing == "freshness"
    assert "not specified" in result.confidence_unavailable.detail


def test_blocked_result_names_every_missing_input() -> None:
    """ "What is blocking this" must be answerable from the system."""
    result = run([observation(source=SOURCE_A, value=42)], spec=UNAVAILABLE_SPECIFICATION)
    absence = result.confidence_unavailable
    assert absence is not None

    names = {entry["name"] for entry in absence.as_dict()["missing_specifications"]}  # type: ignore[index]

    assert "freshness" in names
    assert "conflict_score" in names
    assert "laptop_001_scenario" in names
    assert len(names) == len(MISSING_SPECIFICATIONS)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("freshness", (Decimal(1),)),
        ("quality", (Decimal(1), True)),
        ("agreement", (Decimal(1), 2)),
        ("reliability_for_authority", ("primary",)),
        ("penalty", ("coverage", {})),
        ("conflict_score", ({},)),
        ("severity_for_score", (Decimal("0.5"),)),
        ("contested_margin_threshold", ()),
    ],
)
def test_every_unspecified_sub_formula_raises(method: str, args: tuple[object, ...]) -> None:
    """No sub-formula has a quiet default. Every one refuses."""
    with pytest.raises(SpecificationUnavailableError):
        getattr(UNAVAILABLE_SPECIFICATION, method)(*args)


# --- Ceiling (confirmed formula) -------------------------------------------


def test_ceiling_combines_independent_sources() -> None:
    """1 - product(1 - R). Two 0.6 sources give 0.84, not 1.2."""
    assert compute_ceiling((Decimal("0.6"), Decimal("0.6"))) == Decimal("0.840000")


def test_ceiling_is_capped_at_the_approved_bound() -> None:
    """Many strong sources approach certainty but never reach it."""
    ceiling = compute_ceiling(tuple(Decimal("0.99") for _ in range(20)))

    assert ceiling == CEILING_CAP
    assert ceiling < Decimal(1)


def test_a_single_source_ceiling_is_its_own_reliability() -> None:
    assert compute_ceiling((Decimal("0.8"),)) == Decimal("0.800000")


def test_no_sources_gives_a_zero_ceiling() -> None:
    assert compute_ceiling(()) == Decimal(0)


# --- Base (confirmed weights) ----------------------------------------------


def test_base_uses_the_approved_weights() -> None:
    """0.40 reliability + 0.30 freshness + 0.15 quality + 0.15 agreement."""
    base, factors = compute_base(
        reliability=Decimal(1),
        freshness=Decimal(1),
        quality=Decimal(1),
        agreement=Decimal(1),
    )

    assert base == Decimal("1.000000")
    assert {f.name: f.weight for f in factors} == {
        "reliability": WEIGHT_RELIABILITY,
        "freshness": WEIGHT_FRESHNESS,
        "quality": WEIGHT_QUALITY,
        "agreement": WEIGHT_AGREEMENT,
    }


def test_each_factor_contributes_exactly_its_weight() -> None:
    """Isolating one factor at 1 yields precisely that weight."""
    base, _ = compute_base(
        reliability=Decimal(1),
        freshness=Decimal(0),
        quality=Decimal(0),
        agreement=Decimal(0),
    )
    assert base == Decimal("0.400000")


def test_confidence_can_never_exceed_the_cap() -> None:
    """Perfect inputs still stop at 99."""
    result = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.99"),
            observation(source=SOURCE_B, value=42, reliability="0.99"),
            observation(source=SOURCE_C, value=42, reliability="0.99"),
        ]
    )

    assert result.is_scored
    assert result.confidence.score <= MAX_CONFIDENCE


# --- Determinism -----------------------------------------------------------


def test_repeated_calculation_is_byte_identical() -> None:
    """The core guarantee. Same inputs, same output, every time."""
    observations = [
        observation(source=SOURCE_A, value=42, observation_id=uuid.UUID(int=1)),
        observation(source=SOURCE_B, value=57, observation_id=uuid.UUID(int=2)),
        observation(source=SOURCE_C, value=42, observation_id=uuid.UUID(int=3)),
    ]

    first = run(observations)
    second = run(observations)

    assert first.is_scored
    assert second.is_scored
    assert first.value == second.value
    assert first.confidence.score == second.confidence.score
    assert first.confidence.as_dict() == second.confidence.as_dict()
    assert first.selection_reason == second.selection_reason
    assert [c.value_key for c in first.candidates] == [c.value_key for c in second.candidates]


def test_input_order_does_not_change_the_result() -> None:
    """Non-determinism usually enters as an ordering dependency."""
    observations = [
        observation(source=SOURCE_A, value=42, observation_id=uuid.UUID(int=1)),
        observation(source=SOURCE_B, value=57, observation_id=uuid.UUID(int=2)),
        observation(source=SOURCE_C, value=42, observation_id=uuid.UUID(int=3)),
    ]

    forward = run(observations)
    reverse = run(list(reversed(observations)))

    assert forward.is_scored
    assert reverse.is_scored
    assert forward.value == reverse.value
    assert forward.confidence.score == reverse.confidence.score
    assert [c.value_key for c in forward.candidates] == [c.value_key for c in reverse.candidates]


def test_the_engine_never_reads_a_clock() -> None:
    """`as_of` is an argument, so a state is reproducible years later.

    Two runs at different notional "now" values with a constant freshness
    produce the same selection — proof the calculation depends on its inputs
    rather than on when it happened to run.
    """
    observations = [observation(source=SOURCE_A, value=42, observation_id=uuid.UUID(int=1))]

    now = run(observations, as_of=NOW)
    much_later = run(observations, as_of=NOW + timedelta(days=3650))

    assert now.is_scored
    assert much_later.is_scored
    assert now.value == much_later.value == 42
    assert now.confidence.score == much_later.confidence.score


# --- Tie-breaking ----------------------------------------------------------


def test_equal_weights_are_broken_by_authority() -> None:
    """A system of record outranks a downstream copy at equal weight."""
    result = run(
        [
            observation(
                source=SOURCE_A,
                value="from_copy",
                authority=SourceAuthority.SECONDARY,
                reliability="0.6",
            ),
            observation(
                source=SOURCE_B,
                value="from_record",
                authority=SourceAuthority.AUTHORITATIVE,
                reliability="0.6",
            ),
        ]
    )

    assert result.is_scored
    assert result.value == "from_record"


def test_equal_weight_and_authority_are_broken_by_event_time() -> None:
    result = run(
        [
            observation(source=SOURCE_A, value="older", event_time=T0),
            observation(source=SOURCE_B, value="newer", event_time=T0 + timedelta(hours=5)),
        ]
    )

    assert result.is_scored
    assert result.value == "newer"


def test_a_total_tie_is_still_resolved_deterministically() -> None:
    """Identical in every respect except value.

    The final tie-break is the canonical value key — arbitrary but total, so
    the outcome can never depend on dict or set iteration order.
    """

    def scenario() -> list[ObservationInput]:
        return [
            observation(
                source=SOURCE_A,
                value="zebra",
                observation_id=uuid.UUID(int=1),
                event_time=T0,
            ),
            observation(
                source=SOURCE_B,
                value="alpha",
                observation_id=uuid.UUID(int=2),
                event_time=T0,
            ),
        ]

    results = {run(scenario()).value for _ in range(10)}  # type: ignore[union-attr]

    assert len(results) == 1
    assert results == {"alpha"}


# --- Bitemporal behaviour --------------------------------------------------


def test_a_source_is_superseded_by_its_own_newer_observation() -> None:
    old = observation(source=SOURCE_A, value="old", event_time=T0)
    new = observation(source=SOURCE_A, value="new", event_time=T0 + timedelta(hours=1))

    kept, superseded = latest_per_source((old, new))

    assert kept == (new,)
    assert superseded[0][0] is old
    assert "superseded" in superseded[0][1]


def test_late_arrival_does_not_displace_a_newer_event() -> None:
    """The classic pipeline bug, guarded.

    A backfill ingested today describing last week must not override an
    observation describing yesterday. Supersession is by event time.
    """
    current = observation(
        source=SOURCE_A,
        value="current",
        event_time=T0 + timedelta(days=7),
        ingested_at=T0 + timedelta(days=7),
    )
    backfill = observation(
        source=SOURCE_A,
        value="stale_backfill",
        event_time=T0,
        # Arrives much later than the observation it must not displace.
        ingested_at=T0 + timedelta(days=30),
    )

    result = run([current, backfill])

    assert result.is_scored
    assert result.value == "current"


def test_ingestion_time_breaks_ties_at_the_same_event_time() -> None:
    """A later correction of the same instant is the newer belief."""
    first = observation(source=SOURCE_A, value="first", event_time=T0, ingested_at=T0)
    correction = observation(
        source=SOURCE_A,
        value="correction",
        event_time=T0,
        ingested_at=T0 + timedelta(hours=6),
    )

    kept, _ = latest_per_source((first, correction))

    assert kept[0].value == "correction"


def test_valid_from_is_an_event_time_not_a_calculation_time() -> None:
    result = run([observation(source=SOURCE_A, value=42, event_time=T0)])

    assert result.is_scored
    assert result.valid_from == T0
    assert result.calculated_as_of == NOW
    assert result.valid_from != result.calculated_as_of


# --- Candidate grouping ----------------------------------------------------


def test_values_group_by_canonical_form_not_python_equality() -> None:
    """12.500 and 12.5 are different claims about precision."""
    assert value_key("12.500") != value_key("12.5")


def test_key_order_does_not_split_an_identical_object() -> None:
    assert value_key({"a": 1, "b": 2}) == value_key({"b": 2, "a": 1})


def test_agreeing_sources_combine_into_one_candidate() -> None:
    result = run(
        [
            observation(source=SOURCE_A, value=42),
            observation(source=SOURCE_B, value=42),
        ]
    )

    assert result.is_scored
    assert len(result.candidates) == 1
    assert result.candidates[0].share == Decimal("1.0000")
    assert result.status is RealityStatus.CONFIRMED


def test_margin_is_the_share_gap_in_percentage_points() -> None:
    weighted = (
        (observation(source=SOURCE_A, value="win"), Decimal("0.6")),
        (observation(source=SOURCE_B, value="lose"), Decimal("0.4")),
    )
    candidates = build_candidates(weighted)

    assert selection_margin(candidates) == Decimal("20.00")


def test_a_single_candidate_has_no_contest() -> None:
    weighted = ((observation(source=SOURCE_A, value="only"), Decimal("0.6")),)

    assert selection_margin(build_candidates(weighted)) == Decimal("100.00")


def test_numeric_divergence_is_reported_in_attribute_units() -> None:
    """42 vs 57 diverge by 15 — the units the golden test speaks in."""
    weighted = (
        (observation(source=SOURCE_A, value=42), Decimal("0.6")),
        (observation(source=SOURCE_B, value=57), Decimal("0.4")),
    )

    assert numeric_divergence(build_candidates(weighted)) == Decimal(15)


def test_divergence_is_undefined_for_non_numeric_values() -> None:
    weighted = (
        (observation(source=SOURCE_A, value="shipped"), Decimal("0.6")),
        (observation(source=SOURCE_B, value="pending"), Decimal("0.4")),
    )

    assert numeric_divergence(build_candidates(weighted)) is None


def test_booleans_are_not_treated_as_numbers() -> None:
    """bool subclasses int; a 'divergence' between two flags is meaningless."""
    weighted = (
        (observation(source=SOURCE_A, value=True), Decimal("0.6")),
        (observation(source=SOURCE_B, value=False), Decimal("0.4")),
    )

    assert numeric_divergence(build_candidates(weighted)) is None


# --- Conflicting observations ----------------------------------------------


def test_disagreement_produces_a_contested_state() -> None:
    result = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.7"),
            observation(source=SOURCE_B, value=57, reliability="0.3"),
        ]
    )

    assert result.is_scored
    assert result.status is RealityStatus.CONTESTED
    assert result.value == 42
    assert len(result.candidates) == 2


def test_a_value_conflict_is_detected_without_any_specification() -> None:
    """Detection is categorical; only grading needs the missing constants."""
    result = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.7"),
            observation(source=SOURCE_B, value=57, reliability="0.3"),
        ]
    )

    assert result.is_scored
    conflict = next(c for c in result.conflicts if c.conflict_type == "value_conflict")
    assert conflict.details["divergence"] == "15"


def test_an_ungraded_conflict_is_recorded_rather_than_scored_zero() -> None:
    """A 0 score would read as "harmless"; unspecified says what is true."""
    result = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.7"),
            observation(source=SOURCE_B, value=57, reliability="0.3"),
        ]
    )

    assert result.is_scored
    conflict = next(c for c in result.conflicts if c.conflict_type == "value_conflict")
    assert conflict.score is None
    assert conflict.severity == "unspecified"


def test_conflicts_are_graded_once_a_specification_exists() -> None:
    spec = MechanicalSpecification(conflict_score=Decimal("0.594"))
    result = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.7"),
            observation(source=SOURCE_B, value=57, reliability="0.3"),
        ],
        spec=spec,
    )

    assert result.is_scored
    conflict = next(c for c in result.conflicts if c.conflict_type == "value_conflict")
    assert conflict.score == Decimal("0.594")
    assert conflict.severity == "high"


def test_independent_sources_disagreeing_is_flagged_separately() -> None:
    result = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.7"),
            observation(source=SOURCE_B, value=57, reliability="0.3"),
        ]
    )

    assert result.is_scored
    assert any(c.conflict_type == "source_disagreement" for c in result.conflicts)


def test_conflict_fingerprints_are_stable_across_runs() -> None:
    """So re-running updates in place instead of accumulating duplicates."""
    observations = [
        observation(source=SOURCE_A, value=42, observation_id=uuid.UUID(int=1)),
        observation(source=SOURCE_B, value=57, observation_id=uuid.UUID(int=2)),
    ]

    first = run(observations)
    second = run(observations)

    assert first.is_scored
    assert second.is_scored
    assert [c.fingerprint for c in first.conflicts] == [c.fingerprint for c in second.conflicts]


def test_contested_state_detection_needs_the_margin_threshold() -> None:
    """Without it the engine declines to guess where "too close" begins."""
    without = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.51"),
            observation(source=SOURCE_B, value=57, reliability="0.49"),
        ]
    )
    assert without.is_scored
    assert not any(c.conflict_type == "contested_state" for c in without.conflicts)

    with_threshold = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.51"),
            observation(source=SOURCE_B, value=57, reliability="0.49"),
        ],
        spec=MechanicalSpecification(
            conflict_score=Decimal("0.5"), contested_threshold=Decimal("20.00")
        ),
    )
    assert with_threshold.is_scored
    assert any(c.conflict_type == "contested_state" for c in with_threshold.conflicts)


def test_conflicts_never_change_the_selected_value() -> None:
    """The one-way dependency, asserted.

    Grading a conflict must not feed back into selection, or the state would
    depend on the order conflicts were processed.
    """
    observations = [
        observation(source=SOURCE_A, value=42, reliability="0.7"),
        observation(source=SOURCE_B, value=57, reliability="0.3"),
    ]

    ungraded = run(observations)
    graded = run(observations, spec=MechanicalSpecification(conflict_score=Decimal("0.9")))

    assert ungraded.is_scored
    assert graded.is_scored
    assert ungraded.value == graded.value == 42


# --- Provenance ------------------------------------------------------------


def test_every_observation_appears_in_the_evidence() -> None:
    """Nothing considered is left unrecorded."""
    observations = [
        observation(source=SOURCE_A, value=42, observation_id=uuid.UUID(int=1)),
        observation(source=SOURCE_B, value=57, observation_id=uuid.UUID(int=2)),
        observation(
            source=SOURCE_A,
            value="older",
            event_time=T0 - timedelta(days=1),
            observation_id=uuid.UUID(int=3),
        ),
    ]

    result = run(observations)

    assert result.is_scored
    assert {e.observation.observation_id for e in result.evidence} == {
        uuid.UUID(int=1),
        uuid.UUID(int=2),
        uuid.UUID(int=3),
    }


def test_evidence_separates_support_dissent_and_exclusion() -> None:
    result = run(
        [
            observation(source=SOURCE_A, value=42, observation_id=uuid.UUID(int=1)),
            observation(source=SOURCE_B, value=57, observation_id=uuid.UUID(int=2)),
            observation(
                source=SOURCE_A,
                value="superseded",
                event_time=T0 - timedelta(days=1),
                observation_id=uuid.UUID(int=3),
            ),
        ]
    )

    assert result.is_scored
    roles = {e.observation.observation_id: e.role for e in result.evidence}

    assert roles[uuid.UUID(int=1)] is EvidenceRole.SUPPORTING
    assert roles[uuid.UUID(int=2)] is EvidenceRole.DISSENTING
    assert roles[uuid.UUID(int=3)] is EvidenceRole.EXCLUDED


def test_an_excluded_observation_records_why() -> None:
    result = run(
        [
            observation(source=SOURCE_A, value=42, observation_id=uuid.UUID(int=1)),
            observation(
                source=SOURCE_B,
                value="bad",
                validation_passed=False,
                observation_id=uuid.UUID(int=2),
            ),
        ]
    )

    assert result.is_scored
    excluded = next(e for e in result.evidence if e.observation.observation_id == uuid.UUID(int=2))
    assert excluded.role is EvidenceRole.EXCLUDED
    assert excluded.exclusion_reason == "validation_failed"


def test_a_failing_observation_is_kept_as_evidence_not_dropped() -> None:
    """Dropping it would hide that a source is emitting bad data."""
    result = run(
        [
            observation(source=SOURCE_A, value=42),
            observation(source=SOURCE_B, value="bad", validation_passed=False),
        ]
    )

    assert result.is_scored
    assert len(result.evidence) == 2
    assert result.value == 42


def test_the_selection_reason_states_why_without_ai() -> None:
    result = run(
        [
            observation(source=SOURCE_A, value=42, reliability="0.7"),
            observation(source=SOURCE_B, value=57, reliability="0.3"),
        ]
    )

    assert result.is_scored
    assert "competing values" in result.selection_reason
    assert "percentage points" in result.selection_reason


def test_the_breakdown_allows_the_score_to_be_rechecked_by_hand() -> None:
    result = run([observation(source=SOURCE_A, value=42, reliability="0.8")])

    assert result.is_scored
    breakdown = result.confidence.as_dict()

    assert set(breakdown) >= {"ceiling", "base", "factors", "penalties", "formula"}
    assert len(breakdown["factors"]) == 4  # type: ignore[arg-type]
    recomputed = sum(
        Decimal(f["contribution"])
        for f in breakdown["factors"]  # type: ignore[index,union-attr]
    )
    assert recomputed == Decimal(breakdown["base"])  # type: ignore[arg-type]


# --- Empty and degenerate inputs -------------------------------------------


def test_no_observations_gives_an_honest_unknown() -> None:
    """Not a guess, and not an error — an absence, stated.

    CHANGED IN PHASE 9. This previously asserted ``confidence.score == 0.0`` on
    the reasoning that there is nothing to be confident about. That was the one
    place the codebase converted an unavailable confidence into a number: 0.0
    is a *score*, and asserting one claims what the missing formula would have
    produced for an empty evidence set. Nobody knows that. The absence is now
    represented as an absence.
    """
    result = run([])

    assert result.status is RealityStatus.UNKNOWN
    assert result.value is None
    assert result.value_selected is False
    assert result.confidence is None, "UNKNOWN must not carry a fabricated score"
    assert result.confidence_unavailable is not None
    assert result.evidence == ()


def test_only_invalid_observations_gives_unknown_with_evidence() -> None:
    result = run([observation(source=SOURCE_A, value="bad", validation_passed=False)])

    assert result.status is RealityStatus.UNKNOWN
    assert len(result.evidence) == 1
    assert result.evidence[0].role is EvidenceRole.EXCLUDED


def test_unknown_needs_no_specification() -> None:
    """ "We have nothing" is knowable without the missing constants."""
    result = run([], spec=UNAVAILABLE_SPECIFICATION)

    assert result.status is RealityStatus.UNKNOWN
    # And still no score, because "what confidence does an empty evidence set
    # carry" is itself part of the missing specification.
    assert result.confidence is None


# --- Golden test -----------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "BLOCKED: the LAPTOP-001 scenario and the confidence sub-formulas are "
        "part of the Phase 0 specification, which an exhaustive search of all "
        "branches, tags, refs, stashes, reflogs, dangling objects, committed "
        "files, docs and the surrounding filesystem could not recover. "
        "Constructing inputs that produce 71.0% would fit the data to the "
        "answer and verify nothing. See app/engine/spec.py MISSING_SPECIFICATIONS."
    )
)
def test_golden_laptop_001() -> None:
    """The authoritative golden test.

    Expected, per the Phase 4 brief::

        value          = 42
        confidence     = 71.0%
        status         = CONTESTED
        value conflict = HIGH
        conflict score = 0.594
        divergence     = 15 units
        margin         = 0.78%

    Two of these the engine already produces from confirmed rules: a divergence
    of 15 between 42 and 57, and CONTESTED whenever sources disagree. The rest
    require the sub-formulas.

    To enable this test, supply the LAPTOP-001 observations and the sub-formula
    definitions, implement them in place of UNAVAILABLE_SPECIFICATION, and
    remove this skip.
    """
    raise AssertionError("Golden scenario not available")


def test_ungraded_conflict_summary_claims_no_winner() -> None:
    """Without the weighting specification, nothing "leads" anything.

    Every candidate carries the same weight while the specification is
    missing, so the margin is zero — and the summary rendered that as
    "'42' leads '57' by 0 percentage points", asserting a ranking the evidence
    does not support and that the engine had just explicitly refused to make.
    Seen live on the conflicts screen.
    """
    result = run(
        [
            observation(source=SOURCE_A, value=42),
            observation(source=SOURCE_B, value=57),
        ]
    )

    value_conflicts = [c for c in result.conflicts if c.conflict_type == "value_conflict"]
    assert value_conflicts, "two distinct values must produce a value conflict"
    summary = value_conflicts[0].summary

    assert "leads" not in summary
    assert "percentage points" not in summary
    assert "Neither one is treated as more correct" in summary
    # The values and the gap are still reported; only the ranking is withheld.
    assert "2 different answers" in summary
