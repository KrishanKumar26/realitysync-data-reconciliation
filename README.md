# RealitySync

**Know what is actually happening.**

RealitySync reconciles observations from multiple data sources — databases, APIs
and operational systems — into a continuously verified representation of
reality. It detects discrepancies between sources, explains them using evidence,
maintains historical state, and provides a trusted reality layer for
software and autonomous systems.

> **Status: Phase 5 — Conflicts and Timeline.**
> RealitySync ingests real data, resolves it to entities, detects when sources
> disagree, and reconstructs history on both time axes — what was true, and
> what we knew. **Confidence scoring is blocked**: the Phase 0 confidence
> specification is unrecoverable, so no score is produced rather than a guessed
> one. See [docs/phase-5-conflicts-and-timeline.md](docs/phase-5-conflicts-and-timeline.md).
> **Every value shown comes from a real source row — nothing is seeded, and no
> placeholder data pretends to be product data.**

---

## Architecture overview

```
External sources → Connectors → Observations → Normalisation → Validation
                                                                    ↓
                                                          Entity resolution
                                                                    ↓
        UI ← API ← Timeline / State ← Conflicts ← Reality Engine ←──┘
```

Five load-bearing decisions from the approved architecture:

1. **The observation is the atom.** Immutable, append-only, fingerprinted,
   carrying both `event_time` (when a fact was true) and `ingested_at` (when we
   learned it). Everything downstream is a function of observations.
2. **Confidence is a stored computation, not a number.** Each reality state
   persists its full component breakdown, so a score can always be re-derived
   and explained.
3. **Bitemporal from day one.** "What actually happened at 10:30?" and "What did
   RealitySync know at 10:30?" are different questions with different answers.
4. **The Reality Engine never imports a connector.** Connectors emit canonical
   observations; adding a source does not change the core.
5. **AI explains, never calculates.** Claude receives a frozen evidence bundle
   and returns prose with citations. It has no database access and no write path.

### Stack

| Layer     | Technology                                              |
| --------- | ------------------------------------------------------- |
| Frontend  | Next.js 15 (App Router), React 19, TypeScript, Tailwind 4 |
| Backend   | Python 3.12, FastAPI, Pydantic, SQLAlchemy 2, Alembic   |
| Database  | PostgreSQL 16 (psycopg3)                                |
| Cache     | Redis 7 — non-authoritative, three uses only            |
| Local dev | Docker Compose                                          |

---

## Prerequisites

| Tool           | Version | Notes                                  |
| -------------- | ------- | -------------------------------------- |
| Docker         | 24+     | With Compose v2                        |
| Node.js        | 20+     | Frontend                               |
| Python         | 3.12    | Backend (3.13+ is not yet supported)   |
| npm            | 10+     | Frontend package manager               |

Everything can also run entirely in Docker, in which case only Docker is required.

---

## Quick start

```bash
git clone <repository-url> realitysync
cd realitysync

cp .env.example .env      # then review it — see "Port conflicts" below

docker compose up
```

Once the stack reports healthy:

| Service           | URL                             |
| ----------------- | ------------------------------- |
| Web               | http://localhost:3000           |
| API               | http://localhost:8000           |
| API docs          | http://localhost:8000/docs      |
| Liveness          | http://localhost:8000/health    |
| Readiness         | http://localhost:8000/ready     |

Open the web app and **create a workspace** — that is the only way accounts
enter the system. There is no seeded user and no demo login, because a seeded
account is a credential shipped in a repository.

Once signed in you get the application shell with your workspace, an
organization selector (once you belong to more than one), member list and sign
out. The API connection indicator in the header is a live probe result, not a
decoration.

### Connect a database

**Sources → Add source** connects a PostgreSQL database over TLS, discovers its
schema, and syncs a table you choose into observations. See
[docs/phase-3-postgres-connector.md](docs/phase-3-postgres-connector.md) for
the required read-only role and supported SSL modes.

For development there is a disposable, TLS-only source database — **test
infrastructure, not product data**:

```bash
docker compose --profile dev-source up -d source-postgres
```

It starts empty. Create your own tables in it, or point RealitySync at a real
external database.

### Port conflicts

Host ports are shadowed silently: if another process already holds a port, your
container starts fine but you reach the *other* application. This is a common
source of confusing failures, so all four host ports are configurable in `.env`:

```
POSTGRES_PORT=5433   # deliberately not 5432 — a local Postgres.app or
REDIS_PORT=6380      # Homebrew PostgreSQL binds 127.0.0.1:5432 and wins
API_PORT=8000
WEB_PORT=3000
```

Check what holds a port before assuming the stack is broken:

```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN
```

If you change `WEB_PORT`, update `WEB_BASE_URL` and `CORS_ORIGINS` to match —
the API rejects requests from origins that are not on its explicit allowlist.

Containers always reach each other on internal ports (`postgres:5432`,
`redis:6379`) regardless of these values.

---

## Environment variables

Copy `.env.example` to `.env`. Never commit `.env`; it is git-ignored.

