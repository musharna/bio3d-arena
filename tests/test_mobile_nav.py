# tests/test_mobile_nav.py
from fastapi.testclient import TestClient
from app.main import app


def test_nav_burger_markup_present():
    html = TestClient(app).get("/").text
    # v2 shell: JS-driven off-canvas drawer (checkbox-hack nav removed)
    assert 'id="b3d-burger"' in html
    assert 'class="b3d-sidebar"' in html or "b3d-sidebar" in html
    assert 'class="b3d-scrim"' in html
    # the nav links still exist (collapsed, not removed)
    assert 'href="/leaderboard"' in html
