# tests/test_pool_autogate.py
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app, _build_comparison
from app.models import Category, Completeness, Generator, ModelOutput, Task

client = TestClient(app)


def setup_module(_module):
    from app.seed import seed_all

    seed_all(force=True)


def _task_with(db, cats):
    """A task whose only paradigm group is `cats` (one output per completeness category)."""
    cat = Category(slug=f"pg-{uuid.uuid4().hex[:8]}", name="C")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=f"pg-{uuid.uuid4().hex[:8]}", prompt="p")
    db.add(t)
    db.flush()
    outs = []
    for c in cats:
        g = Generator(slug=f"pg-{uuid.uuid4().hex}", name="g", kind="model", paradigm="same")
        db.add(g)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
        db.add(o)
        db.flush()
        if c is not None:
            db.add(Completeness(output_id=o.id, category=c, score=0.0, scorer_version="v1"))
        outs.append(o)
    db.commit()
    return t, cat, outs


def test_pool_excludes_bad_categories_but_keeps_good():
    with SessionLocal() as db:
        t, cat, outs = _task_with(db, ["complete", "complete", "fragment", "isolated-organ"])
        bad = {outs[2].id, outs[3].id}
        for _ in range(30):
            payload = _build_comparison(db, f"s-{uuid.uuid4().hex}", None, cat.slug)
            if payload is None:
                continue
            comp_id = payload["comparison_id"]
            from app.models import Comparison

            c = db.get(Comparison, comp_id)
            assert not ({c.output_a_id, c.output_b_id} & bad)


def test_pool_excludes_hidden():
    with SessionLocal() as db:
        from app.models import Comparison, _utcnow

        t, cat, outs = _task_with(db, ["complete", "complete", "complete"])
        outs[0].hidden_at = _utcnow()
        db.commit()
        for _ in range(30):
            payload = _build_comparison(db, f"s-{uuid.uuid4().hex}", None, cat.slug)
            if payload is None:
                continue
            c = db.get(Comparison, payload["comparison_id"])
            assert outs[0].id not in {c.output_a_id, c.output_b_id}
