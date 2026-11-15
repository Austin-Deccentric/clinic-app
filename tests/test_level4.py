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
] + [
    {"id": f"s{i}", "provider": "Dr Ade", "start": f"2030-01-01T{8 + i:02}:00:00Z", "end": f"2030-01-01T{8 + i:02}:30:00Z"}
    for i in range(3, 7)
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


# --- pagination (level 4b) ---
# At most 5 bookings per email per test: Phase 7 rate-limits 5 POSTs per email per minute.

def page(email, **params):
    return api.get("/bookings", params={"email": email, **params})


def make(n, email, start=1):
    ids = []
    for i in range(start, start + n):
        r = api.post("/bookings", json=body(f"s{i}", email))
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def walk(email, **params):
    pages, cursor = [], None
    while True:
        r = page(email, **params, **({"cursor": cursor} if cursor else {}))
        assert r.status_code == 200, r.text
        pages.append(r.json())
        cursor = r.json()["next_cursor"]
        if cursor is None:
            return pages
        assert isinstance(cursor, str)


def test_walk_returns_every_booking_once_oldest_first():
    ids = make(2, "walker@example.com")
    other = make(1, "other@example.com", start=3)
    ids += make(3, "walker@example.com", start=4)
    pages = walk("Walker@Example.COM", limit=2)
    assert [len(p["bookings"]) for p in pages] == [2, 2, 1]
    seen = [b["id"] for p in pages for b in p["bookings"]]
    assert seen == ids and other[0] not in seen


def test_exactly_full_last_page_has_null_cursor():
    make(4, "full@example.com")
    assert [len(p["bookings"]) for p in walk("full@example.com", limit=2)] == [2, 2]
    r = page("full@example.com", limit=4).json()
    assert len(r["bookings"]) == 4 and r["next_cursor"] is None


def test_items_match_get_booking_and_envelope_is_exact():
    make(3, "shape@example.com")
    r = page("shape@example.com", limit=2).json()
    assert set(r) == {"bookings", "next_cursor"} and isinstance(r["next_cursor"], str)
    for b in r["bookings"]:
        assert b == api.get(f"/bookings/{b['id']}").json()


def test_cancelled_bookings_are_listed():
    first, second = make(2, "cx@example.com")
    assert api.delete(f"/bookings/{first}").status_code == 200
    r = page("cx@example.com").json()["bookings"]
    assert [(b["id"], b["status"]) for b in r] == [(first, "cancelled"), (second, "confirmed")]


def test_unknown_email_and_cursor_past_the_end_are_empty():
    assert page("nobody@example.com").json() == {"bookings": [], "next_cursor": None}
    make(1, "end@example.com")
    r = page("end@example.com", cursor="2147483647")
    assert r.status_code == 200 and r.json() == {"bookings": [], "next_cursor": None}


def test_email_is_trimmed_and_case_insensitive():
    ids = make(2, "walker@example.com")
    assert [b["id"] for b in page(" Walker@Example.com ").json()["bookings"]] == ids


@pytest.mark.parametrize("email", [None, "", "   "])
def test_missing_or_blank_email_is_400(email):
    r = api.get("/bookings") if email is None else page(email)
    assert_error(r, 400)


@pytest.mark.parametrize("limit", ["0", "51", "-1", "abc", "1.5", ""])
def test_bad_limit_is_400(limit):
    assert_error(page("ada@example.com", limit=limit), 400)


@pytest.mark.parametrize("limit", ["1", "50"])
def test_limit_bounds_are_accepted(limit):
    assert page("ada@example.com", limit=limit).status_code == 200


def test_default_limit_covers_five():
    make(5, "dflt@example.com")
    r = page("dflt@example.com").json()
    assert len(r["bookings"]) == 5 and r["next_cursor"] is None


@pytest.mark.parametrize("cursor", ["abc", "-1", "1.5", "", "99999999999"])
def test_bad_cursor_is_400(cursor):
    assert_error(page("ada@example.com", cursor=cursor), 400)


def test_list_needs_api_key():
    assert_error(httpx.get(f"{BASE_URL}/bookings", params={"email": "ada@example.com"}), 401)
