# Phase 12 — Performance and scale

Two N+1 queries found by measurement, both fixed. No new indexes, because the
access patterns were already covered and adding one on a hunch is how a schema
accumulates indexes nobody can justify removing.

## Method

Query counts were measured per endpoint at three data volumes against the real
database, not inferred from reading code. An N+1 is invisible in a code review
and invisible in a passing test suite: the response is correct, the tests are
green, and the only symptom is a page that was fine with five rows taking
seconds with five hundred.

The signal is whether the count *grows with the row count*:

| Endpoint | N=4 | N=12 | N=24 | Verdict |
| --- | --- | --- | --- | --- |
| `GET /api/data-sources` | 12 | 28 | 52 | **N+1** — 2N+4 |
| `GET /api/entities` | 8 | 16 | 28 | **N+1** — N+4 |
| `GET /api/dashboard` | 23 | 23 | 23 | flat |
| `GET /api/activity` | 6 | 6 | 6 | flat |
| `GET /api/conflicts` | 4 | 4 | 4 | flat |

After the fix:

| Endpoint | N=4 | N=24 |
| --- | --- | --- |
| `GET /api/data-sources` | **6** | **6** |
| `GET /api/entities` | **5** | **5** |

At 24 sources the list endpoint went from 52 queries to 6.

---

## The two N+1s

### `GET /api/data-sources` — 2N+4

`_source_response` ran two counts per source, one for streams and one for
observations. A workspace with fifty sources spent a hundred round trips
producing two integers each.

Now two grouped aggregates for the whole page. Deliberately **two queries, not
one join**: counting streams and observations together would multiply the two
row sets against each other and inflate both — the classic wrong answer that
still looks plausible.

A source with no streams or no observations is absent from a grouped result, so
the counts are filled in as zero rather than left missing. Without that the list
endpoint would silently omit newly-created sources, which is a correctness bug
wearing a performance fix's clothes.

### `GET /api/entities` — N+4

`list_entities` called `count_observations` once per entity. The mapping count
was already a correlated subquery; the observation count was not.

Now one grouped query joining mappings to observations. Two details matter:

`count(DISTINCT observations.id)` rather than `count(*)` — a mapping is unique
per (stream, external_id), so today the two agree, but a plain count would
silently double if that ever stopped being true.

Both organization filters are in `WHERE`, not the join's `ON` clause. The
tenancy guard cannot inspect a join condition, so a tenant filter placed there
is invisible to it — the lesson Phase 5 learned twice and Phase 11 hardened
against.

---

## Indexes — none added

The four new access patterns were checked against the existing indexes:

| Query | Index | Covered |
| --- | --- | --- |
| observations grouped by `source_id` | `ix_observations_source_id_event_time` | leading column |
| streams grouped by `data_source_id` | `ix_source_streams_data_source_id` | leading column |
| mappings filtered by `entity_id` | `ix_entity_mappings_entity_id` | leading column |
| observations joined on `(stream_id, external_id)` | `ix_observations_stream_external_event_time` | leading pair |

Verified with `EXPLAIN ANALYZE` at realistic volume — 200 sources, 200 streams,
12,000 observations, seeded inside a transaction and rolled back — rather than
against the near-empty development database, where PostgreSQL correctly
sequential-scans everything and the plan proves nothing:

```
->  Bitmap Heap Scan on observations  (actual rows=60 loops=50)
      ->  Bitmap Index Scan on ix_observations_source_id_event_time
            Index Cond: (source_id = data_sources.id)
```

Index-backed, no sequential scan. **No migration in this phase.**

---

## Bounded responses

Four list endpoints returned every row a tenant owned:
`GET /api/data-sources`, entity mappings, reality states, and evidence. Evidence
is the one that genuinely grows with observation volume — one row per
observation considered for one attribute.

Each now takes a `limit` (default 500, maximum 1000), matching the idiom
`GET /api/entities` already used. The defaults are high enough that no current
client changes behaviour.

These are **caps, not cursors.** A ceiling stops one request returning an entire
table; it does not let a client page through one. Real pagination belongs with
the phase that needs it, and inventing a cursor format now would be guessing at
requirements — the same position Phase 6 took on the activity feed.

`ORDER BY` was made total on the two list queries that lacked a tiebreaker, so
a capped page is deterministic rather than dependent on the planner.

---

## Regression tests

11 tests in `tests/test_performance.py`. Each runs an endpoint at two data
volumes and asserts the query count did not grow.

They assert `large <= small`, not equality. Growth is the defect; a *smaller*
second count is benign and happens for a real reason — the ORM identity map
serves the session and user lookups from memory on a repeat call. Requiring
exact equality would fail on that, and a flaky performance test gets deleted
rather than investigated.

Transaction bookkeeping (`SAVEPOINT`, `RELEASE`, `BEGIN`, `COMMIT`) is filtered
out. The test harness emits savepoints unevenly between runs, and counting them
adds noise in exactly the direction that hides a real N+1.

Correctness is tested separately, because fewer queries is worthless if the
numbers changed: the batched counts are compared against the per-row functions
they replaced, across sources with deliberately different row counts so a
batching bug that returned one source's count for another shows up as a
mismatch rather than passing by coincidence. One test confirms a grouped
aggregate cannot cross a tenant — a `GROUP BY` is exactly where a tenant filter
gets forgotten.

---

## Known limitations

**The dashboard costs 23 queries.** Flat, so it is not a scale bug, but it is
the first page every user loads and it could be fewer. Consolidating them means
either a large union query or caching, and neither is worth doing without a
measured latency problem to point at.

**No cursor pagination.** Caps only, as above.

**No load testing.** Query counts and plans were measured; concurrent throughput,
connection-pool saturation and lock contention under load were not.

**Recalculation is unmeasured at scale.** It runs one engine pass per attribute
over all of an entity's observations. That is bounded by a single entity's data,
not by tenant size, but no measurement was taken with a large observation
history.

**The connector fetch path was not re-measured.** It streams with a server-side
cursor and is bounded by `CONNECTOR_MAX_ROWS_PER_SYNC`; Phase 3 covered it and
nothing in this phase changed it.
