# Phase 5 — Conflict engine and Timeline

Scope taken from the repository's own declarations: `nav.ts` marks `/conflicts`
and `/timeline` as Phase 5, `architecture.md` lists "Conflict engine — Phase 5"
and "Time model — Status: Phase 5".

---

## The dependency that had to be isolated

Phase 4 ended blocked: the Phase 0 confidence specification is unrecoverable,
so the engine cannot produce a confidence score. Phase 5 would have inherited
that block entirely — except that two questions which look alike are not:

| Question | Needs the specification? |
| --- | --- |
| **Do these sources disagree?** | **No.** Group observations by canonical value; more than one group *is* a disagreement. |
| **Which is right, and how sure are we?** | **Yes.** Requires the weighting formula. |
| **How bad is this disagreement?** | **Yes.** Requires the conflict score and severity thresholds. |
| **What was true at T / what did we know at T?** | **No.** Reads `event_time` and `ingested_at` directly. |

So Phase 5 ships the first and fourth, and refuses the second and third.

`app/engine/detection.py` is where that separation lives. When weighting fails,
the engine returns `CalculationBlocked` **carrying a `DetectionResult`**:
distinct values, which sources assert each, and the numeric divergence. All
facts, none of them requiring a formula.

`DetectionResult` has no `value`, no `confidence` and no `status` field — not
as an oversight but so nothing downstream can mistake an alphabetically-first
candidate for a winner. Its candidates carry weight `0` and share `0`, so code
that wrongly treats them as ranked produces an obviously degenerate result
rather than a plausible one.

---

## What gets written, and what does not

| Outcome | `reality_states` | `conflicts` |
| --- | --- | --- |
| Scored | Written, with full breakdown and evidence | Written, graded |
| **Blocked** | **Nothing written** | **Written, ungraded** |

A `reality_states` row is a claim about the world with a confidence attached.
Writing one without a score would put an unfalsifiable assertion into the table
every later phase reads. Conflicts are different: "these sources disagree" is
true regardless, and withholding it would hide a real problem behind a missing
constant.

An ungraded conflict stores `score = NULL` and `severity = 'unspecified'` —
deliberately not `low`, which would read as "harmless" when nothing has
assessed it. The interface renders "Not graded" and explains why.

---

## Bitemporal timeline

Two axes, two questions, two different answers:

```
axis=event      WHERE event_time  <= T     "what was true at T"
axis=knowledge  WHERE ingested_at <= T     "what did we know at T"
```

They diverge exactly where an observation arrived late. A reading taken Monday
and delivered Friday is *old news we just heard*; a single-axis system cannot
tell that from *a new reading taken Friday*.

Combining both — `event_time <= E AND ingested_at <= K` — reconstructs what we
believed at knowledge-time K about the world at event-time E. That is the
question an audit asks after a decision turns out wrong, and it is why both
columns exist.

Late arrivals are **flagged** (`arrived_late`, `lag_seconds`) rather than left
to be inferred, so a reader can see why the two views differ.

Verified live: an observation with `event_time` 2026-08-01 and `ingested_at`
2026-08-10 appears on the event axis at a knowledge cutoff after ingestion, and
vanishes at a cutoff before it.

---

## Entity resolution

Reality state is about *things*; observations know about *rows*. `entities` and
`entity_mappings` bridge the two.

Mappings are **declared, never inferred** — the Phase 0 rule. An inferred
identity that is wrong merges two real-world things irreversibly: every state,
conflict and explanation downstream would be about a chimera, and no later
correction could untangle which observation belonged to which.

Mapping is **retroactive**: the join is on `external_id`, so observations
already ingested resolve immediately. No re-sync, and no observation is
rewritten — observations are immutable, and a mapping is a statement *about*
them, not a change *to* them.

---

## Conflict lifecycle

`open → acknowledged → resolved | dismissed`, recorded with who and when.

Two rules worth stating:

**Resolving never alters reality state.** The dependency runs one way. If
resolution could change a value, the state would depend on the order conflicts
were processed and would stop being reproducible from observations alone.
There is a test asserting the reality payload is byte-identical before and
after a resolve.

**A resolved conflict is not silently reopened** by a later recalculation.
Whether a disagreement is genuinely back is a human judgement; auto-reopening
would make the original resolution meaningless. Recalculation updates
`last_seen_at` and leaves the status alone.

