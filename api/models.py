from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, func, text
from sqlmodel import Field, SQLModel


class Slot(SQLModel, table=True):
    __tablename__ = "slots"
    id: str = Field(primary_key=True)
    provider: str = Field(index=True)
    start: datetime = Field(sa_type=DateTime(timezone=True))
    end: datetime = Field(sa_type=DateTime(timezone=True))


class Booking(SQLModel, table=True):
    __tablename__ = "bookings"
    __table_args__ = (
        # No double booking: at most one confirmed booking per slot, enforced by Postgres.
        Index("one_confirmed_per_slot", "slot_id", unique=True,
              postgresql_where=text("status = 'confirmed'")),
        Index("bookings_by_email", "email", "id"),
        CheckConstraint("status in ('confirmed','cancelled')", name="bookings_status_check"),
    )
    id: int | None = Field(default=None, primary_key=True)  # auto-incrementing; sent to clients as a string
    slot_id: str = Field(foreign_key="slots.id")
    name: str
    email: str  # stored trimmed and lower-cased
    status: str = "confirmed"
    created_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "nullable": False},
    )
