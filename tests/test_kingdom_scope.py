import re

from starlette.testclient import TestClient

from app.main import app


def _kbar_active_label(html: str) -> str | None:
    """Read back the active kingdom shown by the kbar dropdown pill (`b3d-kdrop-label`).

    The kbar renders on every page — including the kingdom-roadmap fallback shown
    for a not-yet-live kingdom (e.g. plants/fungi in a schema-only test DB) — so
    this is a reliable, content-independent signal that kingdom scoping reached
    the shell, unlike the page-body `_scope_pill.html` badge which only renders
    on the real (non-roadmap) page for that kingdom.
    """
    m = re.search(r'b3d-kdrop-label"\s*>([^<]+)</span', html)
    return m.group(1) if m else None


def test_kingdom_defaults_to_all():
    c = TestClient(app)
    r = c.get("/leaderboard")
    assert r.status_code == 200
    # scope pill shows ALL KINGDOMS by default
    assert b"ALL KINGDOMS" in r.content
    assert _kbar_active_label(r.text) == "All kingdoms"


def test_kingdom_query_param_sets_scope_and_cookie():
    c = TestClient(app)
    r = c.get("/leaderboard?kingdom=plants")
    assert r.status_code == 200
    assert _kbar_active_label(r.text) == "Plants"
    assert c.cookies.get("bio3d_kingdom") == "plants"


def test_kingdom_persists_from_cookie():
    c = TestClient(app)
    c.get("/leaderboard?kingdom=fungi")  # sets cookie
    r = c.get("/tasks")  # no param -> cookie wins
    assert _kbar_active_label(r.text) == "Fungi"


def test_bogus_kingdom_falls_back_to_all():
    c = TestClient(app)
    r = c.get("/leaderboard?kingdom=dragons")
    assert b"ALL KINGDOMS" in r.content
