"""The page authenticates with a per-browser cookie instead of the API key, and only reaches its own bookings."""
import os

import httpx
import pytest

BASE_URL = os.environ["BASE_URL"].rstrip("/")
api = httpx.Client(base_url=BASE_URL, headers={"X-API-Key": os.environ.get("API_KEY", "")}, timeout=60)

SLOTS = [
    {"id": f"p{i}", "provider": "Dr Ade", "start": f"2030-03-01T{9 + i:02}:00:00Z", "end": f"2030-03-01T{9 + i:02}:30:00Z"}
    for i in range(1, 5)
]


@pytest.fixture(autouse=True)
def reset():
    assert api.post("/reset", json={"slots": SLOTS}).status_code == 200


def browser():
    """A cookie-only client, the way the page calls the API (no key)."""
    r = httpx.get(f"{BASE_URL}/")
    assert r.status_code == 200
    sid = r.headers["set-cookie"].split(";")[0].removeprefix("sid=")
    return httpx.Client(base_url=BASE_URL, headers={"Cookie": f"sid={sid}"}, timeout=60)


def book(client, slot, email="pat@example.com"):
    r = client.post("/bookings", json={"slot_id": slot, "name": "Pat", "email": email})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_page_gets_a_locked_down_cookie():
    c = httpx.get(f"{BASE_URL}/").headers["set-cookie"].lower()
    assert c.startswith("sid=") and "httponly" in c and "samesite=strict" in c and "secure" in c


def test_page_bundle_has_no_api_key():
    page = httpx.get(f"{BASE_URL}/").text
    for js in [p.split('"')[0] for p in page.split('src="')[1:] if p.startswith("/assets/")]:
        assert os.environ["API_KEY"] not in httpx.get(f"{BASE_URL}{js}").text


def test_cookie_can_list_slots_and_book():
    b = browser()
    assert b.get("/slots").status_code == 200
    book(b, "p1")


@pytest.mark.parametrize("method,path", [("POST", "/reset"), ("GET", "/nope")])
def test_cookie_cannot_reach_other_routes(method, path):
    r = browser().request(method, path, json={"slots": []})
    assert r.status_code == 401 and set(r.json()) == {"error"}


@pytest.mark.parametrize("sid", ["", "short", "x" * 43 + "!"])
def test_malformed_cookie_is_401(sid):
    r = httpx.get(f"{BASE_URL}/slots", headers={"Cookie": f"sid={sid}"})
    assert r.status_code == 401


def test_browser_lists_only_its_own_bookings():
    mine, theirs = browser(), browser()
    own = book(mine, "p1")
    book(theirs, "p2")  # same email, other browser
    keyed = book(api, "p3")  # same email, key caller
    assert [b["id"] for b in mine.get("/bookings", params={"email": "pat@example.com"}).json()["bookings"]] == [own]
    # The key still sees everything for the email, as the contract says.
    assert len(api.get("/bookings", params={"email": "pat@example.com"}).json()["bookings"]) == 3
    assert mine.get(f"/bookings/{keyed}").status_code == 404
    assert mine.get(f"/bookings/{own}").status_code == 200


def test_browser_cannot_cancel_someone_elses_booking():
    victim = book(api, "p1", "victim@example.com")
    r = browser().delete(f"/bookings/{victim}")
    assert r.status_code == 404 and set(r.json()) == {"error"}
    assert api.get(f"/bookings/{victim}").json()["status"] == "confirmed"


def test_browser_cancels_its_own_booking():
    b = browser()
    own = book(b, "p1")
    assert b.delete(f"/bookings/{own}").json()["status"] == "cancelled"
    assert b.delete(f"/bookings/{own}").status_code == 409