| Variable                  | Purpose                                                    |
| ------------------------- | ---------------------------------------------------------- |
| `ENVIRONMENT`             | `development` / `test` / `staging` / `production`           |
| `LOG_LEVEL`               | `DEBUG` … `CRITICAL`                                        |
| `POSTGRES_USER/PASSWORD/DB` | Credentials for the Postgres container                    |
| `DATABASE_URL`            | SQLAlchemy async URL (`postgresql+psycopg://…`)             |
| `REDIS_URL`               | Redis connection URL                                        |
| `API_BASE_URL`            | Public URL of the API                                       |
| `WEB_BASE_URL`            | Public URL of the frontend                                  |
| `CORS_ORIGINS`            | Comma-separated allowlist. `*` is rejected at startup        |
| `COOKIE_NAME/DOMAIN/SECURE/SAMESITE` | Session cookie contract                          |
| `CSRF_COOKIE_NAME` / `CSRF_HEADER_NAME` | Readable CSRF cookie and its echo header      |
| `SESSION_LIFETIME_SECONDS` | Absolute session expiry (default 14 days)                   |
| `SESSION_IDLE_TIMEOUT_SECONDS` | Inactivity expiry (default 24 hours)                    |
| `PASSWORD_MIN_LENGTH`     | Minimum password length (default 12)                         |
| `ARGON2_TIME_COST` / `ARGON2_MEMORY_COST_KIB` | Hashing cost; defaults follow OWASP  |
| `SECRET_KEY`              | Signing secret. Production refuses to start on the default   |
| `CREDENTIAL_ENCRYPTION_KEY` | Base64 AES-256 key for source credentials. Validated at startup |
| `CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS` | Retired keys (`version:base64`) kept for decryption |
| `CONNECTOR_CONNECT_TIMEOUT_SECONDS` | Outbound connection timeout to a source        |
| `CONNECTOR_MAX_ROWS_PER_SYNC` | Ceiling on rows read per sync (default 50,000)           |
| `NEXT_PUBLIC_API_URL`     | API URL baked into the frontend at build time                |

Production startup **fails fast** rather than running with unsafe defaults: an
unset `SECRET_KEY`, the published development `CREDENTIAL_ENCRYPTION_KEY`,
`COOKIE_SECURE=false`, or an `http://` CORS origin all raise at boot. Credential
encryption additionally self-tests at startup, so a key that decodes but does
not work stops the process rather than failing one sync at a time.

Generate a real encryption key with:

```bash
cd apps/api && .venv/bin/python -c \
  "from app.core.encryption import generate_key; print(generate_key())"
```

`NEXT_PUBLIC_*` values are inlined into the browser bundle. Never put a secret
behind that prefix.

---

## Running without Docker

### Backend

```bash
cd apps/api
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Datastores still come from Compose:
docker compose up -d postgres redis

.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

### Frontend

```bash
cd apps/web
npm ci
npm run dev
```

---

## Testing

### Backend

```bash
cd apps/api

.venv/bin/pytest -m "not integration"   # unit only, no services needed
.venv/bin/pytest -m integration         # requires postgres + redis running
.venv/bin/pytest                        # everything

.venv/bin/ruff check .                  # lint
.venv/bin/ruff format --check .         # formatting
.venv/bin/mypy app                      # strict type check
```

### Frontend

```bash
cd apps/web

npm run test        # vitest
npm run lint        # eslint
npm run typecheck   # tsc --noEmit
npm run build       # production build
```

### Test data policy

Mock data is forbidden in the product; test fixtures are required for
correctness. The line is enforced concretely:

- Engine logic is tested with hand-built objects and no I/O.
- Integration tests run against a **real** PostgreSQL and Redis in Docker.
- Nothing under `app/` may import from `tests/`.
- No fabricated metric may ever render in the UI.

---

## Database migrations

```bash
cd apps/api

