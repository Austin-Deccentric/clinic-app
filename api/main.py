from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import exists, insert, select, text
from sqlmodel import SQLModel

from api.db import Session, engine
from api.models import Booking, Slot

app = FastAPI()

# The built page (web/dist): only GET / and /assets, so unknown routes stay ordinary API 404s.
DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="assets")

INT4_MAX = 2**31 - 1  # bookings.id is a Postgres integer


class SlotIn(SQLModel):
    id: str
    provider: str
    start: datetime
    end: datetime


class ResetIn(SQLModel):
    slots: list[SlotIn]


class NewBooking(SQLModel):
    slot_id: str
    name: str
    email: str


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
    async with Session.begin() as s:
        booking = await s.scalar(
            insert(Booking).values(slot_id=body.slot_id, name=body.name, email=body.email).returning(Booking)
        )
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
