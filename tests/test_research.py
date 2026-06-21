"""Tests for research-grade features: multi-criterion / per-category slices + export."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import integrity
from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)
    # Reset the process-wide in-memory rate limiter so votes cast by earlier test
    # modules don't throttle this module's vote loops (pre-existing cross-file flake:
    # the shared TestClient identity accumulates counts across files within the 60s
    # window, intermittently 429-ing this test's 14 votes below the >=6 floor).
    integrity.reset_rate_limits()


def test_meta_lists_categories_and_criteria():
    meta = client.get("/api/meta").json()
    slugs = {c["slug"] for c in meta["categories"]}
    assert {"plants", "fungi", "animals", "microbes"} <= slugs
    crit_slugs = {c["slug"] for c in meta["criteria"]}
    assert {"overall", "realism", "morphology"} <= crit_slugs


def test_meta_marks_empty_domains_coming_soon():
    # Tree-of-life taxonomy: Plants is the active flagship (has tasks); Fungi/Animals/
    # Microbes are placeholders (no tasks) flagged coming_soon so the UI can disable them.
    meta = client.get("/api/meta").json()
    by_slug = {c["slug"]: c for c in meta["categories"]}
    assert by_slug["plants"]["coming_soon"] is False
    for slug in ("fungi", "animals", "microbes"):
        assert by_slug[slug]["coming_soon"] is True


def test_next_honors_criterion_and_category():
    data = client.get("/api/next?criterion=realism&category=plants").json()
    assert data["criterion"]["slug"] == "realism"
    assert data["task"]["category"] == "Plants"


def test_sliced_leaderboard_and_export():
    # Matchmaking picks pairs with the global RNG (random.choice/shuffle); seed it so
    # the sequence of served pairs is deterministic. Without this, per-session dedup
    # rejects repeat pairs and the count of DISTINCT landed votes varies run-to-run,
    # intermittently dropping below the >=6 floor (root cause of the prior flake).
    import random

    random.seed(20260620)
    # Cast scoped votes (flowers × realism), including a tie.
    for i in range(14):
        nxt = client.get("/api/next?criterion=realism&category=plants").json()
        winner = "tie" if i == 0 else ("a" if i % 2 else "b")
        client.post(
            "/api/vote?criterion=realism&category=plants",
            json={"comparison_id": nxt["comparison_id"], "winner": winner},
        )

    assert client.post("/admin/recompute", data={"token": "test-token"}).status_code == 200

    # Sliced leaderboard returns a ranked board for that scope.
    board = client.get("/api/leaderboard?criterion=realism&category=plants").json()
    assert board["criterion"] == "realism"
    assert board["category"] == "plants"
    rows = board["rows"]
    assert len(rows) == 5
    scores = [r["bt_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    for r in rows:
        assert r["bt_lower"] <= r["bt_score"] <= r["bt_upper"]

    # The global/overall scope is independent and still present.
    g = client.get("/api/leaderboard?criterion=overall&category=all").json()
    assert len(g["rows"]) >= 5  # global scope includes benchmark generators after recompute

    # Export contains the scoped votes with full provenance (generators revealed).
    # (Per-session dedup caps distinct flowers×realism pairs, so < 14 may land.)
    ex = client.get("/api/export.json").json()
    assert ex["n_votes"] >= 6
    rec = ex["votes"][0]
    for field in ("criterion", "category", "generator_a", "generator_b", "winner", "voted_at"):
        assert field in rec
    assert any(v["winner"] == "tie" for v in ex["votes"])


def test_sliced_leaderboard_html_renders():
    r = client.get("/leaderboard?criterion=realism&category=plants")
    assert r.status_code == 200
    assert "Realism" in r.text or "realism" in r.text
