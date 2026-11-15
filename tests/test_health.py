import os

import httpx

BASE_URL = os.environ["BASE_URL"].rstrip("/")


def test_health():
    r = httpx.get(f"{BASE_URL}/health", timeout=60)  # 60s: a sleeping Render instance takes ~30s to wake
    assert r.status_code == 200
    assert r.json() == {"ok": True}
