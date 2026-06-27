from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.main import _judge_leaderboard_rows
from app.models import Category, Criterion, Generator, JudgeRating, JudgeVote, ModelOutput, Task


def setup_module(_m):
    init_db()


def test_judge_leaderboard_rows_ranked():
    with SessionLocal() as db:
        # Clean previous run's jl-prefixed rows, children before parents (Category +
        # Generator slugs are unique, so a re-run would otherwise hit a UNIQUE collision).
        db.query(JudgeVote).delete()
        db.query(JudgeRating).delete()
        db.query(ModelOutput).filter(ModelOutput.asset_path.like("seed/%.glb")).delete(
            synchronize_session=False
        )
        db.query(Task).filter(Task.title == "jl-task").delete(synchronize_session=False)
        db.query(Generator).filter(Generator.slug.like("jl-%")).delete(synchronize_session=False)
        db.query(Category).filter_by(slug="jl-cat").delete(synchronize_session=False)
        db.commit()
        cat = Category(slug="jl-cat", name="C")
        db.add(cat)
        db.flush()
        crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
            slug="overall", name="Overall"
        )
        db.add(crit)
        db.flush()
        s = Generator(slug="jl-strong", name="Strong")
        w = Generator(slug="jl-weak", name="Weak")
        db.add_all([s, w])
        db.flush()
        task = Task(category_id=cat.id, title="jl-task", prompt="p")
        db.add(task)
        db.flush()
        so = ModelOutput(task_id=task.id, generator_id=s.id, asset_path="seed/s.glb")
        wo = ModelOutput(task_id=task.id, generator_id=w.id, asset_path="seed/w.glb")
        db.add_all([so, wo])
        db.flush()
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
        service.recompute_judge_scope(db, crit, "multi4")
        rows = _judge_leaderboard_rows(db, "overall", "multi4")
        assert rows and rows[0]["generator"] == "Strong"
        assert rows[0]["rank"] == 1
