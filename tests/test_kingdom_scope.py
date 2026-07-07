from starlette.testclient import TestClient

from app.main import app


def test_kingdom_defaults_to_all():
    c = TestClient(app)
    r = c.get("/leaderboard")
    assert r.status_code == 200
    # scope pill shows ALL KINGDOMS by default
    assert b"ALL KINGDOMS" in r.content


def test_kingdom_query_param_sets_scope_and_cookie():
    c = TestClient(app)
    r = c.get("/leaderboard?kingdom=plants")
    assert r.status_code == 200
    assert b"PLANTS" in r.content
    assert c.cookies.get("bio3d_kingdom") == "plants"


def test_kingdom_persists_from_cookie():
    c = TestClient(app)
    c.get("/leaderboard?kingdom=fungi")  # sets cookie
    r = c.get("/tasks")  # no param -> cookie wins
    assert b"FUNGI" in r.content


def test_bogus_kingdom_falls_back_to_all():
    c = TestClient(app)
    r = c.get("/leaderboard?kingdom=dragons")
    assert b"ALL KINGDOMS" in r.content