Conflicts are matched on a **fingerprint** over the competing values and the
sources asserting them, so re-running the engine updates in place rather than
accumulating a duplicate row per calculation. Verified: three consecutive
recalculations leave the conflict count unchanged.

---

## Database

**No migration was required.** `alembic check` reports "No new upgrade
operations detected" — `entities`, `entity_mappings` and `conflicts` all landed
in `0004_reality`, and the timeline is a query over existing observation
columns. Head remains `0004_reality`.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/entities` | Create an entity |
| GET | `/api/entities` | List, with mapping and observation counts |
| GET | `/api/entities/{id}` | One entity |
| DELETE | `/api/entities/{id}` | Delete (observations untouched) |
| POST | `/api/entities/{id}/mappings` | Declare a source row describes it |
| GET | `/api/entities/{id}/mappings` | List mappings |
| POST | `/api/entities/{id}/recalculate` | Run the engine |
| GET | `/api/entities/{id}/reality` | Scored states |
| GET | `/api/entities/{id}/reality/{attr}/evidence` | Provenance trail |
| GET | `/api/entities/{id}/attributes/{attr}/unscored` | Values, no verdict |
| GET | `/api/entities/{id}/timeline` | Bitemporal reconstruction |
| GET | `/api/conflicts` | List, filterable by status |
| GET | `/api/conflicts/{id}` | One conflict |
| PATCH | `/api/conflicts/{id}` | Acknowledge / resolve / dismiss |

`recalculate` returns **200 with `blocked: true`** rather than an error when
scoring is unavailable. The request succeeded and reported what happened; a 5xx
would conflate "RealitySync is broken" with "a constant is missing".

---

## Frontend

**Conflicts** — real detected disagreements with their competing values,
sources and divergence. Ungraded conflicts show "Not graded" and a short
explanation, never a severity nobody assigned. Filter by status; acknowledge,
resolve or dismiss inline.

**Timeline** — entity selector plus a two-way axis toggle labelled by the
question each answers: *What was true* / *What we knew*. Every observation
shows both timestamps side by side, with late arrivals badged (`arrived 4d
late`). Truncation is reported rather than implied.

---

## Verified against real PostgreSQL

Two real sources over TLS reading two real tables that genuinely disagree:

```
warehouse: status=in_transit  weight_kg=12.500
erp:       status=delivered   weight_kg=27.500

recalculate -> blocked=True, states=0, conflicts=6
  value_conflict      status     competing=[delivered, in_transit]
  value_conflict      weight_kg  competing=[12.500, 27.500]  divergence=15.000
  source_disagreement (one per attribute)

lifecycle    -> acknowledged -> resolved; open=5 resolved=1
re-run       -> conflicts still 6; resolved stayed resolved
timeline     -> 2 events, 2 flagged late; knowledge cutoff 2020 -> 0 events
unscored     -> disagreement=true, distinct=[delivered, in_transit], no verdict
isolation    -> every cross-tenant read/write 404; anonymous 401
```

> The `weight_kg` divergence of 15.000 is a coincidence of the test values
> chosen here (27.5 − 12.5). It is **not** the LAPTOP-001 golden scenario and
> verifies nothing about it. That test remains blocked.

---

## Known limitations

**Every payload key becomes an attribute.** The live run produced a conflict on
`updated_at` — the event-time column — because sources naturally hold different
timestamps for the same row. That is technically a real disagreement but it is
metadata about the row, not a belief about the thing.
`app/services/reality.py::_STRUCTURAL_KEYS` exists for this and is currently
empty; populating it needs a rule about which columns are structural, which
belongs with the attribute-configuration work rather than being guessed here.

**Reality state remains unscored.** Conflicts and timelines are fully
functional; `reality_states` stays empty until the confidence specification
arrives. `/api/entities/{id}/reality` correctly returns `[]`.

**No conflict grading.** Score and severity stay `NULL` / `unspecified`.

**Contested-state detection is unreachable.** It needs the contested-margin
threshold, which is unspecified. Detected as `value_conflict` instead.

---

## Still blocked on the Phase 0 specification

Unchanged from Phase 4, and enumerated in
`app/engine/spec.py::MISSING_SPECIFICATIONS`: the freshness curve, quality and
agreement derivations, the reliability table, the four penalty multipliers, the
conflict-score formula, the margin definition, the severity thresholds, and the
LAPTOP-001 scenario itself.