.venv/bin/alembic upgrade head                          # apply
.venv/bin/alembic downgrade base                        # revert
.venv/bin/alembic revision --autogenerate -m "message"  # create
.venv/bin/alembic upgrade head --sql                    # preview SQL, no DB
.venv/bin/alembic current                               # show version
```

The database URL comes from application settings, never from `alembic.ini`, so
credentials live only in the environment.

**Current schema:** `0001_foundation` enables `citext`; `0002_identity_tenancy`
creates `users`, `organizations`, `memberships`, `sessions` and `audit_logs`;
`0003_connector` creates `data_sources`, `source_credentials`,
`source_streams`, `observations` and `sync_runs`. The entity and reality-state
tables arrive in Phases 4–5.

---

## Project structure

```
.
├── apps/
│   ├── api/                     FastAPI backend
│   │   ├── alembic/             Migration environment and versions
│   │   ├── app/
│   │   │   ├── api/routes/      HTTP route handlers
│   │   │   ├── cache/           Redis client lifecycle
│   │   │   ├── core/            Config, logging, secret redaction
│   │   │   ├── db/              Engine, session, declarative base
│   │   │   ├── middleware/      Request id, error envelope
│   │   │   ├── models/          ORM models: user, organization, membership,
│   │   │   │                      session, audit_log
│   │   │   ├── schemas/         Pydantic request/response models
│   │   │   ├── services/        Application services
│   │   │   └── main.py          Application factory
│   │   └── tests/
│   └── web/                     Next.js frontend
│       ├── src/
│       │   ├── app/             App Router pages and boundaries
│       │   ├── components/
│       │   │   ├── shell/       Application chrome
│       │   │   └── ui/          Reusable primitives
│       │   ├── lib/             API client, utilities
│       │   ├── styles/          Design tokens
│       │   └── types/           Ambient declarations
│       └── tests/
├── docs/                        Architecture and phase documentation
├── infra/docker/                Infrastructure assets
├── .github/workflows/           CI
├── docker-compose.yml
└── .env.example
```

---

## MVP PostgreSQL connectivity limitation

**RealitySync MVP supports PostgreSQL databases that are reachable from the
deployed backend.**

The backend connects outbound over TCP to a publicly resolvable endpoint. In
practice that means one of:

- A managed database with a public endpoint (Neon, Supabase, RDS with public
  access, Render PostgreSQL, Aiven).
- A self-hosted database with a public address whose firewall allows the
  backend's static egress IP addresses.

**Out of scope for the MVP:**

- Databases reachable only inside a private VPC or subnet
- Access via SSH tunnel or bastion host
- Tailscale, WireGuard or other mesh networks
- Corporate networks where IP allowlisting is not possible

Additional requirements:

- **TLS is required.** `sslmode=disable` is rejected. `require` is the minimum,
  `verify-full` is recommended. An endpoint that cannot negotiate TLS is not
  supported.
- **Credentials are encrypted at rest** and are never returned by the API,
  never written to logs, and never exposed to the frontend.
- **Access should be read-only.** Connections are opened with
  `default_transaction_read_only = on`, and a dedicated read-only role is
  recommended.

Private-network support is planned as a **RealitySync Agent** — a self-hosted
process that runs the same connector inside the customer network and pushes
canonical observations outbound. The architecture already accommodates it: the
Reality Engine consumes observations and has no knowledge of how they were
obtained, so no core change is required.

---

## Security

Infrastructure:

- Secrets are environment-only; `.env` and key material are git-ignored
- A root-level log redaction filter scrubs passwords, DSNs, tokens, cookies and
  PEM blocks from every log record, including exception text
- API errors return a uniform envelope with a request id and **no** stack
  traces, driver text or connection details. Validation errors report which
  field failed and why, never the value that was submitted
- CORS is an explicit allowlist; `*` is rejected at startup
- Production configuration is validated at boot and fails fast
- Containers run as non-root users
- Every request carries a correlation id, echoed in `X-Request-ID`

Authentication:

- Passwords hashed with **Argon2id** at OWASP-recommended cost, upgraded
  automatically when the cost is raised
- Sessions are **server-side and revocable**; only a SHA-256 hash of the token
  is stored, so a database disclosure yields no usable sessions
- Session cookie is **HttpOnly**; `Secure` and `SameSite` are environment-driven
  and validated at boot
- **CSRF**: a session-bound token for authenticated writes, validated against
  the session row rather than against the cookie; plus `Origin` validation on
  every state-changing request, which is what covers login CSRF
- Credential failures are indistinguishable in message *and* timing, so the
  login endpoint is not a user-enumeration oracle
- Password hashes have no field in any response type
- Security events are written to an append-only audit trail

Multi-tenancy is enforced in three independent layers — a database composite
foreign key, an ORM-level scope guard, and non-optional tenant ids in route
signatures. See [docs/phase-2-authentication.md](docs/phase-2-authentication.md).

Source credentials:

- Encrypted at rest with **AES-256-GCM**, an authenticated cipher, using an
  environment-provided key validated at startup — the process refuses to boot
  if it cannot decrypt
- Bound to their row by associated data, so a ciphertext copied to another
  source or another tenant will not decrypt
- Key versioning supports rotation without a data migration
- No response type in the API has a field for a credential, and exactly one
  function in the codebase returns plaintext

Arriving in later phases: Redis-backed rate limiting (the seam exists), member
invitations and password reset (both need email delivery).

---

## Documentation

| Document                                             | Contents                          |
| ---------------------------------------------------- | --------------------------------- |
| [docs/architecture.md](docs/architecture.md)          | Component boundaries and data flow |
| [docs/development.md](docs/development.md)            | Day-to-day workflow and troubleshooting |
| [docs/phase-1-foundation.md](docs/phase-1-foundation.md) | What Phase 1 delivers, and what it deliberately does not |
| [docs/phase-2-authentication.md](docs/phase-2-authentication.md) | Tenancy model, session design, CSRF strategy, isolation proofs |
| [docs/phase-3-postgres-connector.md](docs/phase-3-postgres-connector.md) | Connector architecture, SSL modes, permissions, sync and idempotency |
| [docs/phase-5-conflicts-and-timeline.md](docs/phase-5-conflicts-and-timeline.md) | Conflict detection, bitemporal timeline, and the isolated confidence dependency |
