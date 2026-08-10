# Phase 3 — PostgreSQL connector

The first real-data vertical slice:

```
REAL EXTERNAL POSTGRESQL → CONNECTION → SCHEMA DISCOVERY → STREAM
                                                             ↓
                       SYNC HISTORY ← OBSERVATIONS ← REAL SYNC
```

Nothing in this phase interprets what it reads. Observations are recorded
exactly as the source stated them; reality state, confidence and conflicts are
later phases reading this table.

---

## Connector architecture

```
        app/ingestion/            app/connectors/
        ┌──────────────┐          ┌────────────────────┐
        │ sync.py      │────────▶ │ base.DataConnector │  ← the interface
        │ normalization│          │ types.*            │
        │ fingerprint  │          │ registry           │
        │ locks        │          └─────────┬──────────┘
        └──────────────┘                    │ implements
                                  ┌─────────▼──────────┐
                                  │ postgres/          │
                                  │  connector.py      │
                                  │  config.py         │
                                  │  errors.py         │
                                  │  factory.py        │
                                  └────────────────────┘
```

The dependency arrow points one way and only one way. Ingestion imports
`DataConnector`; it never imports `postgres`. Adding Snowflake, a REST API or a
CSV drop means writing one class and one line in the registry — no change to
ingestion, and none to the Reality Engine when it arrives.

### The interface

| Method | Contract |
| --- | --- |
| `connect()` | Open the connection. Raises `ConnectorError`. |
| `test_connection()` | Structured result: reachability, TLS, auth, discovery permission. |
| `discover_schema()` | Catalog metadata only. Never reads table data. |
| `fetch_data(selector)` | Async iterator of `SourceRecord`. |
| `fetch_changes(selector)` | Rows at or after a high-water mark. |
| `get_health()` | State for display. Never credentials. |
| `disconnect()` | Idempotent, never raises. |

Connectors are **read-only by construction**. Nothing in the interface can
express a write, and PostgreSQL connections are opened with
`default_transaction_read_only=on`, so a bug cannot mutate a customer's
database.

---

## MVP network requirement

**RealitySync's backend must be able to reach your PostgreSQL endpoint over the
public internet.** The connector dials outbound over TCP; there is no agent
inside your network.

Supported:

- Managed databases with a public endpoint — Neon, Supabase, RDS with public
  access, Render, Aiven
- Self-hosted PostgreSQL with a public address and a firewall allowlist

**Not supported in the MVP:** private VPC-only addresses, SSH tunnels, bastion
hosts, Tailscale or WireGuard meshes, or networks where IP allowlisting is
impossible.

Private-network support is planned as a self-hosted **RealitySync Agent** that
runs the same connector inside your network and pushes observations outbound.
The architecture already accommodates it: ingestion consumes observations and
has no knowledge of how they were obtained.

---

## TLS

RealitySync **will not open an unencrypted connection.** Three libpq modes are
rejected outright:

| Mode | Why it is rejected |
| --- | --- |
| `disable` | Sends credentials and data in plaintext. |
| `allow` | Only uses TLS if the server refuses plaintext first. |
| `prefer` | **Silently falls back to plaintext** when TLS is unavailable. |

`prefer` is the dangerous one — it looks safe and downgrades without telling
anyone. Accepted modes:

| Mode | Guarantee |
| --- | --- |
| `require` | Encrypted; certificate not verified. Minimum. Use for self-signed certs. |
| `verify-ca` | Encrypted; certificate chain verified. |
| `verify-full` | Chain verified **and** hostname checked. **Recommended for production** — the only mode that defends against an active machine-in-the-middle. |

The connection test reads `pg_stat_ssl` and reports the negotiated TLS version,
so the interface shows what actually happened rather than what was requested.
If a session somehow arrives unencrypted, the test fails rather than passing.

---

## Required database permissions

Create a dedicated read-only role. RealitySync needs nothing more, and the
connector is developed and tested against exactly these grants.

```sql
CREATE ROLE realitysync_reader WITH LOGIN PASSWORD 'a-strong-password';

GRANT CONNECT ON DATABASE your_database TO realitysync_reader;
GRANT USAGE   ON SCHEMA public          TO realitysync_reader;
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO realitysync_reader;

-- So a table added later needs no further grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO realitysync_reader;
```

Repeat `GRANT USAGE` and `GRANT SELECT` for every schema you want to sync.

