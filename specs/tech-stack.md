# Tech stack

## Choices

| Layer | Choice | Why |
|---|---|---|
| API | **Python 3.12 + FastAPI**, run by `uvicorn` | Quick to write, its request models cover the Level 2 validation, and it's async. |
| ORM | **SQLModel** (async engine, `postgresql+psycopg` driver) | One class serves as both the table and the Pydantic shape. The concurrency guarantees still come from Postgres constraints and `INSERT ... ON CONFLICT`, not from the ORM. |
| Migrations | **Alembic** (`alembic/versions/`, autogenerate from SQLModel metadata, reviewed by hand) | Schema changes are versioned. Autogenerate misses some details, such as the partial unique index's `WHERE`, so every migration file gets checked. |
| Database | **Neon Postgres** (free tier) | Durable, and Postgres constraints and row locks give us correctness under concurrency without extra work. |
| Web page | **React + TypeScript + Vite + Tailwind** (no component library) | One page: slot list, booking form, confirmation. |
| Hosting | **Render web service** (Docker), with the API and page from **one origin** | One deploy, one URL, and no CORS. FastAPI serves the built page at `/`, and the API routes sit alongside it. |
| Tests | **pytest + httpx**, run against a local or the deployed base URL | The same checks prove each level locally and in production. |
| Python tooling | **uv** for everything: `uv init`, `uv add`, `uv run uvicorn ...`, `uv run pytest`, with a committed `uv.lock` | Fast, reproducible, one tool. No pip, venv, or requirements.txt. |
| JS tooling | **bun** for everything: `bun create vite`, `bun add`, `bun run dev/build`, with a committed `bun.lock` | Fast, one tool. No npm, npx, yarn, or pnpm. |

## Layout

```
clinic-app/
  specs/            mission.md, tech-stack.md, Roadmap.md
  pyproject.toml    uv project (+ uv.lock)
  api/              FastAPI app (main.py, db.py engine/session, models.py SQLModel tables)
  alembic/          env.py (target_metadata = SQLModel.metadata) + versions/
  alembic.ini
  web/              Vite React app (bun, bun.lock) → builds to web/dist
  tests/            pytest contract checks per level
  Dockerfile        stage 1: oven/bun, runs `bun install && bun run build` → web/dist
                    stage 2: python:3.12-slim + uv binary, `uv sync --frozen`, serves api + dist
                             with `uvicorn api.main:app --host 0.0.0.0 --port $PORT` (one worker)
```

## Config (environment variables)

- `DATABASE_URL`: the Neon **pooled** connection string as `postgresql+psycopg://...`, used by the app. Neon's pooler doesn't keep prepared statements between queries, so the engine sets `connect_args={"prepare_threshold": None}`.
- `DATABASE_URL_DIRECT`: the Neon **direct** (non-pooled) string, used by Alembic only. Migrations take locks and need a real session.
- `API_KEY`: the key we choose and submit in the form.
- No key in the page. `GET /` sets a random `sid` cookie (HttpOnly, Secure, SameSite=Lax; Lax so a link from another site keeps the session). Without a key, the middleware accepts that cookie on the page's routes only (`GET /slots`, `GET`/`POST /bookings`, `GET`/`DELETE /bookings/{id}`), and those routes only see bookings whose `session` matches it. In dev, the Vite proxy adds the key server-side from `API_KEY`.

## How each level is enforced

