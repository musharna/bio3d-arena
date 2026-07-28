from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import (
    Category,
    Criterion,
    Generator,
    JudgeRating,
    JudgeVote,
    ModelOutput,
    Task,
)
from tests.factories import cascade_delete, delete_outputs_matching


def setup_module(_m):
    init_db()


def _seed_votes(db):
    # Clean previous run's jr2-prefixed rows (Category+Generator slugs are unique).
    db.query(JudgeVote).delete()
    db.query(JudgeRating).delete()
    delete_outputs_matching(db, ModelOutput.asset_path.like("seed/%.glb"))
    cascade_delete(db, Task, Task.title == "jr2-task")
    cascade_delete(db, Generator, Generator.slug.like("jr2-%"))
    db.query(Category).filter_by(slug="jr2-cat").delete(synchronize_session=False)
    db.commit()
    cat = Category(slug="jr2-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    strong = Generator(slug="jr2-strong", name="Strong")
    weak = Generator(slug="jr2-weak", name="Weak")
    db.add_all([strong, weak])
    db.flush()
    task = Task(category_id=cat.id, title="jr2-task", prompt="p")
    db.add(task)
    db.flush()
    so = ModelOutput(task_id=task.id, generator_id=strong.id, asset_path="seed/s.glb")
    wo = ModelOutput(task_id=task.id, generator_id=weak.id, asset_path="seed/w.glb")
    db.add_all([so, wo])
    db.flush()
    # Strong (slot a) beats weak 9 times under multi4.
    for _ in range(9):
        db.add(
            JudgeVote(
                task_id=task.id,
                output_a_id=so.id,
                output_b_id=wo.id,
                criterion_id=crit.id,
                winner="a",
                view_condition="multi4",
                judge_model="claude-sonnet-4-6",
                swap_group="g",
                rationale="",
            )
        )
    db.commit()
    return crit, strong, weak


def test_recompute_judge_purges_rows_for_generators_no_longer_in_scope():
    """A cached judge rating must never outlive the fit that produced it.

    `_players_for_scope` drops a generator as soon as it has no non-gold outputs (hidden,
    deleted, or reclassified). The old upsert-only recompute then never touched that
    generator's row again, so a PRE-FIX Bradley-Terry score survived every later recompute and
    still rendered on the board. Production shape: 40 rows stranded at 2026-07-02 with scores
    from -6403 to +60292, including a VISIBLE model (TRELLIS, bt=18029) — long after the
    evidence-scaled prior had brought live fits back into [655, 1390].

    The kingdom sibling (`recompute_kingdom_judge_scope`) already delete-then-reinserts per
    scope for exactly this reason; this asserts the global path behaves the same."""
    with SessionLocal() as db:
        crit, _strong, _weak = _seed_votes(db)
        # A generator with NO outputs at all -> not in _players_for_scope, so an upsert-only
        # recompute can never reach its row.
        ghost = Generator(slug="jr2-ghost", name="Ghost")
        db.add(ghost)
        db.flush()
        db.add(
            JudgeRating(
                generator_id=ghost.id,
                criterion_id=crit.id,
                view_condition="multi4",
                category_id=None,
                bt_score=18029.3,
                bt_lower=8695.9,
                bt_upper=27709.3,
                n_games=119,
            )
        )
        db.commit()

        service.recompute_judge_scope(db, crit, "multi4")

        stranded = (
            db.query(JudgeRating)
            .filter_by(generator_id=ghost.id, criterion_id=crit.id, view_condition="multi4")
            .one_or_none()
        )
        assert stranded is None, (
            "stale judge rating survived a recompute that no longer covers its generator "
            f"(bt_score={stranded.bt_score if stranded else None})"
        )
        # The generators still in scope keep real, in-range ratings.
        live = db.query(JudgeRating).filter_by(criterion_id=crit.id, view_condition="multi4").all()
        assert live, "recompute must still write ratings for in-scope generators"
        assert all(-2000 < r.bt_score < 4000 for r in live), [r.bt_score for r in live]


def test_recompute_judge_orders_strong_above_weak():
    with SessionLocal() as db:
        crit, strong, weak = _seed_votes(db)
        out = service.recompute_judge_scope(db, crit, "multi4")
        assert out["matches"] == 9
        rs = (
            db.query(JudgeRating)
            .filter_by(generator_id=strong.id, criterion_id=crit.id, view_condition="multi4")
            .one()
        )
        rw = (
            db.query(JudgeRating)
            .filter_by(generator_id=weak.id, criterion_id=crit.id, view_condition="multi4")
            .one()
        )
        assert rs.bt_score > rw.bt_score
        assert rs.n_games == 9


def test_recompute_judge_all_runs_over_criteria():
    with SessionLocal() as db:
        _seed_votes(db)
        res = service.recompute_judge_all(db, view_condition="multi4")
        assert res["status"] == "ok"
        assert res["view_condition"] == "multi4"
