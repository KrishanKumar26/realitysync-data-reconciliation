# Phase 9 — Reality Engine productionization (non-scoring)

Phase 4 proved the engine calculates. Phase 5 proved it detects disagreement.
Neither produced a reality state anyone could read, because none was ever
written.

## The problem

Phase 5 made a decision that was defensible and turned out to be too broad:

> | Outcome | `reality_states` | `conflicts` |
> | Scored | Written | Written, graded |
> | **Blocked** | **Nothing written** | **Written, ungraded** |

The reasoning was that a reality state is a claim with a confidence attached,
so writing one without a score would put an unfalsifiable assertion into the
table every later phase reads.

The cost was concrete. `reality_states` was **empty in every deployment**. The
selection, the evidence, the provenance and the bitemporal validity — none of
which need a formula — were unreachable. The Reality page was indistinguishable
from an empty workspace.

## What changed

Phase 9 narrows the withholding to the part that is actually missing.

| Question | Needs the specification? | Phase 9 |
| --- | --- | --- |
| Which distinct values were asserted? | No | Recorded |
| Which observations were superseded? | No | Recorded, with the reason |
| Which failed validation? | No | Recorded, with the reason |
| Do the sources disagree? | No | Recorded |
| What is the value, when they all agree? | No | **Selected** |
| What is the value, when they disagree? | **Yes** | **Not selected** |
| How confident are we? | **Yes** | **`null`, with the reason** |

Two things stay withheld, and they are withheld differently:

**Confidence is absent, never zero.** A zero is a score. Writing one asserts
what the missing formula would have produced, which nobody knows.

**No winner is chosen between competing values.** Ranking candidates *is* the
weighting formula. Returning the alphabetically-first would produce a state
indistinguishable from a real verdict — the exact failure this product exists
to prevent. The state is `contested`, the value is `null`, and every candidate
is kept as evidence.

### The engine has no "blocked" outcome any more

`calculate()` is now total: every input produces a `RealityCalculation`. Two of
its fields are independently optional, for different reasons, and conflating
them would be the easiest way to turn "we do not know" into a claim.

```
confidence   absent when the scoring specification is unavailable
value        absent when nothing could be selected, told apart by status:
             UNKNOWN   - no usable evidence
             CONTESTED - several values compete, ranking is unavailable
```

`value_selected` is stored rather than derived from `value IS NULL`, because a
source can legitimately assert JSON null. That is also why there is deliberately
**no** constraint tying the two: forbidding the legitimate case to guard
against the ambiguous one would trade a real capability for a redundant check.

### Invented constants removed

`ObservationInput` defaulted `reliability` to `0.5` ("the neutral midpoint") and
`quality` to `1.0`. Both were invented values sitting in production code. They
were harmless only because weighting raised before reaching them — the day the
specification arrives they would silently become load-bearing, and every
unconfigured source would score as though someone had assessed it.

Both are now `Decimal | None`, and the scoring path fails loudly on absence.
The type checker proved the point: making them optional produced four errors,
each at a place that had been quietly relying on a number nobody chose.

Relatedly, `_unknown()` wrote `confidence = 0.0`. That was the one place the
codebase converted an unavailable confidence into a number.

### A fourth evidence role

`considered` — eligible, with no selected value to support or dissent from.
"Supporting" and "dissenting" are defined *relative to a selection*; labelling a
contested attribute's observations either way would smuggle in a verdict the
engine has not reached.

---

## Determinism

Unchanged from Phase 4 and now covered end to end through the database:

- `as_of` is an argument, never a clock read. The same `as_of` a year later
  reproduces the same state; only the `calculated_at` stamp differs.
- Values are grouped by canonical JSON, so `12.500` and `12.5` stay distinct
  assertions about precision while `{"a":1,"b":2}` and `{"b":2,"a":1}` do not
  split.
- Every collection is iterated in an explicitly sorted order, and evidence is
  sorted before it is returned.
- All arithmetic is `Decimal`.

`test_ingestion_order_does_not_change_the_outcome` inserts the same three
observations in both orders and asserts an identical state. Any dependence on
insertion order — a stable sort relying on it, a `max()` over an unordered set —
shows up there and nowhere else.

## Bitemporal semantics

Event time decides what is true. Ingestion time breaks ties **only** between
statements about the same instant.

| Scenario | Outcome |
| --- | --- |
| Newer event, arrives later | Wins |
| **Older event, arrives later** | **Does not win** |
| Same event time, later ingestion | Later ingestion wins — a correction |
| Out-of-order arrival | Identical to in-order arrival |

The second row is the one worth testing. A backfill delivered today describing
last Monday must not displace Tuesday's reading; ordering by arrival makes every
late correction look like the newest truth, and it looks completely normal.

## Provenance

Every state is explainable from the API without a second lookup. The evidence
endpoint now carries the source, the stream, the external id, the observed
value, **both** timestamps, the role, and the exclusion reason where one
applies.

