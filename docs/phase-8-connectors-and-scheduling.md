# Phase 8 — Connector and ingestion expansion

Two gaps the repository had declared itself, plus a schema field that had been
promising something since Phase 3 and delivering nothing.

| Declared in | Said |
| --- | --- |
| `docs/phase-3-postgres-connector.md` | "Sync runs inline… Background scheduling belongs to a later phase" |
| `docs/phase-3-postgres-connector.md` | "One connector type. The interface is ready for more." |
| `source_streams.poll_interval_seconds` | minimum 30, default 300, **zero consumers** |

## A note on scope

`architecture.md` listed Phase 8 as "AI investigation". That entry was written
in Phase 1 and never revisited; it also sits awkwardly against the standing
rule that reality state is never computed by a model. The brief for this phase
specified connector and ingestion expansion, which is what was built. The stale
row has been corrected and AI investigation is now listed without a phase
number rather than attached to the wrong one.

---

## Scheduled background sync

`poll_interval_seconds` is now read. A field that promises polling and delivers
none is worse than no field: it tells an operator their source refreshes every
five minutes when in fact it refreshes only when someone presses a button.

Every API instance runs the loop. Two mechanisms keep that from becoming two
syncs, and they solve different problems:

**A PostgreSQL advisory lock** (from Phase 3) stops two attempts overlapping in
time. The second is told "already running" and records a `SKIPPED` run rather
than queueing behind the first.

**A windowed idempotency key** stops a second instance repeating work the first
has already finished. The key is `scheduled:{source_id}:{window}`, where the
window is the current time floored to the source's shortest poll interval — an
absolute value both processes compute from their own clocks without
coordinating.

Failure is per-source: one unreachable customer database must not stop every
other tenant's syncing, so each source is attempted independently and a failure
is logged and recorded rather than raised. The loop itself survives anything a
single pass throws, including a database outage, because a scheduler that dies
on the first bad tick silently stops refreshing every source in the deployment.

The scheduler is not authoritative. If it never runs, nothing is wrong — data
is staler than configured, manual sync still works, and no observation is lost.
That is why it degrades quietly and reports its state on
`/api/system/status` instead of failing the process.

### Two bugs found by running it, not by testing it

Both were found by watching a real source over several minutes, and both had
passing unit tests at the time.

**The cursor was written to a detached ORM instance.** The scheduler discovered
due work in one session and performed it in another. `DueSource` carried the
`SourceStream` object across that boundary, so `stream.last_synced_at = …`
landed on an instance belonging to a closed session and was silently dropped.
`DueSource` now carries identifiers only, and the working session loads its own
rows — scoped to the owning organization, which is a second benefit: the
cross-tenant query is confined to selecting ids.

The test that was supposed to catch this re-read the stream through the *same*
session and passed on an in-memory attribute that had never been written. It
now asserts through a column-only select, which reads the database rather than
the identity map.

**The idempotency key was derived from the cursor.** This was the worse of the
two. An attempt that does not advance `last_synced_at` — a failure, a crash, an
unreachable source — leaves the next attempt computing an identical key.
`run_sync` correctly treats that as a retry of a request it has already
answered, returns the old run, and does no work.

The result was a scheduler that ticked forever, logged `scheduler.synced
status=completed` every 30 seconds, and never synced anything again. The
"completed" being reported was the original run. **A source that failed once
would never be retried.**

Keying on a floored window fixes it without giving up the property the key
exists for: two instances ticking seconds apart still land in the same window
and produce one run, while the window always advances, so a failure is retried
on the next one.

Both now have regression tests, and the cursor test was verified to fail when
the fix is reverted.

---

## MySQL connector

The second source type, and the point at which "adding a connector requires no
downstream changes" stops being an assertion.

Nothing in `app/ingestion`, `app/engine` or the API routes changed to
accommodate it. The full checklist turned out to be four items, not three:

1. A `DataConnector` implementation
2. A `SourceKind` value
3. A registry entry
4. **A migration** — `kind` is constrained by a CHECK, so adding a type widens it

That fourth item is a real cost and a deliberate one: the constraint means a
typo cannot create a source no connector can build, which would otherwise fail
at sync time, in production, long after the mistake was made. It is recorded in
`0005_mysql_connector.py` so the next connector's author is not surprised.

