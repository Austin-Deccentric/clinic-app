# Clinic booking API

A small clinic booking API (list slots, book, look up, cancel, page through bookings) with a one-page web UI on top. It's built with FastAPI, Postgres and React, and covers all five levels of the Koya Cohort 4 Task 1 contract.

- Live API and page: https://clinic-app-mh31.onrender.com
- Contract: https://koyaacademy.com/assessment-api/openapi.json (the API follows it exactly, so it serves as the API reference)

Every route except `GET /`, the page's assets, `/health`, `/docs` and `/openapi.json` needs an `X-API-Key` header. The page itself never holds the key: `GET /` gives each browser an HttpOnly session cookie, and the page only sees the bookings made with that cookie.

## Run locally

You need [uv](https://docs.astral.sh/uv/), [bun](https://bun.sh) and Docker.

```sh
# 1. Postgres
docker run -d --name clinic-pg -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:55432:5432 postgres:17-alpine

# 2. Env (see "Env vars" below)
cat > .env <<'EOF'
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:55432/postgres
DATABASE_URL_DIRECT=postgresql+psycopg://postgres:postgres@127.0.0.1:55432/postgres
API_KEY=pick-any-local-key
EOF

# 3. Migrations, then the page build, then the API
uv run --env-file .env alembic upgrade head
(cd web && bun install && bun run build)
uv run --env-file .env uvicorn api.main:app --reload
```

The API and the page are both at http://localhost:8000. For page work with hot reload, run `cd web && bun run dev` instead of building.

## Env vars

| Name | What it is |
|---|---|
| `API_KEY` | The value every API caller sends in `X-API-Key`. |
| `DATABASE_URL` | Postgres URL the app uses. On Neon this is the **pooled** URL (the app turns off prepared statements for it). |
| `DATABASE_URL_DIRECT` | Postgres URL Alembic uses for migrations. On Neon this is the **direct**, non-pooled URL. |

## Tests

The tests are black-box contract checks over HTTP, so the same suite runs against a local server or production:

```sh
uv run --env-file .env env BASE_URL=http://localhost:8000 pytest
uv run --env-file .env env BASE_URL=http://localhost:8000 SLOW=1 pytest -k slow   # the ~61s rate-limit window test
```

They call `POST /reset`, so **they wipe the database they point at**.

## Demo data

`scripts/demo-slots.json` holds 12 slots with three providers, on weekdays between 2026-12-01 and 2027-01-29. To load it (this replaces all slots and deletes all bookings):

```sh
curl -sS -X POST "$BASE_URL/reset" -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' --data @scripts/demo-slots.json
```

## The two biggest decisions

**1. Correctness lives in Postgres, not in app code.** The app never checks first and then inserts.

- **No double booking:** a partial unique index on `bookings(slot_id) WHERE status = 'confirmed'`. The app inserts, and a unique violation becomes a 409. Twenty simultaneous bookings for one slot give exactly one 201.
- **Idempotency:** the `Idempotency-Key` row is inserted first with `ON CONFLICT DO NOTHING`, in the same transaction as the booking. A concurrent duplicate waits on that row until the first request commits, then replays its stored status and body. The booking insert runs in a savepoint, so a 409 doesn't roll back the key row and can be replayed as well. The same key with a different body gets 422.
- **Cancel** is a single `UPDATE ... WHERE status = 'confirmed' RETURNING`, so two simultaneous cancels give one 200 and one 409.

**2. The rate limiter is in memory, on a single worker.** `POST /bookings` is limited to 5 attempts per email (trimmed, lower-cased) in a 60-second sliding window. The limiter is a deque per email in process memory, with no `await` between the check and the append, so it's atomic on the event loop. That makes it simple and exact, but **only on one process**, so the Dockerfile pins `--workers 1`. An idempotent replay doesn't count as an attempt.

## Known limits

- **Cold starts.** The service runs on Render's free instance, which sleeps after 15 idle minutes. The first request after that can take up to about a minute.
- **A restart empties the rate-limit windows**, because they live in memory.
- **The service can't scale out** past one worker on one instance without moving the limiter (see below).

## What's next

- Move the rate limiter to Postgres or Redis, so the service can run more than one worker or instance.
- Remove cold starts with a paid instance or a keep-warm ping.
