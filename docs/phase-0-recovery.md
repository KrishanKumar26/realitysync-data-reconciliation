# Phase 0 recovery — search record and evidence

Phase 10 was an exhaustive attempt to recover the original Phase 0 confidence
specification and the LAPTOP-001 golden scenario.

**Outcome: partially recovered. The twelve missing specification inputs remain
missing.** One genuine Phase 0 decision record was found and is preserved here;
it confirms the shape of the formula and several product decisions, and it
contains none of the sub-formulas.

This document exists because the recovered record lived only in a conversation
transcript. Losing it a second time would be worse than never having found it.

---

## 1. Search summary

Every location searched, and what it returned.

### Git

| Location | Result |
| --- | --- |
| Branches (local and remote) | `main` only |
| Tags | none |
| Remotes | none configured |
| Stash | empty |
| Reflog (`--all`) | 9 entries, all the phase commits |
| Dangling / unreachable objects (`fsck`) | none |
| All 506 objects, grepped for the constants | only this project's own writing |
| Every historical version of `docs/architecture.md` | 9 versions; the Phase 1 version holds the formula block, no sub-formulas |
| Commit messages and diffs | no specification content |

### Repository filesystem

| Location | Result |
| --- | --- |
| `docs/`, `README.md` | reference Phase 0 but do not contain it |
| Source comments and docstrings | cite `Phase 0 §15, §20, §22, §24, §25, §38` |
| Test fixtures and test names | no LAPTOP-001 inputs |
| JSON / YAML / TOML / CSV | no specification content |
| Ignored files (`git status --ignored`) | `.env`, caches, build artefacts only |
| Editor / project configuration | none present |

The section citations are worth stating plainly: the code refers to a document
with **at least 38 sections**, and that document is not in the repository at
any commit.

### Filesystem beyond the repository

| Location | Result |
| --- | --- |
| `~/Desktop` (filename and content) | no match outside this project |
| `~/Documents`, `~/Downloads` | empty |
| `~/Desktop/Archive.zip`, `Archive copy.zip` (1.4 GB, byte-identical) | **CityPulse OS** — a different product |
| Spotlight index (`mdfind`) | every hit is inside this project |
| `~/.Trash` | empty |

The archives contain a `docs/PRD.md`, and it is **not** RealitySync's. CityPulse
OS is an urban-intelligence platform for Indian metros; its PRD, architecture
and ML documents contain zero occurrences of `RealitySync`, `LAPTOP-001`,
`0.594` or `reality state`. It was checked and set aside rather than mined,
because a PRD from another product would have supplied plausible numbers with
no authority behind them.

### Shell history and conversation artefacts

| Location | Result |
| --- | --- |
| `~/.zsh_history` (1,263 lines) | no match |
| All 13 Claude transcripts on the machine | only this project's transcript mentions RealitySync |
| This project's transcript — **user messages only** | **one genuine Phase 0 record found** |

Only user messages were treated as evidence. Assistant output is this project's
own reasoning and cannot confirm its own inputs.

There is no separate Phase 0 session on this machine. Phase 0 was reviewed
elsewhere, and what survives is the summary of its conclusions that was pasted
into this project.

---

## 2. Evidence recovered

From the transcript, the message beginning `Phase 0 review completed.` Quoted
only where it bears on the specification:

> **2. CONFIDENCE FORMULA**
> Keep the deterministic formula:
>
>     Confidence = Ceiling × Base × Penalties
>
> Weights:
> - Agreement = 0.40
> - Freshness = 0.30
> - Quality = 0.15
> - Validation = 0.15
>
> Maximum confidence = 99%.
>
> For the LAPTOP-001 example, accept the mathematically calculated 71.0%
> result. Do NOT force the illustrative PRD value of 78%. The formal
> deterministic formula is authoritative.

> **3. RECORDED EVENT TIME**
> Keep recorded event-time semantics without applying an automatic freshness
> discount in MVP.

> **6. CONFLICT RESOLUTION**
> Resolving a conflict must NEVER directly modify the Reality State.

And from the later Phase 4 directive, which explicitly supersedes the older
architecture formula:

> 3. Phase 4 Base formula:
>    `Base = 0.40·reliability + 0.30·freshness + 0.15·quality + 0.15·agreement`
>
> 7. Phase 4 brief overrides the old architecture formula.

---

## 3. Classification

### CONFIRMED

