"""Synthetic-plant fidelity scope — category + botanical_plausibility criterion + per-type
tasks + the pd-archetype generator, all idempotent (votes-only, reuses the arena/BT pipeline)."""

from __future__ import annotations

from app import seed
from app.database import SessionLocal, init_db
from app.models import Category, Criterion, Generator, Task


def setup_module(_module):
    init_db()


def test_seed_synthetic_plants_creates_scope():
    db = SessionLocal()
    try:
        seed.seed_synthetic_plants(db)
        db.commit()
        cat = db.query(Category).filter(Category.slug == "synthetic-plants").first()
        crit = db.query(Criterion).filter(Criterion.slug == "botanical_plausibility").first()
        assert cat is not None and crit is not None
        tasks = db.query(Task).filter(Task.category_id == cat.id).all()
        assert len(tasks) >= 4
        assert db.query(Generator).filter(Generator.slug == "pd-archetype").first() is not None
        t = seed.synth_task_for_slug(db, "zea_mays")
        assert t is not None and t.category_id == cat.id
    finally:
        db.close()


def test_seed_synthetic_plants_is_idempotent():
    db = SessionLocal()
    try:
        seed.seed_synthetic_plants(db)
        db.commit()
        cat = db.query(Category).filter(Category.slug == "synthetic-plants").first()
        n_tasks = db.query(Task).filter(Task.category_id == cat.id).count()
        seed.seed_synthetic_plants(db)  # re-run
        db.commit()
        assert db.query(Task).filter(Task.category_id == cat.id).count() == n_tasks
    finally:
        db.close()
