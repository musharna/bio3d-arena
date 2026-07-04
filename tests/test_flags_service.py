# tests/test_flags_service.py
from __future__ import annotations

import uuid

from app import flags
from app.database import SessionLocal, init_db
from app.models import Completeness, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _output(db, category=None):
    g = Generator(slug=f"fg-{uuid.uuid4().hex}", name="g", kind="model", paradigm="p")
    db.add(g)
    db.flush()
    t = Task(title=f"ft-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb")
    db.add(o)
    db.flush()
    if category is not None:
        db.add(Completeness(output_id=o.id, category=category, score=0.0, scorer_version="v1"))
        db.flush()
    return o


def test_excluded_by_completeness():
    with SessionLocal() as db:
        frag = _output(db, "fragment")
        iso = _output(db, "isolated-organ")
        good = _output(db, "complete")
        partial = _output(db, "partial-organism")
        unscored = _output(db, None)
        db.commit()
        ex = flags.excluded_output_ids_by_completeness(db, {"isolated-organ", "fragment"})
        assert frag.id in ex and iso.id in ex
        assert good.id not in ex and partial.id not in ex and unscored.id not in ex


def test_excluded_reflects_rescored_category():
    with SessionLocal() as db:
        o = _output(db, "fragment")
        db.commit()
        assert o.id in flags.excluded_output_ids_by_completeness(db, {"isolated-organ", "fragment"})
        # rescore overwrites the single row (output_id is unique)
        row = db.query(Completeness).filter_by(output_id=o.id).one()
        row.category = "complete"
        db.commit()
        assert o.id not in flags.excluded_output_ids_by_completeness(
            db, {"isolated-organ", "fragment"}
        )


def test_record_flag_dedup_and_autohide():
    with SessionLocal() as db:
        o = _output(db)
        db.commit()
        hidden, n = flags.record_flag(db, o.id, "s1", "not_a_plant", threshold=3)
        assert (hidden, n) == (False, 1)
        # same session again → no double count
        hidden, n = flags.record_flag(db, o.id, "s1", "not_a_plant", threshold=3)
        assert (hidden, n) == (False, 1)
        flags.record_flag(db, o.id, "s2", "failed", threshold=3)
        hidden, n = flags.record_flag(db, o.id, "s3", "not_a_plant", threshold=3)
        db.commit()
        assert (hidden, n) == (True, 3)
        assert db.get(ModelOutput, o.id).hidden_at is not None
