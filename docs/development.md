# Development guide

## First run

```bash
cp .env.example .env
docker compose up
```

Compose starts services in dependency order and waits on health checks, so a
stack that reaches healthy is genuinely working end to end:

```
postgres (healthy) ─┬─► migrate (runs alembic upgrade head, exits 0)
redis    (healthy) ─┘        │
                             ▼
                        api (healthy) ──► web
```

Verify:

```bash
curl -s localhost:8000/health   # {"status":"ok",...}
curl -s localhost:8000/ready    # {"status":"ready","database":"ok","redis":"ok",...}
```

---

## Common commands

```bash
docker compose up -d              # start detached
docker compose logs -f api        # follow API logs
docker compose ps                 # service status
docker compose restart api        # restart one service
docker compose down               # stop
docker compose down -v            # stop and delete the database volume
docker compose up -d --build      # rebuild after dependency changes
```

Source changes hot-reload in both applications; only dependency changes need a
rebuild.

---

## Health endpoints

| Endpoint  | Checks                    | Codes            | Purpose                              |
| --------- | ------------------------- | ---------------- | ------------------------------------ |
| `/health` | Nothing — process only    | 200              | Platform liveness probe              |
| `/ready`  | PostgreSQL + Redis        | 200 / 503        | Deployment gating, operator insight  |

`/health` deliberately does not touch dependencies. If it did, a Redis outage
would fail the liveness check and put the API into a restart loop — turning a
degradation into an outage. There is a test asserting this.

`/ready` reports per-component status and latency, and returns 503 if any
dependency is unusable. Failure detail is a safe summary (`timeout`,
`unavailable`); the full exception goes to the logs, where the redaction filter
scrubs it.

---

## Troubleshooting

### "role realitysync does not exist" from the host

Something else is listening on your Postgres port. A locally installed
PostgreSQL binds `127.0.0.1:5432` specifically, which beats Docker's wildcard
bind — so host tools reach the wrong server while containers are unaffected.

```bash
lsof -nP -iTCP:5432 -sTCP:LISTEN
```

`.env` defaults to `POSTGRES_PORT=5433` to avoid this. If you still collide,
pick another port and update `DATABASE_URL` to match.

### The web page shows a different application

Same cause, port 3000. Check the listener and change `WEB_PORT`:

```bash
lsof -nP -iTCP:3000 -sTCP:LISTEN
```

Remember to update `WEB_BASE_URL` and `CORS_ORIGINS` to the new port, or the
API will reject the browser's requests.

### "API unavailable" in the interface

1. `docker compose ps` — is `api` healthy?
2. `docker compose logs api` — did startup fail?
3. `curl -s localhost:8000/health` — does it respond directly?
4. Confirm `NEXT_PUBLIC_API_URL` matches the API's actual port. It is baked in
   at build time, so changing it needs a rebuild of the web service.

### CORS errors in the browser console

`CORS_ORIGINS` must contain the exact origin the browser is using, including
scheme and port. `*` is rejected at startup because the API sends credentials.

### Docker daemon keeps stopping

Usually a full disk — the VM cannot allocate space and exits.

```bash
df -h /System/Volumes/Data     # macOS
docker system df               # what Docker is using
docker builder prune           # reclaim build cache
docker system prune -a         # reclaim everything unused (destructive)
```

### Migration fails on startup

```bash
docker compose logs migrate
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic upgrade head
```

---

## Adding a dependency

**Backend** — add it to `apps/api/pyproject.toml`, then:

```bash
cd apps/api && .venv/bin/pip install -e ".[dev]"
docker compose up -d --build api
```

**Frontend** — `cd apps/web && npm install <package>`, then rebuild the web
service. Commit `package-lock.json`.

---

## Code quality gates

Both applications are checked in CI, and the same commands run locally:

| Check       | Backend                        | Frontend            |
| ----------- | ------------------------------ | ------------------- |
| Lint        | `ruff check .`                 | `npm run lint`      |
| Format      | `ruff format --check .`        | (ESLint covers it)  |
| Types       | `mypy app` (strict)            | `npm run typecheck` |
| Tests       | `pytest`                       | `npm run test`      |
| Build       | Docker image                   | `npm run build`     |

Type checking is strict on both sides. That is deliberate: the domain is full of
optional values with real meaning — a null `entity_id` means "unmapped", not
"missing" — and the type system should carry that distinction.

---

## Conventions

**Backend**

- Type hints everywhere; `mypy --strict` must pass
- Pydantic models at the API boundary; ORM objects never cross it
- Services own transactions; routes stay thin
- Structured logging with event names (`http.request`, `health.database.error`)
- Never log a secret — the redaction filter is a backstop, not permission

**Frontend**

- Server Components by default; `"use client"` only where interactivity requires
- `@/` path alias for imports
- Every list and panel has designed loading, empty and error states
- Confidence colours come from the shared tokens — components never invent them
- No metric may be displayed unless a real endpoint produced it
