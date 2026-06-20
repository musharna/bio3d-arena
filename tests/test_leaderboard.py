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