Discovery lists only relations the role can actually `SELECT` from, and reports
schemas it cannot read under "not readable by this role" rather than hiding
them — "you cannot see this" is actionable; a silent omission looks identical
to the schema not existing.

---

## Schema discovery

Reads `pg_class`, `pg_attribute`, `pg_index` and `pg_namespace`. It **never
reads table data**: discovery runs against a production database at
configuration time, and scanning every table to describe it would be an outage
caused by a settings screen.

Discovered per table: columns, data types, nullability, primary key columns and
their order, temporal columns, relation kind, and an **approximate** row count
from `pg_class.reltuples` — the planner's estimate, labelled as approximate
everywhere it appears. A table never analysed reports 0 or unknown.

System schemas (`pg_catalog`, `information_schema`, `pg_toast`) are excluded
unless explicitly requested.

All catalog queries bind schema names as parameters; no SQL is built by string
formatting anywhere in the connector, and every identifier from configuration
goes through `psycopg.sql.Identifier`.

---

## Stream configuration

A stream is one table selected for ingestion:

| Field | Meaning |
| --- | --- |
| `schema_name`, `table_name` | What to read |
| `primary_key_columns` | Row identity → the observation's `external_id` |
| `event_time_column` | Which column carries the event time |
| `event_time_semantics` | **What that time means** |
| `selected_columns` | Empty means all |
| `enabled`, `poll_interval_seconds` | Scheduling, used from the phase that owns it |

### Event-time semantics

The question that matters most, so the interface asks it plainly:

| Value | Meaning |
| --- | --- |
| `observed` | The column records when the fact was true in the world. |
| `recorded` | The column records when the source system wrote the row. |
| `ingest_fallback` | No usable time column; ingestion time stands in. |

`recorded` carries **no confidence penalty** — that was settled in Phase 0. The
distinction is preserved because it is what makes a root-cause explanation
possible later. Penalising it would mean inventing a number.

A database CHECK ties the two fields together: `ingest_fallback` is exactly the
case with no column, and every other value requires one.

---

## Sync behaviour

1. Take a PostgreSQL **advisory lock** on the source — one sync at a time.
2. Open a sync run (`pending` → `running`).
3. For each enabled stream: read rows, normalise values, compute fingerprints,
   insert with `ON CONFLICT DO NOTHING`.
4. Advance the stream's high-water mark to the **maximum** event time seen.
5. Close the run (`completed` / `failed` / `skipped`) with row counts.

Incremental reads filter `event_time >= last_event_time`. The comparison is
`>=`, not `>`: a source with second-granularity timestamps can write several
rows at the same instant, and `>` would skip the ones that arrived after the
cursor was taken. Re-reading is free because fingerprints make it idempotent;
skipping is not.

### Idempotency

`observations` is UNIQUE on `(stream_id, fingerprint)`, and the fingerprint is
a SHA-256 over source id, stream id, external id, event time, event-time
semantics and the normalised payload.

**Ingestion time is deliberately not in the fingerprint.** If it were, every
sync would duplicate every row. Nothing about *when or how we looked* may
affect the identity of *what we saw* — all of it goes into provenance instead,
where it is visible without being load-bearing.

The constraint is the mechanism, not a check in application code: two
concurrent syncs cannot both pass a read-then-write check, but they can both
hit the same unique index and exactly one wins.

### Locking uses PostgreSQL, not Redis

The lock must be consistent with the data it protects. An advisory lock lives
in the same server as `observations`, so there is no window where the lock says
one thing and the database another. A Redis lock could be lost to eviction or
failover while a sync was still running, and Redis holds nothing authoritative
in this architecture by design.

### Value normalisation

| PostgreSQL type | Canonical form | Why |
| --- | --- | --- |
| `numeric` | String, scale preserved (`"12.500"`) | A float cannot represent every decimal exactly, and scale is information |
| `timestamptz` | ISO-8601 UTC | One representation, stable ordering |
| `timestamp` (naive) | ISO-8601, assumed UTC | Guessing a local zone would shift every event |
| `bytea` | Base64 | May contain invalid UTF-8 |
| `boolean` | `true`/`false`, never 1/0 | `bool` is an `int` subclass in Python |
| `jsonb` | Recursed, keys sorted | Same content must serialise identically |

Normalisation lives in the ingestion layer, not the connector, so every
connector produces identical observations for identical values.

---

## Observations

```
organization_id   source_id   stream_id   external_id
payload           event_time  ingested_at fingerprint
entity_mapping_state          event_time_semantics    provenance
```

