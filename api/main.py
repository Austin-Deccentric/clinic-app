import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, Query, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import StringConstraints
from sqlalchemy import exists, insert, select, text, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DataError, IntegrityError
from sqlmodel import SQLModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.db import Session, engine
from api.models import Booking, IdempotencyKey, Slot

# Only puts the key in the schema so /docs gets an Authorize button; the middleware does the real check.
app = FastAPI(dependencies=[Security(APIKeyHeader(name="X-API-Key", auto_error=False))])

# The built page (web/dist): only GET / and /assets, so unknown routes stay ordinary API 404s.
DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="assets")

INT4_MAX = 2**31 - 1  # bookings.id is a Postgres integer

API_KEY = os.environ["API_KEY"]
assert API_KEY, "API_KEY must not be empty"  # fail closed: never boot without auth

# The page never holds the API key. GET / gives each browser a random sid cookie instead, which only
# reaches the page's own routes, and only the bookings that browser made.
SID = re.compile(r"[A-Za-z0-9_-]{43}")  # secrets.token_urlsafe(32)
PAGE_ROUTES = re.compile(r"GET /slots|(GET|POST) /bookings|(GET|DELETE) /bookings/[^/]+")

# shortcut: in-process, so only correct with one worker on one instance; move to Postgres or Redis if
# we ever scale out.
WINDOW, LIMIT, MAX_EMAILS = 60, 5, 10_000
attempts: defaultdict[str, deque[float]] = defaultdict(deque)


def check_rate(email: str) -> None:
    """Record one booking attempt, or raise 429. No await inside, so it's atomic on the event loop."""
    now = time.monotonic()
    if len(attempts) > MAX_EMAILS:  # drop idle emails, so a stream of one-shot addresses can't grow memory forever
        for k in [k for k, d in attempts.items() if d[-1] <= now - WINDOW]:
            del attempts[k]
    q = attempts[email]
    while q and q[0] <= now - WINDOW:
        q.popleft()
    if len(q) >= LIMIT:
        raise StarletteHTTPException(429, "too many booking attempts for this email",
                                     headers={"Retry-After": str(max(1, ceil(q[0] + WINDOW - now)))})
    q.append(now)


class SlotIn(SQLModel):
    id: str
    provider: str
    start: datetime
    end: datetime


class ResetIn(SQLModel):
    slots: list[SlotIn]


class NewBooking(SQLModel):
    slot_id: Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"\S")]  # blank is 400, not trimmed
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=254,
                                            pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def booking_out(b: Booking) -> dict:
    return {
        "id": str(b.id),
        "slot_id": b.slot_id,
        "name": b.name,
        "email": b.email,
        "status": b.status,
        "created_at": iso(b.created_at),
    }


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.exception_handler(RequestValidationError)
async def validation_failed(request, exc):  # 400, never FastAPI's 422: the contract keeps 422 for idempotency
    e = exc.errors()[0]
    loc = tuple(e["loc"])
    if e["type"] == "json_invalid":
        msg = "invalid JSON body"
    elif loc == ("body",):
        msg = "request body must be a JSON object"
    elif e["type"] in ("missing", "string_too_short"):
        msg = f"{loc[-1]} is required"
    else:
        msg = f"{loc[-1]} is invalid"
    return error(400, msg)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return JSONResponse({"error": str(exc.detail).lower()}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(DataError)
async def bad_data(request, exc):  # e.g. a NUL byte, which Postgres text can't hold: the client's input, so 400
    return error(400, "invalid characters in input")


@app.exception_handler(Exception)
async def internal_error(request, exc):
    return error(500, "internal error")


@app.middleware("http")
async def require_api_key(request, call_next):
    # Runs before routing and body parsing, so a bad key is 401 even on unknown routes or bad JSON.
    path = request.url.path
    if request.method == "GET" and (path in ("/", "/health", "/docs", "/openapi.json") or path.startswith("/assets/")):
        return await call_next(request)
    request.state.sid = None
    if hmac.compare_digest(request.headers.get("x-api-key", "").encode(), API_KEY.encode()):
        return await call_next(request)
    sid = request.cookies.get("sid", "")
    if SID.fullmatch(sid) and PAGE_ROUTES.fullmatch(f"{request.method} {path}"):
        request.state.sid = sid
        return await call_next(request)
    return error(401, "missing or invalid API key")


def mine(request: Request):
    """Page requests see only their own browser's bookings; API-key requests see all of them."""
    sid = request.state.sid
    return Booking.session == sid if sid else true()


@app.get("/", include_in_schema=False)
async def page(request: Request):
    res = FileResponse(DIST / "index.html", headers={"Cache-Control": "no-store"})  # never cache a Set-Cookie
    if not SID.fullmatch(request.cookies.get("sid", "")):
        # Lax, not Strict: Strict drops the cookie on a link from another site, and we would reissue it and lose
        # the history. Lax still keeps it off cross-site POST/DELETE, the only state-changing routes.
        res.set_cookie("sid", secrets.token_urlsafe(32), max_age=365 * 86400,
                       httponly=True, secure=True, samesite="lax")
    return res


@app.get("/health")
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("select 1"))
    return {"ok": True}


