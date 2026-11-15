import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

BASE_URL = os.environ["BASE_URL"].rstrip("/")
HEADERS = {"X-API-Key": os.environ.get("API_KEY", "")}
api = httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=60)

SLOTS = [
    {"id": f"s{i}", "provider": "Dr Ade", "start": f"2030-01-01T{8 + i:02}:00:00Z", "end": f"2030-01-01T{8 + i:02}:30:00Z"}
    for i in range(1, 8)
]


@pytest.fixture(autouse=True)
def reset():
    assert api.post("/reset", json={"slots": SLOTS}).status_code == 200


def assert_error(r, status):
    assert r.status_code == status, r.text
    b = r.json()
    assert set(b) == {"error"} and isinstance(b["error"], str) and b["error"]


def attempt(slot_id="s1", email="ada@example.com", key=None, client=api):
    return client.post("/bookings", json={"slot_id": slot_id, "name": "Ada Obi", "email": email},
                       headers={"Idempotency-Key": key} if key else {})


def assert_limited(r):
    assert_error(r, 429)
    assert 1 <= int(r.headers["Retry-After"]) <= 60


def fill(email="ada@example.com"):
    assert attempt(email=email).status_code == 201
    for _ in range(4):
        assert_error(attempt(email=email), 409)


def test_failed_attempts_count_and_the_sixth_is_429():
    fill()
    assert_limited(attempt())


def test_other_emails_are_not_affected():
    fill()
    assert_limited(attempt())
    assert attempt("s2", "bob@example.com").status_code == 201


def test_one_window_per_trimmed_lowercased_email():
    forms = ["ADA@example.com", " ada@example.com ", "ada@example.com"]
    for i in range(5):
        assert attempt(f"s{i + 1}", forms[i % 3]).status_code == 201
    for email in forms:
        assert_limited(attempt("s6", email))


def test_replays_do_not_count():
    k = str(uuid.uuid4())
    first = attempt(key=k)
    assert first.status_code == 201
    for _ in range(5):
        again = attempt(key=k)
        assert again.status_code == 201 and again.json() == first.json()
    for i in range(2, 6):
        assert attempt(f"s{i}").status_code != 429
    assert_limited(attempt("s6"))


def test_limited_request_leaves_no_key_claim():
    fill()
    k = str(uuid.uuid4())
    assert_limited(attempt("s2", key=k))
    assert_limited(attempt("s2", key=k))  # not a 500 from a half-written claim, not a replay


def test_reset_clears_the_window():
    fill()
    assert_limited(attempt())
    assert api.post("/reset", json={"slots": SLOTS}).status_code == 200
    assert attempt().status_code == 201


def test_concurrent_attempts_exactly_five_get_through():
    def run(i):
        with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=60) as c:
            return attempt(f"s{i % 7 + 1}", client=c)

    with ThreadPoolExecutor(10) as pool:
        rs = list(pool.map(run, range(10)))
    codes = [r.status_code for r in rs]
    assert codes.count(429) == 5 and len(codes) == 10, [r.text for r in rs]


@pytest.mark.skipif(not os.environ.get("SLOW"), reason="about 61s; run with SLOW=1")
def test_slow_window_expires_and_429s_are_not_counted():
    for i in range(5):
        assert attempt(f"s{i + 1}").status_code != 429
    time.sleep(30)
    limited = [attempt("s6") for _ in range(5)]
    for r in limited:
        assert_limited(r)
    # The first five have aged out by now; the 429s would still be in the window if they counted.
    time.sleep(int(limited[-1].headers["Retry-After"]) + 0.5)
    assert attempt("s6").status_code != 429