`event_time` and `ingested_at` are separate and always will be. Substituting
ingestion time for event time makes a late-arriving correction
indistinguishable from a fresh change, and that is unrecoverable once done.

Observations are **append-only**. A changed source row produces a *new*
observation; the original survives because it was true when it was made.

`entity_mapping_state` starts as `unmapped`. MVP entity resolution is
deterministic and manual, so RealitySync records the source's identifier and
nothing more. An invented identity would merge two real-world things
irreversibly, and no downstream reconciliation could recover from it.

---

## Limitations

Stated plainly, because a caller who assumes otherwise will be wrong.

| Limitation | Consequence |
| --- | --- |
| **Deletes are invisible** | PostgreSQL offers no change feed without logical replication. A deleted row simply stops appearing; its observations remain, correctly. |
| **Updates that do not advance the event time are missed on incremental syncs** | Use a full refresh for those tables, or a column that always advances. |
| **Sync runs inline** | Triggered by the API and completed within the request. Background scheduling belongs to a later phase. |
| **50,000 rows per sync** | `CONNECTOR_MAX_ROWS_PER_SYNC`. Bounds memory and run time. |
| **Public reachability required** | See the network requirement above. |
| **One connector type** | PostgreSQL. The interface is ready for more. |

---

## Connecting a real database

1. **Sources → Add source.**
2. Enter host, port, database, the read-only username and its password, and an
   SSL mode. `verify-full` for production.
3. **Save.** Credentials are encrypted immediately. The source is created as
   *Not yet tested* — RealitySync does not claim a connection it has not made.
4. **Test connection.** Reports the server version, negotiated TLS version, the
   role it authenticated as, latency, and whether discovery will work.
5. **Discover schema.** Metadata only.
6. **Configure** a table: confirm the identity columns, then say what its
   timestamp means.
7. **Sync now.** Real rows become real observations.
8. Sync history and the observations themselves appear on the same page.

### A disposable source database for development

`docker-compose.yml` includes a `source-postgres` service behind the
`dev-source` profile. **It is test infrastructure, not product data**: a
separate server, TLS-only, that RealitySync connects to exactly as it would
connect to a customer's database.

```bash
docker compose --profile dev-source up -d source-postgres

# Create your own table as the owner
PGPASSWORD=change-me-locally psql \
  "host=localhost port=5434 user=source_owner dbname=source_demo sslmode=require"
```

Then connect RealitySync to `source-postgres:5432` (from inside Compose) or
`localhost:5434` (from your machine) as `realitysync_reader`.

It starts **empty**. Nothing seeds it, and nothing seeds RealitySync's own
database — every observation in the product comes from a real source row.

---

## Troubleshooting

**"The database host name could not be resolved."**
The host is wrong, or it is a private address. The MVP needs a publicly
resolvable endpoint.

**"The database refused the connection."**
Wrong port, or the firewall is not allowing RealitySync's addresses.

**"The database refused an encrypted connection."**
TLS is off on the source. Enable `ssl` in `postgresql.conf`, or use a provider
endpoint that supports it. RealitySync will not fall back to plaintext.

**"The database's TLS certificate could not be verified."**
You chose `verify-ca`/`verify-full` against a self-signed certificate. Either
supply the correct CA or use `require` — with the understanding that `require`
does not defend against an active attacker.

**"The database rejected the username or password."**
Check the credentials, and that `pg_hba.conf` allows this role to connect over
TLS from RealitySync's address.

**"The database role does not have permission for this operation."**
Run the grants above. Discovery needs `USAGE` on the schema; sync needs
`SELECT` on the table.

**Discovery returns no tables.**
The role can connect but cannot read the catalog. The connection test says so
explicitly under "can discover schema".

**"A sync is already running for this source."**
Expected: the advisory lock is doing its job. The run is recorded as *skipped*,
not failed.

**A sync reports rows seen but none created.**
Correct behaviour on unchanged data — the fingerprints already exist. `skipped`
equals `seen`.

---

## Phase 3 boundary

Built: connector interface and registry, PostgreSQL connector, credential
encryption, data sources, streams, sync runs, observations, the API and the
Sources interface.

**Not built, by instruction:** reality state, confidence scoring, conflict
detection, temporal reconstruction, AI investigation, real-time streaming,
background scheduling, Kafka, Spark, Airflow, Redis job queues.
