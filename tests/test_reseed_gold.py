"""reseed_gold picks a real votable output as 'good', a degenerate decoy as 'bad',
excludes scans/untextured, and is idempotent."""

from __future__ import annotations

import app.config as config
from app.database import SessionLocal, init_db
from app.models import Category, Generator, GoldPair, ModelOutput, Task
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


def _clear(db):
    db.query(GoldPair).filter(GoldPair.note.like("reseed:%")).delete(synchronize_session=False)
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like("rsg/%"))
    cascade_delete(db, ModelOutput, ModelOutput.title.in_(["gold-good", "gold-bad"]))
    cascade_delete(db, Task, Task.title == "rsg-task")
    cascade_delete(db, Generator, Generator.slug.like("rsg-g%"))
    db.query(Category).filter_by(slug="rsg-cat").delete(synchronize_session=False)
    db.commit()


def _add_output(db, task, slug, source, n_cmp, meta=None):
    g = Generator(slug=slug, name=slug)
    db.add(g)
    db.flush()
    o = ModelOutput(
        task_id=task.id,
        generator_id=g.id,
        asset_path=f"rsg/{slug}.glb",
        asset_format="glb",
        source=source,
        n_comparisons=n_cmp,
        meta_json=meta or "{}",
    )
    db.add(o)
    db.flush()
    return o


def _seed(db):
    _clear(db)
    cat = Category(slug="rsg-cat", name="C")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="rsg-task", prompt="p")
    db.add(task)
    db.flush()
    # two textured outputs (different n_comparisons) + a scan + an untextured (both excluded)
    low = _add_output(db, task, "rsg-glow", "api:fal:trellis", n_cmp=2)
    high = _add_output(db, task, "rsg-ghigh", "api:fal:trellis", n_cmp=9)
    _add_output(db, task, "rsg-gscan", "rose-x", n_cmp=99)  # reference scan, excluded
    _add_output(db, task, "rsg-gunt", "bio3d-arena", n_cmp=99, meta='{"untextured": true}')
    db.commit()
    return task, low, high


def test_reseed_picks_real_good_and_degenerate_bad(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    import scripts.reseed_gold as rg

    with SessionLocal() as db:
        task, low, high = _seed(db)
        res = rg.reseed_gold(db, [task.id])
        assert res["created"] == 1 and res["skipped"] == 0

        gp = db.query(GoldPair).filter(GoldPair.task_id == task.id).one()
        good = db.get(ModelOutput, gp.good_output_id)
        bad = db.get(ModelOutput, gp.bad_output_id)
        # good is an is_gold copy of the most-vetted textured output (high, not the scan)
        assert good.is_gold and good.asset_path == high.asset_path
        assert '"source_output_id": %d' % high.id in good.meta_json
        # bad is an is_gold degenerate decoy with a real GLB written under ASSET_DIR
        assert bad.is_gold and bad.asset_path == f"gold/task{task.id}__bad.glb"
        assert (tmp_path / bad.asset_path).exists() and (
            tmp_path / bad.asset_path
        ).stat().st_size > 0
        # the scan/untextured outputs were never chosen
        assert good.asset_path not in ("rsg/rsg-gscan.glb", "rsg/rsg-gunt.glb")


def test_reseed_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    import scripts.reseed_gold as rg

    with SessionLocal() as db:
        task, _low, _high = _seed(db)
        rg.reseed_gold(db, [task.id])
        res2 = rg.reseed_gold(db, [task.id])
        assert res2["created"] == 0 and res2["skipped"] == 1
        assert db.query(GoldPair).filter(GoldPair.task_id == task.id).count() == 1


def test_reseed_skips_task_with_no_votable_output(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path)
    import scripts.reseed_gold as rg

    with SessionLocal() as db:
        _clear(db)
        cat = Category(slug="rsg-cat", name="C")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="rsg-task", prompt="p")
        db.add(task)
        db.flush()
        _add_output(db, task, "rsg-gonlyscan", "rose-x", n_cmp=5)  # only an excluded output
        db.commit()
        res = rg.reseed_gold(db, [task.id])
        assert res["created"] == 0 and res["skipped"] == 1
        assert db.query(GoldPair).filter(GoldPair.task_id == task.id).count() == 0
