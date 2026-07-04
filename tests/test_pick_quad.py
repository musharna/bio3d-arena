import uuid
from app import matchmaking
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _task_with(db, paradigm_counts):
    t = Task(title=f"q-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    for para, n in paradigm_counts.items():
        for _ in range(n):
            g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind="model", paradigm=para)
            db.add(g)
            db.flush()
            db.add(
                ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
            )
    db.flush()
    return t


def test_quad_returned_when_four_same_paradigm():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 4})
        quad = matchmaking.pick_quad(db, t)
        assert quad is not None and len(quad) == 4
        assert len({o.id for o in quad}) == 4
        assert len({o.generator.paradigm for o in quad}) == 1
        db.rollback()


def test_none_when_fewer_than_four_in_any_group():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 3, "text_native": 3})  # no single group has 4
        assert matchmaking.pick_quad(db, t) is None
        db.rollback()


def test_exclude_fn_applied_before_counting():
    with SessionLocal() as db:
        t = _task_with(db, {"procedural": 5})
        outs = sorted(matchmaking._real_outputs(t), key=lambda o: o.id)
        drop = {outs[0].id, outs[1].id}
        assert matchmaking.pick_quad(db, t, exclude_fn=lambda o: o.id in drop) is None
        db.rollback()
