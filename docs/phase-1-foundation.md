# Phase 1 — Foundation

## Objective

Establish a production-grade project foundation: a safe repository, the
monorepo structure, a local development environment, a FastAPI skeleton, a
Next.js shell, database and cache connectivity, the migration system, health
endpoints, CI and documentation.

## Delivered

### Repository safety

The project has its own Git repository root. The pre-existing repository at the
user's home directory is **not** used, not modified, and cannot capture project
files. `.gitignore` was written before the first `git add` and explicitly
ignores personal paths (`.ssh`, `.config`, `.gitconfig`, shell and database
history) as defence in depth against an accidental `git add` from a parent
directory.

### Backend

| Module                    | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------- |
| `app/core/config.py`      | Environment-driven settings; production hardening at boot       |
| `app/core/logging.py`     | structlog JSON logging with request-id binding                  |
| `app/core/redaction.py`   | Secret redaction by key name and by pattern                     |
| `app/db/base.py`          | Declarative base with a stable constraint naming convention     |
| `app/db/session.py`       | Async engine, pooling, session factory                          |
| `app/cache/redis.py`      | Redis client lifecycle                                          |
| `app/middleware/`         | Request correlation, uniform error envelope                     |
| `app/services/health.py`  | Concurrent dependency probes with safe failure summaries        |
| `app/api/routes/health.py`| `/health` and `/ready`                                          |

### Frontend

Application shell with sidebar navigation, responsive layout, dark/light design
tokens, and reusable primitives (`Panel`, `Button`, `StatusDot`, `Skeleton`,
`EmptyState`, `ErrorState`). A typed API client with correlation ids, timeouts
and normalised errors. Route-level loading, error and not-found boundaries.

The only live value in the interface is the API connection status, and it is
genuinely live.

### Infrastructure

Docker Compose with PostgreSQL, Redis, a one-shot migration container, the API
and the web app, wired with health checks and dependency conditions. Alembic
configured with the URL sourced from application settings, plus one foundation
migration enabling `citext`.

---

## Deliberately not built

Building any of these now would mean inventing data, so none of them exist:

| Not built            | Phase | Why it matters                                    |
| -------------------- | ----- | ------------------------------------------------- |
| ORM models           | 2–5   | The 21-table schema belongs with its features     |
| Authentication       | 2     | Only the cookie configuration contract is settled |
| Connectors           | 3     | Real data only, once there is something to read   |
| Reality Engine       | 4     | Deterministic core, built against real observations|
| Conflict engine      | 5     | Requires observations from two sources            |
| Dashboard metrics    | 6     | A fabricated confidence score is worse than none  |
| SSE / real-time      | 6     | Needs events to stream                            |
| AI investigation     | 8     | Needs evidence to explain                         |

The navigation exists, and each unbuilt destination says plainly which phase
makes it functional rather than presenting an empty screen as finished work.

---

## Decisions taken during implementation

These were not specified in the architecture and were settled here.

**`npm` instead of `pnpm`.** `pnpm` was not available in the environment. No
architectural consequence; `package-lock.json` is committed and CI uses `npm ci`.

**Health endpoints at the root, not under `/api`.** `/health` and `/ready` are
infrastructure probes rather than API resources. Product routers will mount
under `/api` from Phase 2.

**Non-standard default host ports for datastores.** `POSTGRES_PORT=5433` and
`REDIS_PORT=6380`. A locally installed PostgreSQL binds `127.0.0.1:5432`
specifically, which silently beats Docker's wildcard bind — the container starts
but host tools reach the wrong server, failing with a confusing
`role "realitysync" does not exist`. This was observed during Phase 1
validation. Container-to-container traffic is unaffected.

**All four host ports parameterised.** `API_PORT` and `WEB_PORT` likewise, after
a pre-existing process on port 3000 shadowed the web container.

**Lazy connection initialisation.** The engine and Redis client are created on
first use rather than at startup, so the API can boot and serve `/health` while
a dependency is still coming up. `/ready` reports the truth.

**`citext` only in the foundation migration.** PostgreSQL 16 provides
`gen_random_uuid()` natively, so `pgcrypto` was not enabled speculatively.

---

## Bug found and fixed during validation

`CORS_ORIGINS` could not be loaded from a `.env` file. pydantic-settings
JSON-decodes complex types before field validators run, so a plain
`CORS_ORIGINS=http://localhost:3000` raised `SettingsError` at import time.

It was invisible to the initial test run because no `.env` file existed yet — it
surfaced only when the real environment file was created. Fixed with a
`NoDecode` annotation, and covered by two regression tests, one of which writes
an actual `.env` file rather than setting environment variables, because the
environment-variable path never reproduced the failure.

---

## Verification performed

| # | Check                                    | Result                                        |
| - | ---------------------------------------- | --------------------------------------------- |
| 1 | Git root is the project directory        | Pass                                          |
| 2 | No personal files tracked                | Pass — only project files staged              |
| 3 | `.gitignore` exists and covers secrets   | Pass                                          |
| 4 | Docker Compose starts                    | Pass — all services healthy                   |
| 5 | PostgreSQL works                         | Pass — migration applied, `citext` present    |
| 6 | Redis works                              | Pass — `PING`/`PONG`, round-trip via API      |
| 7 | FastAPI starts                           | Pass — healthy in container and natively      |
| 8 | Next.js starts                           | Pass — serves the real shell                  |
| 9 | `/health` works                          | Pass — 200, correct payload                   |
|10 | `/ready` works                           | Pass — 200 ready; 503 with a dependency down  |
|11 | Frontend reaches the API                 | Pass — CORS preflight and request verified    |
|12 | Alembic configured                       | Pass — upgrade, downgrade, offline SQL        |
|13 | Backend tests pass                       | Pass — 53 tests                               |
|14 | Frontend tests pass                      | Pass — 17 tests                               |
|15 | Frontend build passes                    | Pass — no warnings                            |
|16 | Backend checks pass                      | Pass — ruff, ruff format, mypy strict         |
|17 | No secrets committed                     | Pass — `.env` ignored, no key material        |
|18 | No mock business data                    | Pass — no fabricated entities or metrics      |

Beyond the checklist: graceful degradation was tested by stopping Redis —
`/health` stayed 200, `/ready` returned 503 with per-component detail and no
leaked connection details, and the client reconnected automatically when Redis
returned.

---

## Phase 2 readiness

Phase 2 (authentication, organisations, tenancy) depends only on foundation
pieces that now exist: the settings object carries the cookie contract, the
declarative base has a naming convention ready for the first tables, the
migration system is proven in both directions, and the error envelope and
request correlation are in place for audit logging to build on.