@app.post("/reset")
async def reset(body: ResetIn):
    async with Session.begin() as s:
        # No RESTART IDENTITY: an id issued before a reset must never point at a later booking.
        await s.execute(text("truncate bookings, idempotency_keys, slots"))
        s.add_all(Slot(**slot.model_dump()) for slot in body.slots)
    attempts.clear()
    return {"ok": True}


@app.get("/slots")
async def list_slots(provider: str | None = None):
    booked = exists().where(Booking.slot_id == Slot.id, Booking.status == "confirmed")
    q = select(Slot, ~booked).order_by(Slot.start, Slot.id)
    if provider is not None:
        q = q.where(Slot.provider == provider)
    async with Session() as s:
        rows = (await s.execute(q)).all()
    return {
        "slots": [
            {"id": slot.id, "provider": slot.provider, "start": iso(slot.start),
             "end": iso(slot.end), "available": available}
            for slot, available in rows
        ]
    }


@app.post("/bookings", status_code=201)
async def create_booking(body: NewBooking, request: Request,
                         idempotency_key: Annotated[str | None, Header(max_length=255)] = None):
    async with Session.begin() as s:
        if idempotency_key:
            request_hash = hashlib.sha256(json.dumps(await request.json(), sort_keys=True).encode()).hexdigest()
            # Claim the key first. A concurrent duplicate blocks here until the first request commits,
            # then hits the conflict and replays what that request stored.
            claimed = await s.scalar(
                pg_insert(IdempotencyKey).values(key=idempotency_key, request_hash=request_hash)
                .on_conflict_do_nothing().returning(IdempotencyKey.key)
            )
            if claimed is None:
                prior = await s.get(IdempotencyKey, idempotency_key)
                if prior.request_hash != request_hash:
                    return error(422, "idempotency key reused with a different body")
                return JSONResponse(prior.body, status_code=prior.status_code)
        # After the replay (replays don't count); raising inside begin() rolls back a fresh key claim.
        check_rate(body.email)
        status, out = await book_slot(s, body, request.state.sid)
        if idempotency_key:
            await s.execute(update(IdempotencyKey).where(IdempotencyKey.key == idempotency_key)
                            .values(status_code=status, body=out))
    return JSONResponse(out, status_code=status)


async def book_slot(s, body: NewBooking, sid: str | None) -> tuple[int, dict]:
    slot = await s.get(Slot, body.slot_id)
    if slot is None:
        return 404, {"error": "slot not found"}
    if slot.start <= datetime.now(UTC):
        return 400, {"error": "slot has already started"}
    try:
        # Savepoint: a failed insert must not roll back the idempotency key row, so the 409 can be replayed.
        async with s.begin_nested():
            # No check for an existing booking: the partial unique index decides, atomically.
            booking = await s.scalar(
                insert(Booking).values(slot_id=body.slot_id, name=body.name, email=body.email, session=sid)
                .returning(Booking)
            )
    except IntegrityError as e:
        if e.orig.sqlstate == "23505":
            return 409, {"error": "slot already booked"}
        if e.orig.sqlstate == "23503":  # a concurrent /reset deleted the slot after we loaded it
            return 404, {"error": "slot not found"}
        raise
    return 201, booking_out(booking)


def booking_id(id: str) -> int | None:
    """Path ids arrive as str, not int: a bad id is 404, never a validation error."""
    if not (id.isascii() and id.isdigit()) or len(id) > 10 or int(id) > INT4_MAX:  # len first: int() refuses >4300 digits
        return None
    return int(id)


@app.get("/bookings")
async def list_bookings(email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True,
                                                                min_length=1, max_length=254)],
                        request: Request, limit: Annotated[int, Query(ge=1, le=50)] = 10,
                        cursor: str | None = None):
    after = 0
    if cursor is not None:
        after = booking_id(cursor)
        if after is None:
            return error(400, "cursor is invalid")
    # Ids only grow, so id order is oldest first. Fetch one extra row to know if another page exists.
    q = select(Booking).where(Booking.email == email, Booking.id > after, mine(request)).order_by(Booking.id).limit(limit + 1)
    async with Session() as s:
        rows = (await s.scalars(q)).all()
    more = len(rows) > limit
    return {"bookings": [booking_out(b) for b in rows[:limit]],
            "next_cursor": str(rows[limit - 1].id) if more else None}


@app.get("/bookings/{id}")
async def get_booking(id: str, request: Request):
    bid = booking_id(id)
    if bid is None:
        return error(404, "booking not found")
    async with Session() as s:
        booking = await s.scalar(select(Booking).where(Booking.id == bid, mine(request)))
    if booking is None:
        return error(404, "booking not found")
    return booking_out(booking)


@app.delete("/bookings/{id}")
async def cancel_booking(id: str, request: Request):
    bid = booking_id(id)
    if bid is None:
        return error(404, "booking not found")
    async with Session.begin() as s:
        # One conditional UPDATE: of two simultaneous cancels, the second sees no confirmed row and gets 409.
        booking = await s.scalar(
            update(Booking).where(Booking.id == bid, Booking.status == "confirmed", mine(request))
            .values(status="cancelled").returning(Booking)
        )
        if booking is None:
            if await s.scalar(select(Booking.id).where(Booking.id == bid, mine(request))) is None:
                return error(404, "booking not found")
            return error(409, "booking already cancelled")
    return booking_out(booking)
