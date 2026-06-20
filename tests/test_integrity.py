"""Tests for vote integrity / anti-abuse: rate limit, dedup, gold trust, gating, captcha."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import config, integrity, service
from app.database import SessionLocal
from app.main import app
from app.models import Comparison, Criterion, ModelOutput, Vote, VoterSession
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)


def _overall_id(db) -> int:
    return db.query(Criterion).filter_by(slug="overall").one().id


def _two_real_outputs(db) -> tuple[ModelOutput, ModelOutput]:
    # Two non-gold outputs from the same task.
    out = db.query(ModelOutput).filter_by(is_gold=False).first()
    mate = (
        db.query(ModelOutput)
        .filter(ModelOutput.task_id == out.task_id, ModelOutput.id != out.id, ~ModelOutput.is_gold)
        .first()
    )
    return out, mate


def test_rate_limit(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(config, "VOTE_RATE_LIMIT", 3)
    integrity.reset_rate_limits()
    codes = []
    for _ in range(6):
        nxt = client.get("/api/next").json()
        r = client.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": "a"})
        codes.append(r.status_code)
    assert 429 in codes  # the limiter kicked in within the burst
    integrity.reset_rate_limits()


def test_dedup_same_pair():
    with SessionLocal() as db:
        cid = _overall_id(db)
        a, b = _two_real_outputs(db)
        comp = Comparison(
            task_id=a.task_id,
            output_a_id=a.id,
            output_b_id=b.id,
            criterion_id=cid,
            session_id="dedup-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="a", session_id="dedup-sess"))
        db.commit()
        # Same unordered pair (either order) is a duplicate for this session.
        assert integrity.already_voted_pair(db, "dedup-sess", a.id, b.id, cid) is True
        assert integrity.already_voted_pair(db, "dedup-sess", b.id, a.id, cid) is True
        # A different session is unaffected.
        assert integrity.already_voted_pair(db, "other-sess", a.id, b.id, cid) is False


def test_gold_check_updates_trust(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(config, "GOLD_RATE", 1.0)  # every comparison is a gold check
    integrity.reset_rate_limits()

    def vote_gold(correct: bool):
        nxt = client.get("/api/next").json()
        cid = nxt["comparison_id"]
        with SessionLocal() as db:
            comp = db.get(Comparison, cid)
            assert comp.is_gold is True
            expected = comp.gold_expected
        winner = expected if correct else ("a" if expected == "b" else "b")
        client.post("/api/vote", json={"comparison_id": cid, "winner": winner})

    vote_gold(correct=True)
    sid = client.cookies.get("bio3d_session")
    with SessionLocal() as db:
        vs = db.get(VoterSession, sid)
        assert vs.gold_seen == 1 and vs.gold_passed == 1
        assert vs.trust == 1.0

    for _ in range(5):
        vote_gold(correct=False)
    with SessionLocal() as db:
        vs = db.get(VoterSession, sid)
        assert vs.gold_seen >= 6
        assert vs.trust < config.TRUST_THRESHOLD  # repeated failures sink trust


def test_low_trust_votes_excluded_from_ranking():
    with SessionLocal() as db:
        cid = _overall_id(db)
        a, b = _two_real_outputs(db)
        gen_a, gen_b = a.generator_id, b.generator_id

        def cast(session_id, winner, trust):
            db.add(VoterSession(session_id=session_id, trust=trust))
            comp = Comparison(
                task_id=a.task_id,
                output_a_id=a.id,
                output_b_id=b.id,
                criterion_id=cid,
                session_id=session_id,
            )
            db.add(comp)
            db.flush()
            db.add(Vote(comparison_id=comp.id, winner=winner, session_id=session_id))

        cast("hi-trust", "a", 1.0)  # A beats B  (counts)
        cast("lo-trust", "b", 0.1)  # B beats A  (should be excluded)
        db.commit()

        matches = service._matches_for_scope(db, cid, None)
    assert (gen_a, gen_b) in matches  # high-trust vote present
    assert (gen_b, gen_a) not in matches  # low-trust vote excluded


def test_captcha_gate(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(config, "REQUIRE_CAPTCHA", True)
    integrity.reset_rate_limits()
    nxt = client.get("/api/next").json()
    blocked = client.post("/api/vote", json={"comparison_id": nxt["comparison_id"], "winner": "a"})
    assert blocked.status_code == 403
    nxt2 = client.get("/api/next").json()
    ok = client.post(
        "/api/vote",
        json={"comparison_id": nxt2["comparison_id"], "winner": "a"},
        headers={"X-Captcha-Token": "any-non-empty"},
    )
    assert ok.status_code == 200


def test_methodology_page_renders():
    r = TestClient(app).get("/methodology")
    assert r.status_code == 200
    assert "integrity" in r.text.lower()
    assert "Gold attention checks" in r.text
