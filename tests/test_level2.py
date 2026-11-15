import os

import httpx
import pytest

BASE_URL = os.environ["BASE_URL"].rstrip("/")
api = httpx.Client(base_url=BASE_URL, headers={"X-API-Key": os.environ.get("API_KEY", "")}, timeout=60)
nokey = httpx.Client(base_url=BASE_URL, timeout=60)

SLOTS = [
    {"id": "s1", "provider": "Dr Ade", "start": "2030-01-01T09:00:00Z", "end": "2030-01-01T09:30:00Z"},
    {"id": "s2", "provider": "Dr Bello", "start": "2030-01-01T10:00:00Z", "end": "2030-01-01T10:30:00Z"},
    {"id": "past", "provider": "Dr Ade", "start": "2020-01-01T09:00:00Z", "end": "2020-01-01T09:30:00Z"},
]
GOOD = {"slot_id": "s1", "name": "Ada Obi", "email": "ada@example.com"}


@pytest.fixture(autouse=True)
def reset():
    assert api.post("/reset", json={"slots": SLOTS}).status_code == 200


def assert_error(r, status):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and isinstance(body["error"], str) and body["error"]


# --- auth ---

KEYED = [("GET", "/slots"), ("POST", "/reset"), ("POST", "/bookings"), ("GET", "/bookings/1"),
         ("GET", "/nope"), ("GET", "/docs")]


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}], ids=["no_key", "wrong_key"])
@pytest.mark.parametrize("method,path", KEYED)
def test_auth_required(method, path, headers):
    assert_error(nokey.request(method, path, headers=headers, json={}), 401)


def test_auth_runs_before_body_parsing():
    r = nokey.post("/bookings", headers={"X-API-Key": "wrong", "Content-Type": "application/json"}, content="{not json")
    assert_error(r, 401)


# --- error shape ---

def test_unknown_route_is_404():
    assert_error(api.get("/nope"), 404)


def test_wrong_method_is_405_with_allow():
    r = api.put("/slots")
    assert_error(r, 405)
    assert "GET" in r.headers["allow"]


def test_unknown_asset_is_404():
    assert_error(api.get("/assets/nope.js"), 404)


def test_reset_bad_body_is_400():
    assert_error(api.post("/reset", json={}), 400)


# --- booking validation ---

@pytest.mark.parametrize("field", ["slot_id", "name", "email"])
@pytest.mark.parametrize("value", [None, "", "   "], ids=["missing", "empty", "blank"])
def test_required_fields(field, value):
    body = {**GOOD, field: value}
    if value is None:
        del body[field]
    r = api.post("/bookings", json=body)
    assert_error(r, 400)
    assert field in r.json()["error"]


@pytest.mark.parametrize("field", ["name", "email"])
def test_non_string_field_is_400(field):
    assert_error(api.post("/bookings", json={**GOOD, field: 123}), 400)


@pytest.mark.parametrize("email", ["ada", "ada@", "@x.com", "ada@x", "a b@x.com"])
def test_bad_email_is_400(email):
    assert_error(api.post("/bookings", json={**GOOD, "email": email}), 400)


@pytest.mark.parametrize("content", ["{not json", "[]"], ids=["malformed", "array"])
def test_bad_json_body_is_400(content):
    assert_error(api.post("/bookings", content=content, headers={"Content-Type": "application/json"}), 400)


# --- booking behaviour ---

def test_unknown_slot_is_404():
    assert_error(api.post("/bookings", json={**GOOD, "slot_id": "nope"}), 404)


def test_past_slot_is_400():
    assert_error(api.post("/bookings", json={**GOOD, "slot_id": "past"}), 400)


def test_double_booking_is_409():
    assert api.post("/bookings", json=GOOD).status_code == 201
    assert_error(api.post("/bookings", json={**GOOD, "email": "bo@example.com"}), 409)


def test_name_and_email_are_trimmed_and_email_lowercased():
    r = api.post("/bookings", json={**GOOD, "name": "  Ada Obi  ", "email": "  ADA@Example.COM "})
    assert r.status_code == 201
    b = r.json()
    assert (b["name"], b["email"]) == ("Ada Obi", "ada@example.com")
    assert api.get(f"/bookings/{b['id']}").json() == b
