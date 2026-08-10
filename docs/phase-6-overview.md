# Phase 6 — Overview dashboard

Scope taken from the repository's own declarations, in two independent places:

* `apps/web/src/components/shell/nav.ts` — `/` Overview, `phase: 6`, described
  as *"Reality confidence, source health and recent activity"*.
* The docstring in `apps/web/src/app/page.tsx`, written in Phase 1 — *"The
  dashboard described in the product specification arrives in Phase 6, once
  there are real observations behind it. Showing a confidence gauge now would
  mean inventing a number."*

So Phase 6 is the Overview, and its brief was written by the phase that
deferred it.

---

## The isolated dependency

The Overview names three things. Two are computable; one is not.

| Element | Blocked on the Phase 0 confidence specification? |
| --- | --- |
| **Source health** | No — the recorded outcome of real connection attempts |
| **Recent activity** | No — audit rows, sync runs and conflict detections |
| **Coverage counts** | No — entities, observations, streams, conflicts |
| **Reality confidence** | **Yes** |

The blocked element is not omitted and not faked. The dashboard reports
`available: false`, states the reason, and lists the twelve missing
specifications.

**Every numeric confidence field is `null`, never `0`.** That distinction is
the whole point of the panel. A gauge reading 0% is a claim about the *data* —
"we are certain of nothing". The truth is a claim about the *specification* —
nobody has told us how to measure. Rendering the first when the second is true
would be exactly the confident-looking fabrication this product exists to
prevent, on the most-viewed screen in the application.

There are three tests holding that line, one in the service, one in the API and
one in the interface.

---

## What the dashboard reports

### Source health

Per source: status, stream count, observation count, last connection, last
sync, and the last error verbatim. Fleet totals for connected / never-tested /
failing / disabled.

Nothing is probed on read. Dialling every customer database to render a
dashboard would be slow and rude, so these are the recorded outcomes of
attempts that actually happened.

**"Never tested" is counted separately from "failing."** A source with stored
credentials whose connection has never been proven is in a different situation
from one that tried and failed, and collapsing them would tell an operator
something is broken when nobody has checked.

### Ingestion

Observation count (all-time and in-window), entities with a mapped/unmapped
split, streams with an enabled count, syncs in the window and how many failed.

### Conflicts

Counts by status, and by severity for outstanding conflicts only — severity
describes work to do, not history.

**Ungraded conflicts are reported separately.** Every conflict is currently
ungraded, because grading needs the missing specification. Folding them into
the `low` bucket would present an absent judgement as a mild one.

### Recent activity

A merged feed from three real tables — the audit log, sync runs and conflict
detections — interleaved newest-first with a stable secondary sort so equal
timestamps do not reorder between calls.

The audit source reads an **allowlist** of actions. The audit log also carries
security events, and a dashboard is not the place to advertise failed logins;
those stay in the audit trail where they belong. There is a test asserting it.

---

## Empty versus quiet

`is_empty` distinguishes "nothing connected yet" from "connected but quiet".
The first gets an onboarding state with a link to connect a source; the second
gets real zeroes. Showing onboarding to a workspace that already has sources
would be wrong, and showing zeroes to a brand-new one would be unhelpful.

---

## Database

**No migration was required.** `alembic check` reports "No new upgrade
operations detected". The dashboard is entirely derived — it reads
`data_sources`, `source_streams`, `observations`, `sync_runs`, `entities`,
`entity_mappings`, `conflicts` and `reality_states`, and writes nothing. Head
remains `0004_reality`.

Verified through a full `upgrade → downgrade base → upgrade` cycle.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/dashboard` | Source health, ingestion, conflicts, confidence, activity |
| GET | `/api/activity` | The activity feed alone, for polling |

Both are read-only, authenticated, and organization-scoped through
`CurrentOrganization` — the tenant id comes from the session, so there is no
request parameter for a caller to tamper with.

`window_days` is bounded to 1–90 and `limit` to 1–100. An unbounded window
would scan every row a workspace has ever produced, which is a denial of
service triggered by opening a screen.

---

## Frontend

**Overview** (`/`) — five panels on real data: reality confidence (or its
absence), source health with per-source detail, ingestion counts, conflicts,
and the activity feed. Loading, empty, onboarding and error states are all
distinct. Failures surface as an error, never as an empty dashboard: rendering
"nothing connected" for a failed request would send someone to reconnect a
source that is already fine.

**Reality** (`/reality`) — completed here, a Phase 4 leftover. Its API landed
in Phase 5 but the page was still a placeholder, and the dashboard drills into
it. Shows scored states when they exist; when a recalculation comes back
blocked it explains what was read, how many conflicts were still detected, and
which specifications are missing.

---

## Verified against real PostgreSQL

```
new workspace     is_empty=True, confidence.available=False, average=None
                  activity=['Created the workspace']

two real sources synced over TLS + one deliberately untested
                  sources: total=3 connected=2 never_tested=1 errored=0
                  ingestion: observations=3 entities=1 mapped=1 syncs=2
                  conflicts: open=6 ungraded=6 by_severity={}
                  confidence: available=False average=None missing=12

confidence        average_confidence is None, not 0
window bounds     0 -> 422, 400 -> 422, 30 -> 200
tenant isolation  second workspace sees 0/0/0 and is_empty=True;
                  its activity feed contains none of our syncs
unauthenticated   /dashboard 401, /activity 401
```

The aggregate endpoints are where a missing tenant filter does the most damage:
it does not leak a visible row, it silently inflates a number, which is far
harder to notice. Hence the explicit cross-tenant count assertions above and in
`tests/test_dashboard.py`.

---

## Known limitations

**`unscored_attribute_count` counts distinct source records, not attributes.**
It answers "how much is waiting to be scored" at record granularity. A true
attribute count would need the structural-key rule noted in Phase 5, which is
still an open question rather than something to guess.

**Activity is not paginated.** Bounded to 100 items with a window filter, which
is sufficient at current scale; a cursor belongs with the phase that needs it.

**No trend or sparkline data.** Everything is a point-in-time count. Time
series would need a retention decision nobody has made.

---

## Still blocked on the Phase 0 specification

Unchanged, and enumerated in `app/engine/spec.py::MISSING_SPECIFICATIONS`: the
freshness curve, quality and agreement derivations, the reliability table, the
four penalty multipliers, the conflict-score formula, the margin definition,
the severity thresholds, and the LAPTOP-001 scenario.

Consequence for Phase 6: the confidence panel reports its absence rather than a
value, and the conflict severity breakdown stays empty with everything counted
as ungraded.