The mechanisms differ from PostgreSQL because MySQL differs:

| Concern | PostgreSQL | MySQL |
| --- | --- | --- |
| Read-only | connection option | `SET SESSION TRANSACTION READ ONLY` |
| Statement timeout | connection option | `SET SESSION max_execution_time` |
| Identifier quoting | `psycopg.sql.Identifier` | backticks, backtick in input **refused** |
| Streaming | server-side cursor | unbuffered `SSDictCursor` |
| Schemas | schemas within a database | databases (no schema layer) |
| Row estimate | `pg_class.reltuples` | `information_schema.tables.table_rows` |

TLS policy is identical in effect and different in expression. MySQL has no
`sslmode=prefer` to reject, so the three modes are RealitySync's own vocabulary
meaning the same things: `require` (encrypted, certificate unverified),
`verify-ca`, `verify-full`. The driver is handed an explicit `SSLContext` —
aiomysql connects in plaintext when given none, so passing one is what makes
the requirement real on the client side. The server is the authority on whether
the session was actually encrypted, and `test_connection` reads
`Ssl_version`/`Ssl_cipher` from the session rather than trusting the requested
mode.

### What the two source types prove

`test_two_source_types_produce_equivalent_observations` writes identical values
into a real PostgreSQL table and a real MySQL table, syncs both, and asserts the
normalised payloads match field for field — `12.500` stays `12.500`, not the
float `12.5`, on both paths.

The fingerprints deliberately **differ**, and that is correct rather than a
wart. The fingerprint includes `source_id` and `stream_id`: two systems
asserting the same thing are two separate observations, and collapsing them
would destroy exactly the corroboration — or disagreement — the Reality Engine
exists to weigh.

---

## Verification

Backend 447 passed / 1 skipped. Frontend 91 passed. `ruff`, `mypy --strict` and
`tsc` clean. Migration `0005_mysql` applied; `alembic check` reports no new
operations.

Live, against the running deployment and a real MySQL 8.4 server:

- Created a MySQL source through the real API. `test-connection` reported
  TLSv1.3, `MySQL 8.4.11`, `realitysync_reader@%`.
- Discovery returned the real columns with real types (`decimal(12,3)`).
- Configured a stream at a 30-second interval and **pressed nothing**. The
  scheduler synced it, and two real rows became two real observations.
- Inserted a third row into MySQL. It appeared without intervention. The run
  history shows `seen 3, created 1, skipped 2` — the two known rows deduplicated
  by fingerprint, the new one ingested.
- Subsequent windows show `seen 1, created 0, skipped 1`: the incremental read
  is following `last_event_time`.
- `triggered_by_user_id` is NULL on every scheduled run. Nobody triggered it,
  so the audit trail names nobody.

The reader account is created with `REQUIRE SSL`, and a plaintext connection
attempt was verified to be refused by the server.

---

## Deliberately not done

**No binlog change feed.** MySQL replication would give true deletes, but
consuming it needs `REPLICATION SLAVE` privilege and a durable position —
neither belongs in a least-privilege read-only account. `fetch_changes` is a
filtered read on both connectors, and says so.

**No per-source scheduler concurrency.** Sources are attempted one at a time
within a tick, bounded by `SYNC_SCHEDULER_MAX_SOURCES_PER_TICK`. Parallelism
here needs a decision about how many simultaneous connections a customer's
database should tolerate, which is not ours to guess.

**No editable poll interval in the UI.** The value is set when a stream is
configured and is now displayed on the source page, so it is visible rather
than silently ignored. An editor is a small form nobody has asked for yet.

**No jitter.** Every instance ticks on its own timer, so they naturally spread.
Adding jitter without evidence of a thundering herd would be tuning against an
imagined problem.

---

## Still blocked on the Phase 0 specification

Unchanged, and enumerated in `app/engine/spec.py::MISSING_SPECIFICATIONS`.
Phase 8 adds no confidence behaviour and removes none. It does make the blocked
part more visible: with two source types now able to observe the same entity,
the conflicts the engine detects are real cross-system disagreements — and they
still cannot be graded.
