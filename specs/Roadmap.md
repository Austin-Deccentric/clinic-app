# Roadmap

Very small phases. **Each phase ends green**, meaning its pytest checks pass, and from Phase 2 onward it's also **deployed**. If time runs out, stop at any phase and submit what's live.

**Hard stop: 22:00 WAT.** At that time, stop building and go straight to Phase 8 with whatever level is live.

Checks live in `tests/test_level<N>.py` and run with `BASE_URL=... uv run pytest` (local or Render). Python tooling is uv only, and JS tooling is bun only.

---

## Phase 0: Skeleton deployed (about 45 min)
- [x] 0.1 Create a git repo in `clinic-app/` and push it to GitHub as a public repo.
- [x] 0.2 Run `uv init` and `uv add fastapi uvicorn sqlmodel alembic "psycopg[binary]"`, plus `uv add --dev pytest httpx`. Write a FastAPI app with `GET /health` → `{"ok": true}`, run with `uv run uvicorn api.main:app`.
- [x] 0.3 Write the Dockerfile (uvicorn with `--host 0.0.0.0 --port $PORT`) and deploy to a Render web service. Confirm the `https` URL responds.
- [x] 0.4 Create the Neon project. Run `uv run alembic init -t async alembic` and point `env.py` at `SQLModel.metadata` and `DATABASE_URL_DIRECT`. Set both DB URLs on Render. Have `/health` run `select 1`.
- [x] 0.5 Write the `Slot` and `Booking` models (with the partial unique index), autogenerate the first migration, check it by hand, and have the container run `alembic upgrade head` on boot.

## Phase 1: Level 1 (list, book, look up)
- [x] 1.1 `POST /reset`: replace all slots and delete all bookings.
- [x] 1.2 `GET /slots` → `{"slots": [...]}`: earliest `start` first, `?provider=` exact match, `available` computed.
- [x] 1.3 `POST /bookings` → 201 with a `Booking` (`status: confirmed`, `created_at` in ISO UTC).
- [x] 1.4 `GET /bookings/{id}` → 200, or 404 `{"error"}`. Take the id as a string: a non-numeric or out-of-range id is 404, not 400.
- [x] 1.5 Write `test_level1.py`, then deploy and run it against Render.

## Phase 2: Web page (first usable version)
- [x] 2.1 Scaffold `web/` with `bun create vite web --template react-ts`, then `bun add` Tailwind. No component library. Use bun for every JS command.
- [x] 2.2 Build the slot list from `GET /slots` (available slots are bookable; booked ones are shown disabled).
- [x] 2.3 Build the booking form (name, email) → `POST /bookings`, with server errors shown inline.
- [x] 2.4 Build the confirmation view (booking id, provider, time) and refresh the slots afterwards.
- [x] 2.5 Serve `web/dist` at `/` from FastAPI through a multi-stage Dockerfile (a bun stage builds `web/dist`, a uv stage runs the API), then deploy and click through on mobile width.

## Phase 3: Level 2 (auth and validation)
- [x] 3.1 API-key middleware: 401 `{"error"}` before body parsing. Exempt only `GET /`, the page's assets, and `/health`. Have the page send `VITE_API_KEY`: add `ARG VITE_API_KEY` to the Dockerfile's bun stage, and after deploying, check that the page's requests carry the key.
- [x] 3.2 Make every error `{"error": ...}`: validation → **400**, bad JSON → 400, unknown routes → 404.
- [x] 3.3 Booking validation: required and non-empty fields, email format, trimming, lower-case email. Unknown slot → 404. Already-started slot → 400.
- [x] 3.4 Write `test_level2.py`, then deploy.

## Phase 4: Level 3 (no double booking, cancel)
- [x] 4.1 Confirm in Neon that the partial unique index exists (`\d bookings`), then map `IntegrityError` → 409.
- [x] 4.2 Concurrency test: fire 20 simultaneous `POST /bookings` at one slot, **each with a different email** (so the Phase 7 limiter doesn't turn them into 429s), and assert exactly one 201 and nineteen 409s.
- [x] 4.3 `DELETE /bookings/{id}` → 200 with the cancelled booking. Unknown or non-numeric id → 404. Already cancelled → 409. The slot becomes available again. Cancel with one `UPDATE ... WHERE status = 'confirmed' RETURNING`.
- [x] 4.4 Write `test_level3.py` (including rebooking a freed slot), then deploy and run the concurrency test against Render.

## Phase 5: Level 4a (idempotency)
- [x] 5.1 Add the `IdempotencyKey` model and an Alembic migration for it. Do the insert-first logic (`pg insert().on_conflict_do_nothing()`) in the booking transaction and store the status and body. Put the booking insert in a savepoint (`session.begin_nested()`), or the `IntegrityError` rolls the key row back and the 409 can't be replayed.
- [x] 5.2 The same key and body → identical response (status and `id`). A different body → 422.
- [x] 5.3 Concurrency test: 10 simultaneous requests with the same key → one booking, and all 10 responses identical.
- [x] 5.4 Have the page send a fresh `Idempotency-Key` (`crypto.randomUUID()`) for each submit, reused on retry. Deploy.

## Phase 6: Level 4b (pagination)
- [x] 6.1 `GET /bookings?email=&limit=&cursor=`: oldest first, case-insensitive email, `limit` from 1 to 50 (default 10, anything else → 400). The response is `{"bookings": [...], "next_cursor": ...}`. A non-numeric `cursor` → 400.
- [x] 6.2 `next_cursor` is the last id as a string, or `null` on the last page. Tests: walking every page yields every booking exactly once. Use at most 5 bookings per email with `limit=2`, so the test still passes once the Phase 7 limiter exists.
- [x] 6.3 Optional: a "My bookings" panel on the page. Skip it if time is short. Deploy.

## Phase 7: Level 5 (rate limit)
- [ ] 7.1 Sliding window per trimmed, lower-cased email on `POST /bookings`, with failed attempts counted. The 6th attempt in 60s → 429 plus `Retry-After` in whole seconds. The idempotency lookup runs first, and a replay doesn't count. The limiter sits after the key claim in the same transaction, so raise the 429 as an `HTTPException` inside `Session.begin()`, which rolls the claim back: an early return would commit the claim with a null status and body, and every retry with that key would 500.
- [ ] 7.2 Tests: the 6th attempt gets 429 while another email still succeeds, and `Retry-After` is an integer of at least 1. `/reset` clears the window. Rerun `test_level3.py` and the Phase 5 and 6 tests to confirm the limiter didn't break them (5.3's ten same-key requests must still all match).
- [ ] 7.3 Pin a single worker in the Dockerfile `CMD` and deploy.

## Phase 8: Harden and submit
- [ ] 8.1 Run every `test_level*.py` against the production URL, then `POST /reset` to a clean demo data set.
- [ ] 8.2 Deal with cold starts (Render Starter instance or a keep-warm ping).
- [ ] 8.3 Write the README: how to run, env vars, the two biggest decisions, and what we'd do next.
- [ ] 8.4 Record the Loom (5 minutes or less), fill in the Tally form, and **freeze the deploy**: turn off auto-deploy on Render so a later push can't redeploy the service.

---

## Open questions to settle while building
- **Does an idempotent replay count toward the 5-per-minute rate limit?** Decided: no. The idempotency lookup runs before the limiter, and a replay isn't a new attempt. Revisit only if the graders' Level 5 check expects otherwise.
- **How should the limiter key an attempt whose email is missing or invalid?** Current plan: don't rate-limit it (it gets a 400 anyway). Revisit if the spec's checks expect otherwise.
