import os
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

BASE_URL = os.environ["BASE_URL"].rstrip("/")
HEADERS = {"X-API-Key": os.environ.get("API_KEY", "")}
api = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=60)

SLOTS = [
    {"id": "s1", "provider": "Dr Ade", "start": "2030-01-01T09:00:00Z", "end": "2030-01-01T09:30:00Z"},
    {"id": "s2", "provider": "Dr Bello", "start": "2030-01-01T10:00:00Z", "end": "2030-01-01T10:30:00Z"},
]


@pytest.fixture(autouse=True)
def reset():
    assert api.post("/reset", json={"slots": SLOTS}).status_code == 200


def book(slot_id="s1", email="ada@example.com"):
    return api.post("/bookings", json={"slot_id": slot_id, "name": "Ada Obi", "email": email})


def available(slot_id):
    return next(s["available"] for s in api.get("/slots").json()["slots"] if s["id"] == slot_id)


def assert_error(r, status):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and isinstance(body["error"], str) and body["error"]


def all_at_once(calls):
    """Run the calls on separate threads and connections, released together by a barrier."""
    barrier = threading.Barrier(len(calls))

    def run(call):
        with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=60) as c:
            barrier.wait()
            return call(c)

    with ThreadPoolExecutor(len(calls)) as pool:
        return list(pool.map(run, calls))


# --- no double booking ---

def test_second_booking_of_a_slot_is_409():
    assert book().status_code == 201
    assert_error(book(email="bo@example.com"), 409)


def test_concurrent_bookings_of_one_slot_one_wins():
    # A different email each, so the per-email rate limit never turns these into 429s.
    calls = [lambda c, i=i: c.post("/bookings", json={"slot_id": "s1", "name": "N", "email": f"p{i}@example.com"})
             for i in range(20)]
    rs = all_at_once(calls)
    assert sorted(r.status_code for r in rs) == [201] + [409] * 19, [r.text for r in rs]
    for r in rs:
        if r.status_code == 409:
            assert_error(r, 409)
    assert available("s1") is False


# --- cancel ---

def test_cancel_returns_the_cancelled_booking_and_frees_the_slot():
    b = book().json()
    r = api.delete(f"/bookings/{b['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == {**b, "status": "cancelled"}
    assert api.get(f"/bookings/{b['id']}").json()["status"] == "cancelled"
    assert available("s1") is True
    assert available("s2") is True


def test_cancel_twice_is_409():
    b = book().json()
    assert api.delete(f"/bookings/{b['id']}").status_code == 200
    assert_error(api.delete(f"/bookings/{b['id']}"), 409)


@pytest.mark.parametrize("bad", ["0", "999999999", "2147483647", "2147483648", "abc", "1.5", "-1", "99999999999999999999", "%C2%B2"])
def test_cancel_unknown_or_malformed_id_is_404(bad):
    assert_error(api.delete(f"/bookings/{bad}"), 404)


def test_cancel_needs_the_key():
    b = book().json()
    assert_error(httpx.delete(f"{BASE_URL}/bookings/{b['id']}"), 401)
    assert api.get(f"/bookings/{b['id']}").json()["status"] == "confirmed"


def test_freed_slot_can_be_rebooked():
    first = book().json()
    assert api.delete(f"/bookings/{first['id']}").status_code == 200
    r = book(email="bo@example.com")
    assert r.status_code == 201, r.text
    second = r.json()
    assert second["id"] != first["id"] and second["status"] == "confirmed"
    assert available("s1") is False
    assert api.get(f"/bookings/{first['id']}").json()["status"] == "cancelled"


def test_concurrent_cancels_one_wins():
    b = book().json()
    rs = all_at_once([lambda c: c.delete(f"/bookings/{b['id']}")] * 20)
    assert sorted(r.status_code for r in rs) == [200] + [409] * 19, [r.text for r in rs]