| Item | Source | Already implemented? |
| --- | --- | --- |
| `Confidence = Ceiling × Base × Penalties` | Phase 0 record | Yes |
| `Ceiling = 1 − Π(1 − R_source)`, capped | Phase 1 `architecture.md`, Phase 4 directive | Yes — `CEILING_CAP` |
| **Maximum confidence 99%** | Phase 0 record | Yes — `CEILING_CAP = 0.99` |
| `w_o = R_source × Freshness × Quality` | Phase 1 `architecture.md`, Phase 4 directive | Yes — `observation_weight` |
| Penalty names: coverage, staleness, impossible, late | Phase 1 `architecture.md`, Phase 4 directive | Yes — names only |
| `Score = 100 × Ceiling × Base × penalties`, bounded 0–99 | Phase 4 directive | Yes — structure |
| **Recorded event-time carries no automatic freshness discount in MVP** | Phase 0 record | Not applicable — there is no freshness curve to discount |
| Resolving a conflict never modifies Reality State | Phase 0 record | Yes — asserted by test |
| Entity resolution stays deterministic and manual | Phase 0 record | Yes |
| 71.0% is the *calculated* LAPTOP-001 result; 78% was illustrative and rejected | Phase 0 record | N/A — golden test skipped |

### CONFLICTING

**The Base formula's first and fourth terms.** Two authoritative statements
disagree:

| Source | Base |
| --- | --- |
| Phase 0 record and Phase 1 `architecture.md` | `0.40·agreement + 0.30·freshness + 0.15·quality + 0.15·validation` |
| Phase 4 directive | `0.40·reliability + 0.30·freshness + 0.15·quality + 0.15·agreement` |

The weights are identical; the *terms* are not. The Phase 4 directive resolves
it explicitly — "Phase 4 brief overrides the old architecture formula" — and the
implementation follows the Phase 4 version. Recorded here because the conflict
is real and a future reader finding the Phase 1 document alone would implement
something different.

Note the consequence: under the Phase 4 form, reliability appears in both the
Ceiling and the Base, which is a deliberate double weighting of source
authority. That was noted in `architecture.md` at the time and is confirmed
rather than assumed.

### DERIVED

Nothing. No value in the implementation was derived from the golden outputs, and
none has been now. Deriving inputs from `71.0`, `0.594` or `0.78%` would produce
a specification that reproduces the example and is otherwise unfounded — it
would look verified and be fabricated.

### NOT FOUND — all twelve remain missing

| # | Missing input | What is needed |
| --- | --- | --- |
| 1 | Freshness curve + constant | The decay function and its constant mapping age to 0–1 |
| 2 | Quality derivation | How the 0–1 factor is produced, or confirmation it is source-declared |
| 3 | Agreement derivation | How the 0–1 factor comes from competing candidate weights |
| 4 | Reliability table | `R_source` per authority level, or confirmation it is configured per source |
| 5 | Coverage penalty | Trigger condition and multiplier |
| 6 | Staleness penalty | Trigger condition and multiplier |
| 7 | Impossible penalty | Trigger condition and multiplier |
| 8 | Late penalty | Trigger condition and multiplier |
| 9 | Conflict-score formula | The function producing 0–1 (golden expects 0.594) |
| 10 | Margin definition | Confirmation it is the winner/runner-up weight-share gap |
| 11 | Severity thresholds | Score boundaries for low/medium/high/critical |
| 12 | LAPTOP-001 scenario | Per-observation source, authority, reliability, value, event_time, ingested_at, quality, validation |

Items 1–4 are the Base and weight inputs; 5–8 are the penalties; 9–11 grade
conflicts; 12 is the only thing that could verify any of it.

---

## 4. Consequence

No scoring code changed. The safe behaviour from Phases 4–9 stands:

- `confidence` is `NULL` with the reason recorded, never `0`
- competing values select no winner, because ranking is the missing formula
- conflicts carry `score = NULL` and `severity = 'unspecified'`
- the LAPTOP-001 golden test remains skipped

One unrelated observation, recorded rather than acted on: the Phase 0 record
names six bitemporal fields — `event_time`, `ingested_at`, `valid_from`,
`valid_to`, `decided_at`, `superseded_at`. The schema has the first four.
`decided_at` and `superseded_at` are absent. That is an architecture question,
not a scoring one, and adding columns on the strength of a single line in a
summary would be guessing at their meaning.

---

## 5. What would unblock this

Any one of:

1. The original Phase 0 specification document — the one with at least 38
   sections that the code cites.
2. The PRD it was reviewing.
3. The LAPTOP-001 worked example, showing the per-observation inputs and the
   arithmetic that produces 71.0%.

Item 3 alone would be enough to verify the engine end to end, and would
constrain most of items 1–11 by construction.

Until then, scoring stays unverified and is reported as unavailable rather than
approximated.
