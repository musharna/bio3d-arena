"""Eval-loop review deferred-minors cleanup.

(1) human _leaderboard_rows must skip an orphan rating (deleted generator), like the
    judge version, instead of crashing on gen.name.
(2) recompute_judge_scope must record the actual judge_model from the votes, not a
    hardcoded constant.
(5) rank_correlation must return None (→ "—" in the report) on constant input, not nan.
"""

from __future__ import annotations

import math

from app import service
from app.calibration import rank_correlation
from app.database import SessionLocal, init_db
from app.main import _leaderboard_rows
from app.models import (
    Category,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    ModelOutput,
    Rating,
    Task,
)


def setup_module(_m):
    init_db()


def test_leaderboard_rows_skips_orphan_generator():
    with SessionLocal() as db:
        crit = Criterion(slug="dm1-crit", name="DM1")
        db.add(crit)
        gen = Generator(slug="dm1-doomed", name="Doomed")
        db.add(gen)
        db.flush()
        db.add(
            Rating(
                generator_id=gen.id,
                criterion_id=crit.id,
                category_id=None,
                elo=1000.0,
                bt_score=1.0,
                bt_lower=0.0,
                bt_upper=2.0,
                n_games=1,
            )
        )
        db.commit()
        db.delete(gen)  # SQLite has no FK cascade → rating is now an orphan
        db.commit()

        rows = _leaderboard_rows(db, criterion_slug="dm1-crit")  # must not raise
        assert rows == []  # orphan skipped, not rendered


def test_recompute_judge_scope_records_actual_model():
    with SessionLocal() as db:
        crit = Criterion(slug="dm2-crit", name="DM2")
        db.add(crit)
        a = Generator(slug="dm2-a", name="A")
        b = Generator(slug="dm2-b", name="B")
        db.add_all([a, b])
        cat = Category(slug="dm2-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="dm2-task", prompt="p")
        db.add(task)
        db.flush()
        oa = ModelOutput(task_id=task.id, generator_id=a.id, asset_path="dm2/a.glb")
        ob = ModelOutput(task_id=task.id, generator_id=b.id, asset_path="dm2/b.glb")
        db.add_all([oa, ob])
        db.flush()
        for _ in range(5):
            db.add(
                JudgeVote(
                    task_id=task.id,
                    output_a_id=oa.id,
                    output_b_id=ob.id,
                    criterion_id=crit.id,
                    winner="a",
                    view_condition="multi4",
                    judge_model="dm-test-opus-9",  # NOT the hardcoded default
                    swap_group="dm2g",
                    rationale="",
                )
            )
        db.commit()

        service.recompute_judge_scope(db, crit, "multi4")
        rating = (
            db.query(JudgeRating)
            .filter_by(generator_id=a.id, criterion_id=crit.id, view_condition="multi4")
            .first()
        )
        assert rating is not None
        assert rating.judge_model == "dm-test-opus-9"


def test_rank_correlation_constant_input_returns_none():
    with SessionLocal() as db:
        crit = Criterion(slug="dm5-crit", name="DM5")
        db.add(crit)
        gens = [Generator(slug=f"dm5-g{i}", name=f"G{i}") for i in range(3)]
        db.add_all(gens)
        db.flush()
        for i, g in enumerate(gens):
            # Human scores all identical → constant input (undefined correlation).
            db.add(
                Rating(
                    generator_id=g.id,
                    criterion_id=crit.id,
                    category_id=None,
                    elo=1000.0,
                    bt_score=1000.0,
                    bt_lower=0.0,
                    bt_upper=0.0,
                    n_games=1,
                )
            )
            db.add(
                JudgeRating(
                    generator_id=g.id,
                    criterion_id=crit.id,
                    view_condition="multi4",
                    category_id=None,
                    elo=1000.0,
                    bt_score=float(i + 1),  # varied
                    bt_lower=0.0,
                    bt_upper=0.0,
                    n_games=1,
                )
            )
        db.commit()

        res = rank_correlation(db, crit.id, "multi4")
        assert res["n"] == 3
        assert res["spearman"] is None  # not nan
        assert not (isinstance(res["spearman"], float) and math.isnan(res["spearman"]))
