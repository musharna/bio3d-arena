"""End-to-end API tests via FastAPI TestClient."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import config
from app.database import SessionLocal
from app.main import app
from app.models import Comparison, KBallot, ModelOutput, Rating
from app.seed import seed_all

client = TestClient(app)


def setup_module(_module):
    seed_all(force=True)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_next_returns_anonymized_pair():
    # ?set=pair is explicit because the DEFAULT ballot is k-wise wherever a task can fill a quad.
    # These tests are about the 2-up payload's own contract (opaque URLs, no identity leak), so
    # they must pin the shape rather than take whatever the corpus happens to allow.
    r = client.get("/api/next?set=pair")
    assert r.status_code == 200
    data = r.json()
    assert "comparison_id" in data
    # URLs are opaque + output-scoped (/media/o/{id}.{ext}) so devtools can't read the
    # asset_path and de-anonymize the gold decoy — see test_opaque_asset_urls.py.
    assert data["a"]["url"].startswith("/media/o/")
    assert data["b"]["url"].startswith("/media/o/")
    # Anonymized: no generator identity leaks in the payload.
    assert "generator" not in str(data).lower()


def test_vote_records_and_advances_and_moves_elo():
    first = client.get("/api/next?set=pair").json()
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
    first = client.get("/api/next?set=pair").json()
    cid = first["comparison_id"]
    assert client.post("/api/vote", json={"comparison_id": cid, "winner": "a"}).status_code == 200
    dup = client.post("/api/vote", json={"comparison_id": cid, "winner": "b"})
    assert dup.status_code == 409


def test_leaderboard_and_recompute():
    # Cast a batch of votes to populate the win record. A session may vote each pairing only
    # once (the /api/vote 409 guard); once its fresh pairs are exhausted /api/next returns 404,
    # so stop rather than KeyError — the votes cast so far are enough to populate the board.
    for _ in range(30):
        nxt = client.get("/api/next?set=pair").json()
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


def test_vote_reveal_present_for_real_comparison(monkeypatch):
    """Feature C: a non-gold vote's response carries a `reveal` with both real names + the
    winner, so the client can show the post-vote fanfare. Force GOLD_RATE=0 so this is
    deterministic rather than depending on the 10% default gold-injection chance. Uses a
    fresh client/session — the shared module-level `client` has already voted through most
    of the seeded pairing pool in earlier tests, so /api/next could 404 here otherwise."""
    monkeypatch.setattr(config, "GOLD_RATE", 0.0)
    fresh = TestClient(app)
    # ?set=pair: the a/b reveal shape under test belongs to the 2-up ballot (k-wise reveals an
    # `outputs` list via /api/kvote instead), and the default ballot is now k-wise.
    nxt = fresh.get("/api/next?set=pair").json()
    res = fresh.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": "a"})
    assert res.status_code == 200
    reveal = res.json()["reveal"]
    assert reveal is not None
    assert isinstance(reveal["a"]["name"], str) and reveal["a"]["name"]
    assert isinstance(reveal["b"]["name"], str) and reveal["b"]["name"]
    assert reveal["winner"] == "a"
    # Reveal is name-only: the "#N/M · provisional" standing chip was removed, so the payload
    # must not re-introduce rank/of/provisional on either side.
    assert set(reveal["a"]) == {"name"}
    assert set(reveal["b"]) == {"name"}


def test_vote_reveal_omitted_for_gold(monkeypatch):
    """Gold comparisons are an attention-check decoy — revealing the winner/names would leak
    the answer, so `reveal` must be omitted (null) even though the vote itself still records.
    Fresh client for the same pool-exhaustion reason as the test above."""
    monkeypatch.setattr(config, "GOLD_RATE", 1.0)
    fresh = TestClient(app)
    nxt = fresh.get("/api/next").json()
    with SessionLocal() as db:
        comp = db.get(Comparison, nxt["comparison_id"])
        assert comp.is_gold is True
        expected = comp.gold_expected
    res = fresh.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": expected})
    assert res.status_code == 200
    assert res.json().get("reveal") is None


def test_kvote_reveal_labels_every_output_and_the_pick():
    """Feature C for K-wise: the /api/kvote response carries a `reveal` with a real name for
    every output shown in the ballot plus which one was picked, so the grid can label each
    card. Builds a ballot directly (mirrors tests/test_kvote_endpoint.py) over real seeded
    outputs so generator_display_names has real rows to resolve."""
    with SessionLocal() as db:
        outs = db.query(ModelOutput).filter_by(is_gold=False).limit(4).all()
        assert len(outs) == 4
        out_ids = [o.id for o in outs]
        task_id = outs[0].task_id
        crit_id = _overall_criterion_id(db)
        ballot = KBallot(
            task_id=task_id,
            criterion_id=crit_id,
            session_id="reveal-kvote-test",
            output_ids_json=json.dumps(out_ids),
        )
        db.add(ballot)
        db.commit()
        ballot_id = ballot.id
        best_id = out_ids[0]

    res = client.post("/api/kvote", json={"ballot_id": ballot_id, "best_output_id": best_id})
    assert res.status_code == 200
    reveal = res.json()["reveal"]
    assert reveal is not None
    assert reveal["best_output_id"] == best_id
    seen_ids = {o["output_id"] for o in reveal["outputs"]}
    assert seen_ids == set(out_ids)
    assert all(isinstance(o["name"], str) and o["name"] for o in reveal["outputs"])
    # Name-only reveal: no rank/provisional standing chip re-introduced per output.
    assert all(set(o) == {"output_id", "name"} for o in reveal["outputs"])


def _overall_criterion_id(db) -> int:
    from app.models import Criterion

    return db.query(Criterion).filter_by(slug="overall").one().id
