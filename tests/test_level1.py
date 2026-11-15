import os
from datetime import datetime

import httpx
import pytest

BASE_URL = os.environ["BASE_URL"].rstrip("/")
api = httpx.Client(base_url=BASE_URL, headers={"X-API-Key": os.environ.get("API_KEY", "")}, timeout=60)

SLOTS = [
    # Deliberately out of order, so the test proves the API sorts by start.
    {"id": "s3", "provider": "Dr Bello", "start": "2030-01-01T10:00:00Z", "end": "2030-01-01T10:30:00Z"},
    {"id": "s1", "provider": "Dr Ade", "start": "2030-01-01T09:00:00Z", "end": "2030-01-01T09:30:00Z"},
    {"id": "s2", "provider": "Dr Ade", "start": "2030-01-01T09:30:00Z", "end": "2030-01-01T10:00:00Z"},
    {"id": "past", "provider": "Dr Ade", "start": "2020-01-01T09:00:00Z", "end": "2020-01-01T09:30:00Z"},
]


@pytest.fixture(autouse=True)
def reset():
    r = api.post("/reset", json={"slots": SLOTS})
    assert r.status_code == 200


def book(slot_id="s1", name="Ada Obi", email="ada@example.com"):
    return api.post("/bookings", json={"slot_id": slot_id, "name": name, "email": email})


def test_slots_earliest_first_and_available():
    r = api.get("/slots")
    assert r.status_code == 200
    slots = r.json()["slots"]
    assert [s["id"] for s in slots] == ["past", "s1", "s2", "s3"]
    assert all(s["available"] is True for s in slots)
    assert slots[1] == {**SLOTS[1], "available": True}


def test_slots_provider_filter_is_exact():
    assert [s["id"] for s in api.get("/slots", params={"provider": "Dr Ade"}).json()["slots"]] == ["past", "s1", "s2"]
    assert api.get("/slots", params={"provider": "dr ade"}).json()["slots"] == []


def test_book_then_look_up():
    r = book()
    assert r.status_code == 201
    b = r.json()
    assert isinstance(b["id"], str)
    assert {k: b[k] for k in ("slot_id", "name", "email", "status")} == {
        "slot_id": "s1", "name": "Ada Obi", "email": "ada@example.com", "status": "confirmed"}
    assert b["created_at"].endswith("Z")
    datetime.fromisoformat(b["created_at"])

    got = api.get(f"/bookings/{b['id']}")
    assert got.status_code == 200
    assert got.json() == b

    available = {s["id"]: s["available"] for s in api.get("/slots").json()["slots"]}
    assert available == {"past": True, "s1": False, "s2": True, "s3": True}


@pytest.mark.parametrize("bad_id", ["999999", "abc", "99999999999999999999", "-1", "9" * 5000])
def test_unknown_booking_is_404(bad_id):
    r = api.get(f"/bookings/{bad_id}")
    assert r.status_code == 404
    assert set(r.json()) == {"error"}


def test_reset_deletes_bookings():
    booking_id = book().json()["id"]
    api.post("/reset", json={"slots": SLOTS})
    assert api.get(f"/bookings/{booking_id}").status_code == 404
    assert all(s["available"] for s in api.get("/slots").json()["slots"])
