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


def setup_module(_m):
    init_db()


def _seed_votes(db):
    # Clean previous run's jr2-prefixed rows (Category+Generator slugs are unique).
    db.query(JudgeVote).delete()
    db.query(JudgeRating).delete()
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("seed/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title == "jr2-task").delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("jr2-%")).delete(synchronize_session=False)
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
