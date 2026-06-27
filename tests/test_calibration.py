from __future__ import annotations

import uuid

from app import calibration
from app.database import SessionLocal, init_db
from app.models import CalibrationPair, Category, Criterion, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _seed_two_tasks(db):
    db.query(CalibrationPair).delete()
    db.commit()
    tag = uuid.uuid4().hex[:8]
    cat = Category(slug=f"cal-cat-{tag}", name="C")
    db.add(cat)
    db.flush()
    for slug, name in [
        ("overall", "Overall"),
        ("visual_quality", "Visual quality"),
        ("structural_accuracy", "Structural accuracy"),
    ]:
        if not db.query(Criterion).filter_by(slug=slug).first():
            db.add(Criterion(slug=slug, name=name))
    gens = [Generator(slug=f"cal-g{i}-{tag}", name=f"G{i}") for i in range(4)]
    db.add_all(gens)
    db.flush()
    for t in range(2):
        task = Task(category_id=cat.id, title=f"cal-task-{t}", prompt="p")
        db.add(task)
        db.flush()
        for g in gens:
            db.add(
                ModelOutput(task_id=task.id, generator_id=g.id, asset_path=f"seed/{t}_{g.id}.glb")
            )
    db.commit()


def test_sampler_creates_stratified_distinct_pairs():
    with SessionLocal() as db:
        _seed_two_tasks(db)
        res = calibration.build_calibration_set(db, n_per_criterion=5, seed=7)
        assert res["created"] == 15  # 5 * 3 criteria
        rows = db.query(CalibrationPair).all()
        assert len(rows) == 15
        for r in rows:
            assert r.output_a_id != r.output_b_id  # no self-pairs
        per = res["per_criterion"]
        assert per["overall"] == 5 and per["visual_quality"] == 5
        assert per["structural_accuracy"] == 5


def test_sampler_is_deterministic_and_idempotent():
    with SessionLocal() as db:
        _seed_two_tasks(db)
        a = calibration.build_calibration_set(db, n_per_criterion=4, seed=99)
        keys_a = {
            (p.task_id, p.output_a_id, p.output_b_id, p.criterion_id)
            for p in db.query(CalibrationPair).all()
        }
        b = calibration.build_calibration_set(db, n_per_criterion=4, seed=99, replace=True)
        keys_b = {
            (p.task_id, p.output_a_id, p.output_b_id, p.criterion_id)
            for p in db.query(CalibrationPair).all()
        }
        assert a["created"] == b["created"]
        assert keys_a == keys_b  # same seed → same set; replace=True avoids duplicates
