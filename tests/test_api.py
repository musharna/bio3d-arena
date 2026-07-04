"""End-to-end API tests via FastAPI TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Rating
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_next_returns_anonymized_pair():
    r = client.get("/api/next")
    assert r.status_code == 200
    data = r.json()
    assert "comparison_id" in data
    assert data["a"]["url"].startswith("/assets/")
    assert data["b"]["url"].startswith("/assets/")
    # Anonymized: no generator identity leaks in the payload.
    assert "generator" not in str(data).lower()


def test_vote_records_and_advances_and_moves_elo():
    first = client.get("/api/next").json()
    res = client.post("/api/vote", json={"comparison_id": first["comparison_id"], "winner": "a"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["next"] is not None  # a fresh comparison is returned

    # A decisive vote must move at least one generator's Elo off the 1000 default.
    with SessionLocal() as db:
        elos = [r.elo for r in db.query(Rating).all()]
    assert any(abs(e - 1000.0) > 1e-6 for e in elos)


def test_double_vote_rejected():
    first = client.get("/api/next").json()
    cid = first["comparison_id"]
    assert client.post("/api/vote", json={"comparison_id": cid, "winner": "a"}).status_code == 200
    dup = client.post("/api/vote", json={"comparison_id": cid, "winner": "b"})
    assert dup.status_code == 409


def test_leaderboard_and_recompute():
    # Cast a batch of votes to populate the win record. A session may vote each pairing only
    # once (the /api/vote 409 guard); once its fresh pairs are exhausted /api/next returns 404,
    # so stop rather than KeyError — the votes cast so far are enough to populate the board.
    for _ in range(30):
        nxt = client.get("/api/next").json()
        if "comparison_id" not in nxt:
            break
        client.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": "a"})

    # Admin recompute requires the token.
    assert client.post("/admin/recompute", data={"token": "wrong"}).status_code == 401
    ok = client.post("/admin/recompute", data={"token": "test-token"})
    assert ok.status_code == 200

    board = client.get("/api/leaderboard").json()
    rows = board["rows"]
    assert len(rows) >= 5  # five seeded generators + any benchmark generators
    assert rows[0]["rank"] == 1
    # Sorted by BT score descending; CI bounds present.
    scores = [r["bt_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    for r in rows:
        assert r["bt_lower"] <= r["bt_score"] <= r["bt_upper"]

    # Leaderboard HTML page renders.
    assert client.get("/leaderboard").status_code == 200


def test_admin_create_category():
    r = client.post(
        "/admin/category",
        data={"token": "test-token", "slug": "fungi-test", "name": "Fungi", "description": "x"},
        follow_redirects=False,
    )
    assert r.status_code == 303
