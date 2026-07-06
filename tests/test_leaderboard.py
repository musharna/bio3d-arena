"""Tests for the leaderboard credibility surface: CI-grouped rank + CI bar."""

from __future__ import annotations

from app.main import _leaderboard_rows
from app.database import SessionLocal
from app.seed import seed_all


def setup_module(_module):
    seed_all(force=True)


def test_leaderboard_rows_have_ci_bar_geometry():
    with SessionLocal() as db:
        rows = _leaderboard_rows(db, "overall", None)
    assert rows, "expected seeded generators on the global overall board"
    for r in rows:
        assert 0.0 <= r["ci_left"] <= 100.0
        assert 0.0 <= r["ci_width"] <= 100.0
        assert "rank" in r


def test_ci_grouped_rank_matches_formula():
    # Directly exercise the rank rule against the serialized rows.
    from app.ranking import rank_by_ci

    with SessionLocal() as db:
        rows = _leaderboard_rows(db, "overall", None)
    expected = rank_by_ci([(r["bt_lower"], r["bt_upper"]) for r in rows])
    assert [r["rank"] for r in rows] == expected
    # Rank 1 always exists; ranks are non-decreasing down the (point-sorted) board.
    assert rows[0]["rank"] == 1
    assert all(rows[i]["rank"] <= rows[i + 1]["rank"] for i in range(len(rows) - 1))


def test_leaderboard_page_renders_rank_ub_and_ci_bar():
    from fastapi.testclient import TestClient
    from app.main import app

    html = TestClient(app).get("/leaderboard").text
    assert "Rank (UB)" in html
    assert "ci-bar" in html  # the whisker bar is present


def test_tie_is_split_into_both_directions_for_bt():
    """A 'tie' vote must feed Bradley-Terry as one win in EACH direction (not dropped)."""
    from app import service
    from app.models import Comparison, Criterion, ModelOutput, Vote

    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.commit()
        crit = db.query(Criterion).filter_by(slug="overall").one()
        a = db.query(ModelOutput).filter_by(is_gold=False).first()
        b = (
            db.query(ModelOutput)
            .filter(ModelOutput.task_id == a.task_id, ModelOutput.id != a.id, ~ModelOutput.is_gold)
            .first()
        )
        comp = Comparison(
            task_id=a.task_id,
            output_a_id=a.id,
            output_b_id=b.id,
            criterion_id=crit.id,
            session_id="tie-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="tie", session_id="tie-sess"))
        db.commit()

        matches, _groups = service._matches_for_scope(db, crit.id, None)
        ga, gb = a.generator_id, b.generator_id
    # The single tie contributes BOTH orderings -- split credit, not dropped.
    assert (ga, gb) in matches
    assert (gb, ga) in matches


def test_bad_vote_excluded_from_matches():
    from app import service
    from app.models import Comparison, Criterion, ModelOutput, Vote

    with SessionLocal() as db:
        db.query(Vote).delete()
        db.query(Comparison).delete()
        db.commit()
        crit = db.query(Criterion).filter_by(slug="overall").one()
        a = db.query(ModelOutput).filter_by(is_gold=False).first()
        b = (
            db.query(ModelOutput)
            .filter(ModelOutput.task_id == a.task_id, ModelOutput.id != a.id, ~ModelOutput.is_gold)
            .first()
        )
        comp = Comparison(
            task_id=a.task_id,
            output_a_id=a.id,
            output_b_id=b.id,
            criterion_id=crit.id,
            session_id="bad-sess",
        )
        db.add(comp)
        db.flush()
        db.add(Vote(comparison_id=comp.id, winner="bad", session_id="bad-sess"))
        db.commit()
        matches, _groups = service._matches_for_scope(db, crit.id, None)
    assert matches == []  # 'bad' contributes nothing


def test_methodology_mentions_rank_ub_and_ties():
    from fastapi.testclient import TestClient
    from app.main import app

    html = TestClient(app).get("/methodology").text
    assert "Rank (UB)" in html
    assert "tie" in html.lower()
