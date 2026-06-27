from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import Category, ReconTask, Task, TaskDifficulty

from scripts.assign_difficulty import assign_all


def setup_module(_m):
    init_db()


def test_assign_all_maps_slugs_and_skips_missing():
    with SessionLocal() as db:
        db.query(TaskDifficulty).delete()
        db.query(ReconTask).filter(ReconTask.species_slug == "ad-sp").delete(
            synchronize_session=False
        )
        db.query(Task).filter(Task.title == "ad-task").delete(synchronize_session=False)
        db.query(Category).filter_by(slug="ad-cat").delete(synchronize_session=False)
        db.commit()
        cat = Category(slug="ad-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="ad-task", prompt="p")
        db.add(task)
        db.flush()
        db.add(ReconTask(task_id=task.id, species_slug="ad-sp", species_name="Sp"))
        db.commit()

        res = assign_all(
            db,
            mapping={"ad-sp": ("hard", "test"), "ad-missing": ("easy", "nope")},
        )
        assert res["assigned"] == 1
        assert res["skipped"] == ["ad-missing"]
        row = db.query(TaskDifficulty).filter_by(task_id=task.id).first()
        assert row.tier == "hard"
