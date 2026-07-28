"""pick_task must use the SAME exclusion predicate as pick_pair, so it never returns a
task that has <2 votable outputs after exclusion (which made /api/next 404 intermittently)."""

from __future__ import annotations

from app import matchmaking
from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.sourcing import is_reference_scan, is_untextured_output
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


_EXCL = lambda o: is_reference_scan(o.source) or is_untextured_output(o)  # noqa: E731


def _clear(db):
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like("mmx/%.glb"))
    cascade_delete(db, Task, Task.title.like("mmx-%"))
    cascade_delete(db, Generator, Generator.slug.like("mmx-g%"))
    db.query(Category).filter_by(slug="mmx-cat").delete(synchronize_session=False)
    db.commit()


def _add_output(db, task, i, source, meta=None):
    g = Generator(slug=f"mmx-g{task.id}-{i}", name=f"G{i}")
    db.add(g)
    db.flush()
    o = ModelOutput(
        task_id=task.id,
        generator_id=g.id,
        asset_path=f"mmx/{task.id}-{i}.glb",
        asset_format="glb",
        source=source,
        meta_json=meta,
    )
    db.add(o)
    db.flush()
    return o


def _seed(db):
    _clear(db)
    cat = Category(slug="mmx-cat", name="C")
    db.add(cat)
    db.flush()
    # Thin task: 2 real outputs, but BOTH are excluded (1 reference scan + 1 untextured).
    thin = Task(category_id=cat.id, title="mmx-thin", prompt="p")
    db.add(thin)
    db.flush()
    _add_output(db, thin, 0, "rose-x")  # reference scan
    _add_output(db, thin, 1, "bio3d-arena", meta='{"untextured": true}')  # untextured
    # Healthy task: 2 textured AI outputs (survive exclusion).
    healthy = Task(category_id=cat.id, title="mmx-healthy", prompt="p")
    db.add(healthy)
    db.flush()
    _add_output(db, healthy, 0, "api:fal:trellis")
    _add_output(db, healthy, 1, "api:fal:trellis")
    db.commit()
    return thin, healthy


def test_pick_task_excludes_tasks_with_no_votable_pair():
    with SessionLocal() as db:
        thin, healthy = _seed(db)
        # Restrict to our category so pre-existing seed tasks don't interfere.
        cat_id = thin.category_id
        # The thin task must NEVER be chosen (no votable pair after exclusion); only healthy.
        picks = {
            matchmaking.pick_task(db, category_id=cat_id, exclude_fn=_EXCL).id for _ in range(40)
        }
        assert picks == {healthy.id}


def test_pick_task_returns_none_when_all_tasks_thin():
    with SessionLocal() as db:
        thin, healthy = _seed(db)
        # Drop the healthy task's outputs so every task in the category is thin.
        cascade_delete(db, ModelOutput, ModelOutput.task_id == healthy.id)
        db.commit()
        assert matchmaking.pick_task(db, category_id=thin.category_id, exclude_fn=_EXCL) is None


def test_pick_task_without_exclude_fn_keeps_legacy_behavior():
    with SessionLocal() as db:
        thin, healthy = _seed(db)
        # No exclude_fn: thin task qualifies on raw output count (>=2), as before.
        picks = {matchmaking.pick_task(db, category_id=thin.category_id).id for _ in range(40)}
        assert picks == {thin.id, healthy.id}
