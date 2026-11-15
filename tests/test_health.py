import os

import httpx

BASE_URL = os.environ["BASE_URL"].rstrip("/")


def test_health():
    r = httpx.get(f"{BASE_URL}/health", timeout=60)  # 60s: a sleeping Render instance takes ~30s to wake
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_page_and_its_assets_are_served():
    # Locally this needs `cd web && bun run build` first; without web/dist, GET / is a 500.
    r = httpx.get(f"{BASE_URL}/", timeout=60)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    asset = r.text.split('src="')[1].split('"')[0]  # the bundled JS, e.g. /assets/index-abc.js
    assert httpx.get(f"{BASE_URL}{asset}", timeout=60).status_code == 200
