import hmac
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import StringConstraints
from sqlalchemy import exists, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.db import Session, engine
from api.models import Booking, Slot

app = FastAPI()

# The built page (web/dist): only GET / and /assets, so unknown routes stay ordinary API 404s.
DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="assets")

INT4_MAX = 2**31 - 1  # bookings.id is a Postgres integer

API_KEY = os.environ["API_KEY"]
assert API_KEY, "API_KEY must not be empty"  # fail closed: never boot without auth


class SlotIn(SQLModel):
    id: str
    provider: str
    start: datetime
    end: datetime


class ResetIn(SQLModel):
    slots: list[SlotIn]


class NewBooking(SQLModel):
    slot_id: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]  # blank is 400, but not trimmed
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1,
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


@app.exception_handler(Exception)
async def internal_error(request, exc):
    return error(500, "internal error")


@app.middleware("http")
async def require_api_key(request, call_next):
    # Runs before routing and body parsing, so a bad key is 401 even on unknown routes or bad JSON.
    path = request.url.path
    if request.method in ("GET", "HEAD") and (path in ("/", "/health") or path.startswith("/assets/")):
        return await call_next(request)
    if not hmac.compare_digest(request.headers.get("x-api-key", "").encode(), API_KEY.encode()):
        return error(401, "missing or invalid API key")
    return await call_next(request)


@app.get("/", include_in_schema=False)
async def page():
    return FileResponse(DIST / "index.html")


@app.get("/health")
async def health():
    async with engine.connect() as conn:
        await conn.execute(text("select 1"))
    return {"ok": True}


@app.post("/reset")
async def reset(body: ResetIn):
    async with Session.begin() as s:
        # No RESTART IDENTITY: an id issued before a reset must never point at a later booking.
        await s.execute(text("truncate bookings, slots"))
        s.add_all(Slot(**slot.model_dump()) for slot in body.slots)
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
async def create_booking(body: NewBooking):
    try:
        async with Session.begin() as s:
            slot = await s.get(Slot, body.slot_id)
            if slot is None:
                return error(404, "slot not found")
            if slot.start <= datetime.now(UTC):
                return error(400, "slot has already started")
            # No check for an existing booking: the partial unique index decides, atomically.
            booking = await s.scalar(
                insert(Booking).values(slot_id=body.slot_id, name=body.name, email=body.email).returning(Booking)
            )
    except IntegrityError as e:
        if e.orig.sqlstate == "23505":
            return error(409, "slot already booked")
        if e.orig.sqlstate == "23503":  # a concurrent /reset deleted the slot after we loaded it
            return error(404, "slot not found")
        raise
    return booking_out(booking)


@app.get("/bookings/{id}")
async def get_booking(id: str):  # str, not int: a bad id is 404, never a validation error
    if not (id.isascii() and id.isdigit()) or len(id) > 10 or int(id) > INT4_MAX:  # len first: int() refuses >4300 digits
        return error(404, "booking not found")
    async with Session() as s:
        booking = await s.get(Booking, int(id))
    if booking is None:
        return error(404, "booking not found")
    return booking_out(booking)