- **Auth (L2).** One middleware checks `X-API-Key` **before** the body is parsed, so a bad key returns 401 even when the body is also invalid. Everything needs the key except `GET /`, the page's assets, and `/health` (Render's health check and the keep-warm ping send no key). An unknown route without a key gets 401.
- **Booking ids (L1, L3).** The path parameter is a string, never `int`, because our validation handler would turn a failed `int` parse into 400. A non-numeric or out-of-range id on `GET` or `DELETE /bookings/{id}` returns **404**.
- **Error shape (L2).** Custom handlers for `RequestValidationError` (→ **400**), `HTTPException`, malformed JSON, and 404/405 all return `{"error": ...}`.
- **Validation (L2).** Trim `name` and `email`, lower-case the email, and reject empty values or an email that fails a simple `local@domain.tld` regex. Missing slot → 404. `slot.start <= now()` (UTC) → 400.
- **No double booking (L3).** A partial unique index, `one_confirmed_per_slot ON bookings(slot_id) WHERE status = 'confirmed'`, declared on the model and created by an Alembic migration. We insert, then catch SQLAlchemy's `IntegrityError` and map it to **409**, so there's no check-then-insert race. The insert runs inside a savepoint (`session.begin_nested()`), so the failure doesn't abort the outer transaction that holds the idempotency key. Cancelling is one `UPDATE ... SET status = 'cancelled' WHERE id = ... AND status = 'confirmed' RETURNING`, so two simultaneous cancels give one 200 and one 409, and the index frees the slot by itself.
- **Availability.** `available = NOT EXISTS (confirmed booking for slot)`, computed in the `GET /slots` query. The response is `{"slots": [...]}`, not a bare array.
- **Idempotency (L4).** Table `idempotency_keys(key PK, request_hash, status_code, body)`. In the booking transaction, run `INSERT ... ON CONFLICT DO NOTHING` first, written as `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_nothing()`. A concurrent duplicate blocks on the uncommitted key, then sees the conflict and replays the stored `status_code` + `body`, or returns 422 if `request_hash` differs. The hash covers the parsed JSON body (`sort_keys=True`). Error responses are stored too, so retries replay them exactly; that only works because of the savepoint around the booking insert (see above). Without it, the `IntegrityError` rolls the key row back and there's nothing to replay. The idempotency lookup runs **before** the rate limiter, and a replay doesn't count as an attempt.
- **Pagination (L4).** The booking `id` is an auto-incrementing integer issued as a string, so "oldest first" means `ORDER BY id`, and `next_cursor` is the last `id` on the page, as a string. We fetch `limit + 1` rows to tell whether a next page exists; if not, `next_cursor: null`. The response is `{"bookings": [...], "next_cursor": ...}`. A non-numeric `cursor` returns 400. The email match is case-insensitive because stored emails are already lower case.
- **Rate limit (L5).** An in-process sliding window, `dict[email] -> deque[timestamps]`, checked and updated with no `await` in between, so it's atomic on the event loop. The key is the trimmed, lower-cased email, so `Ada@x.com` and `ada@x.com` share a window. It counts every `POST /bookings` attempt whose body passes validation, failed outcomes (404, 400 already started, 409) included. It doesn't count idempotent replays, 422s, 429s, or bodies that fail validation (those are 400 before the handler runs). `Retry-After = ceil(oldest + 60 - now)`, minimum 1.
- **Reset (L1).** In one transaction, truncate bookings, idempotency keys, and slots, then insert the new slots (past slots included). It also clears the rate-limit window so test runs start clean.

## Models (`api/models.py`)

```python
class Slot(SQLModel, table=True):
    __tablename__ = "slots"
    id: str = Field(primary_key=True)
    provider: str = Field(index=True)
    start: datetime = Field(sa_type=DateTime(timezone=True))
    end: datetime = Field(sa_type=DateTime(timezone=True))

class Booking(SQLModel, table=True):
    __tablename__ = "bookings"
    __table_args__ = (
        Index("one_confirmed_per_slot", "slot_id", unique=True,
              postgresql_where=text("status = 'confirmed'")),
        Index("bookings_by_email", "email", "id"),
        CheckConstraint("status in ('confirmed','cancelled')"),
    )
    id: int | None = Field(default=None, primary_key=True)   # auto-incrementing integer, sent to clients as a string
    slot_id: str = Field(foreign_key="slots.id")
    name: str
    email: str                                               # stored trimmed and lower-cased
    status: str = "confirmed"
    created_at: datetime = Field(sa_type=DateTime(timezone=True),
                                 sa_column_kwargs={"server_default": func.now()})

class IdempotencyKey(SQLModel, table=True):
    __tablename__ = "idempotency_keys"
    key: str = Field(primary_key=True)
    request_hash: str
    status_code: int | None = None
    body: dict | None = Field(default=None, sa_type=JSON)  # json, not jsonb: keeps key order so replays are byte-identical
```

Request and response shapes (`NewBooking`, `BookingOut` with `id: str`, `SlotOut` with `available`) are plain SQLModel classes without `table=True`, and they match the OpenAPI field names exactly.

## Migrations

- `uv run alembic revision --autogenerate -m "..."`, then **read the generated file**. Check that the partial index has `postgresql_where`, `bookings.id` is an auto-incrementing integer primary key, and the `CHECK` constraint is there.
- On deploy, the container runs `uv run alembic upgrade head && uv run uvicorn ...`. Render's pre-deploy command is paid-only, and with a single instance there's no migration race.

## Known trade-offs

- **The page proves which browser you are, not who you are.** A page session reaches only the bookings it made, so clearing cookies or switching device loses the "My bookings" list (the bookings themselves stay). *Upgrade path:* a one-time code sent to the email. (Until Phase 6 the key shipped in the JS bundle, which let anyone list or cancel any booking.)
- **The rate limiter lives in memory, so it requires one uvicorn worker on one Render instance**, and a restart resets the window (at most one minute of leniency). *Upgrade path:* a Postgres or Redis counter if we ever scale out. Once more than 10,000 emails are tracked, each check drops the ones idle for a full window, so memory stays bounded.
- **The limit is per email, as the contract says, so anyone can use up another person's 5 attempts a minute** (the page path needs only the free `sid` cookie). *Mitigation if it matters:* an extra per-`sid` or per-IP cap on the page path.
- **Free Render instances sleep, and the first request after idle takes about 30s.** Graders may time out. *Mitigation:* use the paid Starter instance, or keep the service warm with an external ping to `/health` every 10 minutes, for as long as the submission is under review.
- **Neon auto-suspends**, adding about 1s to the first query after idle. That's acceptable.
