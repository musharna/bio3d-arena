# tests/test_mobile_nav.py
from fastapi.testclient import TestClient
from app.main import app


def test_nav_burger_markup_present():
    html = TestClient(app).get("/").text
    assert 'id="nav-toggle"' in html
    assert 'class="nav-burger"' in html
    # the nav links still exist (collapsed, not removed)
    assert 'href="/leaderboard"' in html
