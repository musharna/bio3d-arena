"""Tests for statistical rigor: pairwise significance + bias audit."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import ranking
from app.main import app
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_significance_matrix_recovers_order_and_probabilities():
    players = [1, 2, 3]
    matches = [(1, 2)] * 9 + [(2, 1)] + [(2, 3)] * 9 + [(3, 2)] + [(1, 3)] * 10
    sig = ranking.significance_matrix(players, matches, bootstrap=200)
    assert sig.order == [1, 2, 3]
    # Dominant player beats the weakest in essentially all resamples.
    assert sig.p_better[(1, 3)] > 0.9
    assert sig.p_better[(3, 1)] < 0.1
    # Complementary probabilities (no ties in continuous scores).
    assert abs(sig.p_better[(1, 2)] + sig.p_better[(2, 1)] - 1.0) < 1e-9
    # Last-ranked player has no "next".
    assert sig.p_beats_next[sig.order[-1]] is None


def _cast_biased_votes(n: int = 40):
    for i in range(n):
        nxt = client.get("/api/next").json()
        # A session can vote each pairing at most once (the /api/vote 409 guard); once its
        # fresh pairs are exhausted /api/next returns 404, so stop rather than KeyError.
        if "comparison_id" not in nxt:
            break
        winner = "a" if i % 3 else "b"  # mostly A, some B
        client.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": winner})


def test_api_significance_shape():
    _cast_biased_votes()
    sig = client.get("/api/significance?criterion=overall&category=all").json()
    assert sig["status"] == "ok"
    n = len(sig["labels"])
    assert n >= 5  # five seeded generators + any benchmark generators
    assert len(sig["matrix"]) == n and all(len(r) == n for r in sig["matrix"])
    # ranked sorted by score desc; probabilities in range.
    scores = [r["score"] for r in sig["ranked"]]
    assert scores == sorted(scores, reverse=True)
    for r in sig["ranked"]:
        assert r["p_beats_next"] is None or 0.0 <= r["p_beats_next"] <= 1.0
    # Diagonal is self (1.0); off-diagonal rows/cols are complementary-ish.
    for i in range(n):
        assert sig["matrix"][i][i] == 1.0


def test_api_bias_audit():
    bias = client.get("/api/bias").json()
    assert bias["decisive"] > 0
    assert 0.0 <= bias["left_win_rate"] <= 1.0
    assert "cross_format_comparisons" in bias
    assert "format_win_rate" in bias


def test_significance_page_renders():
    r = client.get("/significance?criterion=overall&category=all")
    assert r.status_code == 200
    assert "significance" in r.text.lower()
    assert "Bias audit" in r.text


def test_significance_rated_only_hides_never_voted_generators():
    """compute_significance defaults to rated-only: a generator that never appears in a
    comparison has no significance signal and floods the forest/matrix, so it's excluded
    (and counted in n_unrated) unless rated_only=False."""
    from app import service
    from app.database import SessionLocal

    _cast_biased_votes()
    with SessionLocal() as db:
        rated = service.compute_significance(db, "overall")
        every = service.compute_significance(db, "overall", rated_only=False)

    assert rated["status"] == "ok" and every["status"] == "ok"
    # n_unrated / n_total are properties of the data — identical regardless of rated_only.
    assert rated["n_unrated"] == every["n_unrated"]
    assert rated["n_total"] == every["n_total"]
    # rated_only shows exactly (total - unrated); showing all shows every generator.
    assert len(rated["labels"]) == rated["n_total"] - rated["n_unrated"]
    assert len(every["labels"]) == every["n_total"]
    assert len(rated["labels"]) <= len(every["labels"])
    if rated["n_unrated"] > 0:  # only a strict inequality when something was actually hidden
        assert len(every["labels"]) > len(rated["labels"])
