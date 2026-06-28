"""Tests for thumbnail storage + Critique upsert (the testable half of render_thumbnails)."""

from __future__ import annotations

import random

from app.database import SessionLocal, init_db
from app.models import Category, Critique, Generator, ModelOutput, Task
from app.storage import LocalStorageBackend
from app.thumbnails import store_thumbnail, thumbnail_rel_path


def _mk_output(db):
    r = random.randint(0, 10**6)
    cat = Category(slug=f"c-th-{r}", name="Plants")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title=f"t-th-{r}", prompt="p")
    gen = Generator(slug=f"g-th-{r}", name="M-th")
    db.add_all([task, gen])
    db.flush()
    out = ModelOutput(
        task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
    )
    db.add(out)
    db.flush()
    return out


def test_store_thumbnail_saves_and_sets_render_path(tmp_path):
    init_db()
    db = SessionLocal()
    try:
        store = LocalStorageBackend(tmp_path / "assets")
        out = _mk_output(db)
        rel = store_thumbnail(db, out, b"\x89PNG-stub", storage=store)
        assert rel == thumbnail_rel_path(out.id)
        assert store.exists(rel)
        assert store.read(rel) == b"\x89PNG-stub"
        crit = db.query(Critique).filter_by(output_id=out.id).one()
        assert crit.render_path == rel
        assert crit.status == "ok"
    finally:
        db.close()


def test_store_thumbnail_upserts_preserving_critic_note(tmp_path):
    init_db()
    db = SessionLocal()
    try:
        store = LocalStorageBackend(tmp_path / "assets")
        out = _mk_output(db)
        db.add(Critique(output_id=out.id, critic_note="keep me", render_path=None))
        db.commit()

        store_thumbnail(db, out, b"png-bytes", storage=store)

        crits = db.query(Critique).filter_by(output_id=out.id).all()
        assert len(crits) == 1  # upsert, not a duplicate row
        assert crits[0].render_path == thumbnail_rel_path(out.id)
        assert crits[0].critic_note == "keep me"  # existing note preserved
    finally:
        db.close()
