"""Home landing page (/) + Arena moved to /arena — Task 9 of the design refresh.

`/` now renders the new marketing/landing page (`home.html`); the vote loop that used to
live at `/` moved to `/arena` with identical behavior (same `index`-era logic, renamed).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_home_page_200_and_hero_copy():
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert "arena for" in html  # hero H1: "The life-sciences arena for 3D generation"
    assert "votes" in html  # stats strip: "<N> votes cast"


def test_arena_page_200_and_vote_loop_markup():
    resp = client.get("/arena")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="slot-a"' in html
    assert 'id="kwise-grid"' in html
