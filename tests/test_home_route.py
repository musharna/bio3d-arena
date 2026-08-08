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
    # The strip led with "<N> votes cast" until 2026-08-08. That number was true and misleading:
    # 340 votes from 18 sessions EVER, 93.8% of them two internal ones, read by a visitor as
    # community participation. The hero now leads with the corpus, which is simply what exists.
    # Asserting the NEW copy rather than merely deleting the old assertion.
    assert "3D models generated" in html  # stats strip: "<N> 3D models generated"
    assert "votes cast" not in html  # the overstated headline claim must not creep back


def test_arena_page_200_and_vote_loop_markup():
    resp = client.get("/arena")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="slot-a"' in html
    assert 'id="kwise-grid"' in html
