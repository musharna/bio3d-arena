# tests/test_difficulty_schema.py
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, init_db
from app.difficulty import TIER_ORDER, TIERS
from app.models import Category, Task, TaskDifficulty


def setup_module(_m):
    init_db()


def test_tiers_vocab_ordered():
    assert TIERS == ("easy", "moderate", "hard")
    assert [TIER_ORDER[t] for t in TIERS] == [0, 1, 2]


def _task(db):
    cat = Category(slug="td1-cat", name="C")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="td1-task", prompt="p")
    db.add(task)
    db.flush()
    return task


def test_task_difficulty_roundtrip_and_unique():
    with SessionLocal() as db:
        db.query(TaskDifficulty).delete()
        db.query(Task).filter(Task.title == "td1-task").delete(synchronize_session=False)
        db.query(Category).filter_by(slug="td1-cat").delete(synchronize_session=False)
        db.commit()
        task = _task(db)
        db.add(TaskDifficulty(task_id=task.id, tier="hard", rationale="thin structure"))
        db.commit()
        row = db.query(TaskDifficulty).filter_by(task_id=task.id).first()
        assert row.tier == "hard"
        assert row.rationale == "thin structure"

        # task_id is unique — a second row for the same task must fail.
        db.add(TaskDifficulty(task_id=task.id, tier="easy", rationale="dup"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
