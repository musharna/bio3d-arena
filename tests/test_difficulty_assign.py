from __future__ import annotations

import pytest

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty
from app.models import Category, Task, TaskDifficulty
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


def _task(db):
    cat = Category(slug="td2-cat", name="C")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="td2-task", prompt="p")
    db.add(task)
    db.flush()
    return task


def _clean(db):
    db.query(TaskDifficulty).delete()
    cascade_delete(db, Task, Task.title == "td2-task")
    db.query(Category).filter_by(slug="td2-cat").delete(synchronize_session=False)
    db.commit()


def test_set_valid_then_upsert():
    with SessionLocal() as db:
        _clean(db)
        task = _task(db)
        db.commit()
        row = set_task_difficulty(db, task.id, "easy", "open canopy")
        assert row.tier == "easy"
        # Re-assign updates in place (no duplicate row).
        row2 = set_task_difficulty(db, task.id, "hard", "occlusion")
        assert row2.tier == "hard"
        assert db.query(TaskDifficulty).filter_by(task_id=task.id).count() == 1


def test_invalid_tier_raises():
    with SessionLocal() as db:
        _clean(db)
        task = _task(db)
        db.commit()
        with pytest.raises(ValueError):
            set_task_difficulty(db, task.id, "extreme", "")


def test_unknown_task_raises():
    with SessionLocal() as db:
        _clean(db)
        with pytest.raises(ValueError):
            set_task_difficulty(db, 999999, "easy", "")
