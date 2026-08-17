# Release review — Phase 13

Final end-to-end verification of RealitySync at commit `a653ecc`.

**Verdict: READY WITH KNOWN LIMITATIONS.** One capability — confidence scoring —
is unavailable and reports itself as unavailable rather than guessing. Two
non-blocking defects are recorded below. Everything else was verified working
against real databases.

---

## What was verified, and how

Nothing here is claimed from reading code. Each line was executed against the
running stack, or is a test that runs in CI.

### End-to-end, with real data

One complete flow, no mocks, two different database engines:

| Step | Result |
| --- | --- |
| Register two tenants | 201 |
| Create PostgreSQL source | connected, **PostgreSQL 16.14**, **TLS 1.3**, as `realitysync_reader` |
| Create MySQL source | connected, **MySQL 8.4.11**, **TLS 1.3**, as `realitysync_reader@%` |
| Discovery on both | `e2e_stock` found, 4 columns each, metadata only |
| Configure streams, 30s interval | 201 |
| **Wait — press nothing** | scheduler ingested both within 20s |
| Observations | `{quantity: 100, location: DOCK-A}` from PostgreSQL, `{quantity: 175, location: DOCK-A}` from MySQL |
| Entity + mappings | 201, both streams mapped to one entity |
| Recalculation | 4 states written, 4 unscored, 4 conflicts |
| Reality state | `location` = `"DOCK-A"` **confirmed** (both agree); `quantity` **contested, no value selected** (100 vs 175) |
| Confidence | `null` on every state |
| Conflicts | `score = null`, `severity = unspecified` |
| Provenance | both sources, both values, event **and** ingestion time on each |
| Timeline | 2 entries on the event axis |
| Dashboard | 2 sources, `confidence.available = false`, average `null` |
| Fingerprint dedup | full re-sync of an unchanged row produced **no** duplicate |
| Manual vs scheduled runs | manual 1/1 carries a user; scheduled 6/6 carry **none** |

The `quantity` result is the product working as designed under a missing
specification: two systems disagree, the disagreement is recorded with full
evidence, and no winner is invented because ranking them *is* the missing
formula.

### Tenant isolation — live

Tenant B, authenticated, against every one of Tenant A's real ids:

| Attack | Result |
| --- | --- |
| `GET` entity, reality, evidence, source, observations | **404** on all five |
| `DELETE` entity | **404** |
| B's own conflict list | empty |
| Unauthenticated `GET /api/dashboard` | **401** |

### Phase 11 security regressions

