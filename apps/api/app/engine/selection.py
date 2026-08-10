"""Deterministic candidate selection.

Given observations, decide which value wins. Every step is total and ordered:
run it twice on the same input and it returns the same answer, byte for byte.

Three things make that true, and each is easy to get wrong:

**Canonical value keys.** Values are grouped by a canonical JSON rendering, not
by Python equality. ``12.500`` and ``12.5`` are different assertions about
precision and must not silently merge; ``{"a":1,"b":2}`` and ``{"b":2,"a":1}``
are the same assertion and must not split.

**Bitemporal supersession.** Within one source, a later observation replaces an
earlier one — but *later by event time*, not by arrival. An observation
ingested today describing last week does not supersede one describing
yesterday. Getting this backwards is the classic pipeline bug: it makes a
late-arriving correction look like the newest truth.

**Total tie-breaking.** When two candidates weigh exactly the same, the winner
is decided by an explicit ordered chain ending in a value comparison that can
never tie. No dict ordering, no set iteration, no ``max()`` over an unordered
collection — all three are sources of non-determinism that hide until a
seemingly unrelated change reorders something upstream.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from app.engine.types import (
    Candidate,
    ObservationInput,
    authority_rank,
)
from app.ingestion.fingerprint import canonical_json

#: Quantisation for all engine arithmetic. Decimal throughout — float would
#: make the same calculation differ in the last digit across platforms, and
#: "reproducible" would become "reproducible on this machine".
WEIGHT_PRECISION = Decimal("0.000001")
SHARE_PRECISION = Decimal("0.0001")


def value_key(value: Any) -> str:
    """Canonical, sortable rendering of a value.

    The grouping key. Uses the same canonical JSON as observation
    fingerprints, so "the same value" means the same thing in the engine as it
    does in ingestion — two definitions would eventually disagree.
    """
    return canonical_json(value)


def latest_per_source(
    observations: tuple[ObservationInput, ...],
) -> tuple[tuple[ObservationInput, ...], tuple[tuple[ObservationInput, str], ...]]:
    """Keep each source's most recent statement; return the rest as superseded.

    "Most recent" is by **event time**, then ingestion time, then observation
    id. Ordering by ingestion alone would let a backfill overwrite current
    truth; ordering by event time alone would be ambiguous when a source
    restates the same instant, so ingestion time breaks that tie — a later
    correction of the same moment is the newer belief.

    Superseded observations are returned rather than discarded: they are still
    evidence of what was looked at.
    """
    by_source: dict[Any, list[ObservationInput]] = defaultdict(list)
    for observation in observations:
        by_source[observation.source_id].append(observation)

    kept: list[ObservationInput] = []
    superseded: list[tuple[ObservationInput, str]] = []

    # Sources iterated in a stable order so the output sequence is fixed.
    for source_id in sorted(by_source, key=str):
        ranked = sorted(
            by_source[source_id],
            key=lambda o: (o.event_time, o.ingested_at, str(o.observation_id)),
            reverse=True,
        )
        kept.append(ranked[0])
        superseded.extend(
            (o, "superseded_by_newer_observation_from_same_source") for o in ranked[1:]
        )

    return tuple(kept), tuple(superseded)


def build_candidates(
    weighted: tuple[tuple[ObservationInput, Decimal], ...],
) -> tuple[Candidate, ...]:
    """Group weighted observations into candidate values, ordered by strength.

    The returned order *is* the ranking: index 0 is the winner. Ordering is
    total, so there is never an implementation-defined choice between two
    candidates.
    """
    grouped: dict[str, list[tuple[ObservationInput, Decimal]]] = defaultdict(list)
    for observation, weight in weighted:
        grouped[value_key(observation.value)].append((observation, weight))

    total_weight = sum((w for _, w in weighted), start=Decimal(0))

    candidates: list[Candidate] = []
    for key in sorted(grouped):
        entries = grouped[key]
        weight = sum((w for _, w in entries), start=Decimal(0)).quantize(WEIGHT_PRECISION)
        share = (
            (weight / total_weight).quantize(SHARE_PRECISION) if total_weight > 0 else Decimal(0)
        )
        candidates.append(
            Candidate(
                value=entries[0][0].value,
                value_key=key,
                weight=weight,
                share=share,
                # Sorted so a candidate's observation list is itself stable.
                observations=tuple(
                    o for o, _ in sorted(entries, key=lambda e: str(e[0].observation_id))
                ),
            )
        )

    return tuple(sorted(candidates, key=_candidate_sort_key))


def _candidate_sort_key(candidate: Candidate) -> tuple[Any, ...]:
    """Total ordering over candidates. Strongest first.

    The chain, in order, and why each step is where it is:

    1. **Weight**, descending — the substantive rule; everything else is a
       tie-break.
    2. **Authority**, most authoritative first — a system of record outranks a
       downstream copy even at equal weight.
    3. **Event time**, newest first — at equal weight and authority, the more
       recent statement about the world wins.
    4. **Source count**, descending — more independent sources beats fewer.
    5. **Value key**, ascending — the final, arbitrary-but-total step. It has
       no semantic meaning and is not supposed to: its only job is to
       guarantee the ordering can never tie, so the result cannot depend on
       dict or set iteration order.
    """
    return (
        -candidate.weight,
        authority_rank(candidate.best_authority),
        -candidate.latest_event_time.timestamp(),
        -len(candidate.source_ids),
        candidate.value_key,
    )


def selection_margin(candidates: tuple[Candidate, ...]) -> Decimal:
    """Share gap between the winner and the runner-up, as a percentage.

    A single candidate means no contest: the margin is the full 100%. A thin
    margin is what makes a state *contested* — the winner is still selected,
    because refusing to answer is less useful than answering with the
    disagreement stated, but it must not be presented as settled.
    """
    if len(candidates) < 2:
        return Decimal("100.00")
    gap = (candidates[0].share - candidates[1].share) * 100
    return gap.quantize(Decimal("0.01"))


def numeric_divergence(candidates: tuple[Candidate, ...]) -> Decimal | None:
    """Absolute difference between the top two values, when both are numeric.

    Returns None for non-numeric values, where "how far apart" has no meaning.
    Reported in the units of the attribute itself, because "15 units apart" is
    actionable in a way that "the sources disagree" is not.
    """
    if len(candidates) < 2:
        return None
    first = _as_decimal(candidates[0].value)
    second = _as_decimal(candidates[1].value)
    if first is None or second is None:
        return None
    return abs(first - second)


def _as_decimal(value: Any) -> Decimal | None:
    """Interpret a canonical value as a number, or None.

    Booleans are rejected explicitly: ``bool`` is an ``int`` subclass in
    Python, and treating True as 1 would compute a meaningless "divergence"
    between two flags.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return None
    return None
