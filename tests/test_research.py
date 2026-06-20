"""Tests for research-grade features: multi-criterion / per-category slices + export."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_meta_lists_categories_and_criteria():
    meta = client.get("/api/meta").json()
    slugs = {c["slug"] for c in meta["categories"]}
    assert {"flowers", "proteins", "cells"} <= slugs
    crit_slugs = {c["slug"] for c in meta["criteria"]}
    assert {"overall", "realism", "morphology"} <= crit_slugs


def test_next_honors_criterion_and_category():
    data = client.get("/api/next?criterion=realism&category=flowers").json()
    assert data["criterion"]["slug"] == "realism"
    assert data["task"]["category"] == "Flowers"


def test_sliced_leaderboard_and_export():
    # Cast scoped votes (flowers × realism), including a tie.
    for i in range(14):
        nxt = client.get("/api/next?criterion=realism&category=flowers").json()
        winner = "tie" if i == 0 else ("a" if i % 2 else "b")
        client.post(
            "/api/vote?criterion=realism&category=flowers",
            json={"comparison_id": nxt["comparison_id"], "winner": winner},
        )

    assert client.post("/admin/recompute", data={"token": "test-token"}).status_code == 200

    # Sliced leaderboard returns a ranked board for that scope.
    board = client.get("/api/leaderboard?criterion=realism&category=flowers").json()
    assert board["criterion"] == "realism"
    assert board["category"] == "flowers"
    rows = board["rows"]
    assert len(rows) == 5
    scores = [r["bt_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    for r in rows:
        assert r["bt_lower"] <= r["bt_score"] <= r["bt_upper"]

    # The global/overall scope is independent and still present.
    g = client.get("/api/leaderboard?criterion=overall&category=all").json()
    assert len(g["rows"]) == 5

    # Export contains the scoped votes with full provenance (generators revealed).
    ex = client.get("/api/export.json").json()
    assert ex["n_votes"] >= 14
    rec = ex["votes"][0]
    for field in ("criterion", "category", "generator_a", "generator_b", "winner", "voted_at"):
        assert field in rec
    assert any(v["winner"] == "tie" for v in ex["votes"])


def test_sliced_leaderboard_html_renders():
    r = client.get("/leaderboard?criterion=realism&category=flowers")
    assert r.status_code == 200
    assert "Realism" in r.text or "realism" in r.text