| Control | Result |
| --- | --- |
| SSRF — `127.0.0.1`, `169.254.169.254`, `10.0.0.1` | all refused, logged |
| Hostile identifier (`` x`; DROP TABLE observations; -- ``) | **422** at the API boundary |
| Scheduled runs claiming a human actor | none (0 of 6) |

### Phase 12 performance regressions

Query counts measured at two data volumes against the real database:

| Endpoint | N=4 | N=20 |
| --- | --- | --- |
| `GET /api/data-sources` | 6 | **6** |
| `GET /api/entities` | 5 | **5** |
| `GET /api/dashboard` | 23 | **23** |

Flat. No N+1 has returned.

### Migrations

The full chain was verified on a **clean scratch database**, because the
development database contains data that the destructive-downgrade protection
correctly refuses to destroy:

```
base → head    6 steps
head → base    6 steps
base → head    6 steps
alembic check  no new upgrade operations
```

Both behaviours are correct and both were confirmed: the chain is reversible on
an empty database, and it **refuses** to run backwards over real data.

### Automated suite

| Gate | Result |
| --- | --- |
| `pytest` | **542 passed, 1 skipped** |
| `ruff check` | clean |
| `ruff format --check` | 127 files formatted |
| `mypy --strict` | clean, 92 source files |
| frontend tests | **101 passed** |
| `tsc --noEmit` | clean |
| `eslint` | clean |
| `next build` | compiled successfully |
| `alembic check` | clean |
| `docker compose config` | valid |

### Repository hygiene

13 commits, one per phase, working tree clean, no remote. 214 tracked files.
`.env` is not tracked. No key material, no debug statements, no temporary or
generated files in the tree. The one file matching a private-key pattern is
`tests/test_redaction.py`, which contains a deliberately truncated fake PEM to
assert that redaction works.

Migration head `0006_reality_production`, matching the migration chain.

---

## Findings

### BLOCKER

None.

### HIGH

None outstanding. The six vulnerabilities found in Phase 11 — SSRF through the
connector host, four ORM tenancy-guard bypasses, and identifier validation —
were all fixed and are covered by regression tests. See
[security.md](security.md).

### MEDIUM

**M1 — The scheduler can starve healthy sources behind persistently failing
ones.**

*What it is.* `find_due_sources` orders by `data_source_id`, a stable ordering,
and takes the first `SYNC_SCHEDULER_MAX_SOURCES_PER_TICK` (default 5). A sync
that fails never reaches the line that advances `last_synced_at`, so a broken
source stays due forever and is selected on every tick. With more failing
sources than the per-tick limit, healthy sources behind them in the ordering are
never reached.

*Evidence.* Observed during this review. The development database had
accumulated 94 due sources from earlier phase testing, most of them permanently
broken. The log showed `scheduler.deferred due=94 attempting=5` on every tick,
and two healthy, correctly-configured sources went **70 seconds without being
synced**. After removing the broken sources, the same two synced within
20 seconds. 92 of 94 enabled streams had `last_synced_at IS NULL`.

*Does it block release?* No. It needs more persistently-failing sources than the
per-tick limit, and it degrades freshness rather than corrupting anything — no
observation is lost or wrong, and manual sync is unaffected.

*Recommended action.* Order due work by last **attempt** rather than last
success. That requires recording attempt time separately from `last_synced_at`,
which is a schema and semantics change, not a one-line reorder — deliberately
not attempted during a verification phase.

### LOW

**L1 — The dashboard issues 23 queries per request.**

Flat with respect to data volume, so not a scale defect, but it is the first
page every user loads. Consolidating means a large union query or caching;
neither is worth doing without a measured latency problem. Measured at 58 ms
against the live stack.

**L2 — List endpoints are capped, not paginated.**

`limit` (default 500, max 1000) stops one request returning an entire table, but
a client cannot page past the cap. Real pagination belongs with the phase that
needs it.

### INFORMATIONAL

**I1 — Confidence scoring is unavailable.** The Phase 0 specification was not
recoverable; twelve inputs remain missing. See
[phase-0-recovery.md](phase-0-recovery.md) for the full search record. Every
confidence field is `null` with the reason attached, conflicts are ungraded, and
the LAPTOP-001 golden test is skipped. **Not verified, and not claimed to be.**

**I2 — DNS rebinding is not fully closed.** The connector host is validated at
configuration time and again at connect time, but the address behind a hostname
could change in between. Closing it needs the resolved address pinned and handed
to the driver, which neither driver exposes cleanly.

**I3 — No penetration test, no load test.** Query counts and plans were measured;
concurrent throughput, connection-pool saturation and lock contention were not.
The security work covers the attacks we thought of.

**I4 — `decided_at` and `superseded_at` are absent.** The recovered Phase 0
record names six bitemporal fields; the schema has four. Adding columns on the
strength of one line in a summary would be guessing at their meaning.

---

## Phase status

| Phase | Status | Verified by |
| --- | --- | --- |
| 1 Foundation | Complete | health/ready live, docker compose config, CI |
| 2 Auth + tenancy | Complete | live login, 401 on anonymous, isolation suite |
| 3 PostgreSQL connector | Complete | live TLS 1.3 connection, discovery, real observations |
| 4 Reality Engine core | **Partial** | deterministic selection verified; scoring blocked |
| 5 Conflicts + timeline | Complete | live conflict detection, timeline both axes |
| 6 Overview dashboard | Complete | live dashboard, confidence reported unavailable |
| 7 Hardening | Complete | rate limiter observed enforcing live (429 + headers) |
| 8 Connectors + scheduler | Complete | live MySQL 8.4 TLS 1.3, scheduled ingestion |
| 9 Reality productionization | Complete | live states with null confidence and full provenance |
| 10 Confidence specification | **BLOCKED** | exhaustive search, not recovered |
| 11 Security audit | Complete | 58 adversarial tests, live cross-tenant attacks |
| 12 Performance | Complete | query counts flat at N=4 and N=20 |
| 13 Release review | Complete | this document |

---

## What this release does not claim

It does not claim to be secure — it claims that a specific set of attacks was
attempted and failed. It does not claim confidence scoring works; that
capability is absent and says so. It does not claim to have been load tested.

The product's own thesis is that an unverified green light is worse than a
stated unknown. This document is written to that standard.
