"""_build_gold_comparison must not 500 when a GoldPair references a deleted task/output.

The create_all-only schema has no FK cascade, so a data purge can leave dangling gold
pairs; the vote path must skip them (return None → fall through to a real comparison)."""

from __future__ import annotations

from app.database import SessionLocal, init_db
from app.main import _build_gold_comparison
from app.models import Criterion, GoldPair


def setup_module(_m):
    init_db()


def test_build_gold_comparison_skips_dangling_pair(monkeypatch):
    from app import matchmaking

    with SessionLocal() as db:
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        # GoldPair pointing at ids that do not exist (purged task + outputs). Forced as the
        # selected pair so the guard is exercised deterministically (pick_gold_pair is random,
        # and the shared test DB may hold other, valid gold pairs).
        dangling = GoldPair(task_id=10**9, good_output_id=10**9 + 1, bad_output_id=10**9 + 2)
        monkeypatch.setattr(matchmaking, "pick_gold_pair", lambda _db: dangling)
        # Must return None (skip), not raise AttributeError on None.id.
        assert _build_gold_comparison(db, "sess-x", crit) is None
