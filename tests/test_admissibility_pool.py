# tests/test_admissibility_pool.py
from __future__ import annotations

import uuid

from app.admissibility import Verdict
from app.database import SessionLocal
from app.main import _build_comparison
from app.models import Category, Comparison, Generator, ModelOutput, Task
from app import structural


def setup_module(_module):
    # Run seed_all(force=True) before any per-test session is opened (mirrors
    # tests/test_pool_autogate.py): seed_all opens its own SessionLocal() connection and
    # does delete+commit. Calling it mid-test, after an uncommitted db.flush() on the
    # caller's own session, deadlocks SQLite (two writer connections, 30s busy-timeout ->
    # "database is locked") -- unrelated to the gate being tested.
    from app.seed import seed_all  # ensure an 'overall' criterion exists

    seed_all(force=True)


def _task_with_two(db):
    cat = Category(slug=f"ap-{uuid.uuid4().hex[:8]}", name="C")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=f"ap-{uuid.uuid4().hex[:8]}", prompt="p")
    db.add(t)
    db.flush()
    outs = []
    for _ in range(3):
        g = Generator(slug=f"ap-{uuid.uuid4().hex}", name="g", kind="model", paradigm="same")
        db.add(g)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
        db.add(o)
        db.flush()
        outs.append(o)
    db.commit()
    return cat, outs


def test_structurally_rejected_output_never_served():
    with SessionLocal() as db:
        cat, outs = _task_with_two(db)
        structural.upsert_verdict(
            db, outs[0].id, "structural", Verdict(False, "empty", {}), structural.VERSION
        )
        db.commit()
        for _ in range(30):
            payload = _build_comparison(db, f"s-{uuid.uuid4().hex}", None, cat.slug)
            if payload is None:
                continue
            c = db.get(Comparison, payload["comparison_id"])
            assert outs[0].id not in {c.output_a_id, c.output_b_id}
