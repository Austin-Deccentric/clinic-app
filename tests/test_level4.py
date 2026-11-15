import os
import threading
import uuid
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


def body(slot_id="s1", email="ada@example.com"):
    return {"slot_id": slot_id, "name": "Ada Obi", "email": email}


def book(key, json=None, client=api):
    return client.post("/bookings", json=json or body(), headers={"Idempotency-Key": key})


def key():
    return str(uuid.uuid4())


def assert_error(r, status):
    assert r.status_code == status, r.text
    b = r.json()
    assert set(b) == {"error"} and isinstance(b["error"], str) and b["error"]


def available(slot_id):
    return next(s["available"] for s in api.get("/slots").json()["slots"] if s["id"] == slot_id)


# --- idempotency (level 4a) ---

def test_same_key_same_body_replays_the_booking():
    k = key()
    first = book(k)
    assert first.status_code == 201, first.text
    again = book(k)
    assert again.status_code == 201 and again.json() == first.json()  # same id, not a 409


def test_same_key_different_body_is_422():
    k = key()
    assert book(k).status_code == 201
    assert_error(book(k, body(slot_id="s2")), 422)
    assert available("s2")  # the mismatched request created nothing


def test_key_order_in_body_does_not_matter():
    k = key()
    first = book(k)
    again = book(k, {"email": "ada@example.com", "name": "Ada Obi", "slot_id": "s1"})
    assert again.status_code == 201 and again.json()["id"] == first.json()["id"]


def test_error_responses_are_replayed_too():
    blocker = api.post("/bookings", json=body(email="bo@example.com"))
    assert blocker.status_code == 201
    k = key()
    assert_error(book(k), 409)
    # Free the slot: a fresh attempt would now succeed, but the key replays its stored 409.
    assert api.delete(f"/bookings/{blocker.json()['id']}").status_code == 200
    assert_error(book(k), 409)
    assert available("s1")


def test_different_keys_are_independent():
    assert book(key()).status_code == 201
    assert_error(book(key()), 409)


def test_no_key_behaves_as_before():
    assert api.post("/bookings", json=body()).status_code == 201
    assert_error(api.post("/bookings", json=body()), 409)


def test_reset_clears_keys():
    k = key()
    first = book(k).json()
    api.post("/reset", json={"slots": SLOTS})
    again = book(k)
    assert again.status_code == 201 and again.json()["id"] != first["id"]


def test_overlong_key_is_400():
    assert_error(book("k" * 256), 400)


def test_concurrent_same_key_one_booking_identical_responses():
    k = key()
    barrier = threading.Barrier(10)

    def run(_):
        with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=60) as c:
            barrier.wait()
            return book(k, client=c)

    with ThreadPoolExecutor(10) as pool:
        rs = list(pool.map(run, range(10)))
    assert {r.status_code for r in rs} == {201}, [r.text for r in rs]
    assert len({r.text for r in rs}) == 1  # one booking, byte-identical responses