Both timestamps, always: the gap between them is exactly what a late arrival
looks like, and showing one would hide the thing worth seeing.

Superseded and invalid observations are returned rather than dropped. The trail
shows what was looked at, not only what counted.

The endpoint joins `observations`, and **both** organization filters live in
`WHERE` rather than in the join's `ON` clause — the ORM tenancy guard cannot
inspect a join condition, so a tenant filter placed there is invisible to it and
would silently stop being enforced. This was learned the hard way twice in
Phase 5.

## Recalculation

Idempotent, tenant-scoped, and safe to run repeatedly.

- States are **replaced wholesale**, not merged. Merging would leave evidence
  from two different runs side by side with no way to tell them apart.
- Evidence is replaced with its state, so it always corresponds to the
  observations actually used. No orphans.
- Conflicts are matched on their fingerprint and updated in place, so repeated
  detection does not accumulate duplicates.
- A conflict a person has acknowledged or resolved keeps that status. Rebuilding
  a derived snapshot must not discard human work.
- Conflict handling cannot influence the selected value. If it could, the state
  would depend on the order conflicts happened to be processed in, and two
  identical deployments could disagree.
- The state and its evidence commit together. Evidence without its state would
  be unreachable rows; a state without evidence would be an unexplainable claim,
  which is the one thing this table must never contain.

## Tenant isolation

Every derived artefact is scoped, not only the entity. Cross-tenant reads of
states, evidence and conflicts return **404** rather than 403 — as far as the
other tenant is concerned the resource does not exist, and 403 would confirm it
does.

## Observability

`reality.recalculation_started` and `reality.recalculation_completed` carry the
entity, organization, observation and attribute counts, states written, states
unscored, conflicts written and duration. Per-attribute detail is logged at
debug with the candidate count, status and evidence count.

**The selected value is deliberately absent from every log line.** An attribute
payload is customer data, and a log sink is the one place it has no reason to
be.

## Migration

`0006_reality_production`. Three changes, each removing a place where the schema
forced a claim nobody could justify:

- `confidence` becomes nullable — NOT NULL left only "write 0.0" or "write
  nothing", and both are wrong.
- `value` becomes nullable — an UNKNOWN or unselected state had nowhere to go.
- `value_selected` is added, plus a CHECK that an UNKNOWN state never claims a
  selection.

Also widens the evidence-role CHECK for `considered`.

Data-safe forwards: relaxing NOT NULL and widening a CHECK cannot fail on
existing rows, and the new column has a server default. The downgrade **refuses**
rather than destroys, matching 0005 — narrowing these columns with unscored
states present would otherwise discard every derived state in the deployment
along with its evidence.

Upgrade → downgrade → upgrade verified, and `alembic check` is clean.

---

## Verification

Backend **472 passed, 1 skipped**. Frontend **101 passed**. `ruff`,
`mypy --strict`, `tsc --noEmit`, `eslint` and `next build` all clean.

Tests changed rather than deleted, each with the reason recorded in the test
itself:

| Test | Was | Why it changed |
| --- | --- | --- |
| `test_no_observations_gives_an_honest_unknown` | asserted `confidence == 0.0` | 0.0 is a score; the absence is now an absence |
| `test_the_engine_refuses_to_guess...` | asserted a blocked *result* | the score is still withheld; the state is not |
| `test_recalculate_detects_conflicts_while_scoring_is_blocked` | asserted `states_written == 0` | states are written now, unscored |
| `overview.test.tsx` reality cases | asserted "No reality state could be produced" | that heading is now false |

---

## Deliberately not done

**No structural-key rule.** `_STRUCTURAL_KEYS` is still empty, so a join key
becomes an attribute. Phase 6 recorded this as "an open question rather than
something to guess", and it still is.

**No per-source reliability configuration.** The field exists and is `None`.
Making it configurable without the reliability table would let an operator set a
number that feeds a formula nobody has.

**No `STALE` or `PROVISIONAL` status.** Both need a threshold — how old is too
old, how thin is too thin — and those thresholds are unspecified. The engine
declines to reach those states rather than guessing where the boundary sits.

**No pagination on reality states.** One row per attribute of one entity is
bounded by the entity's own shape; a cursor belongs with the phase that needs
one.

---

## Still blocked on the Phase 0 specification

Unchanged, and enumerated in `app/engine/spec.py::MISSING_SPECIFICATIONS`: the
freshness curve, the quality and agreement derivations, the reliability table,
the four penalty multipliers, the conflict-score formula, the margin definition,
the severity thresholds, and the LAPTOP-001 scenario.

Phase 9 invents none of them. What it changes is how much is usable *without*
them — and it makes the blockage visible in the product rather than only in the
source: every unscored state carries the reason and the full outstanding list in
its `confidence_breakdown`.

**The LAPTOP-001 golden test remains skipped.** It cannot pass without the
original inputs, and tuning anything toward 71.0%, 0.594 or 0.78% would be
fabricating a specification rather than recovering one.
