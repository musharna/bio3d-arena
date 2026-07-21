# tests/test_gold_reference_gate.py
"""Final-review Fix 1: a gold "good" output that aliases a recon (commercial-model) twin by
asset_path must have the TWIN's reference photo clearance-checked, not skipped. Before this
fix, assert_recon_photos_cleared read only the gold row's own source/meta_json -- a decoy
value (source="bio3d-arena", no meta_json) -- so it silently passed every gold output even
when the underlying asset it ships was a recon of an uncleared reference photo."""

import json
import uuid

import pytest

from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task
from app.reference_provenance import (
    ReferenceProvenanceError,
    assert_recon_photos_cleared_for_gold,
)


def setup_module(_m):
    init_db()


def _gen(db, kind="model"):
    g = Generator(slug=f"g-{uuid.uuid4().hex}", name="g", kind=kind, paradigm="p")
    db.add(g)
    db.flush()
    return g


def _task(db):
    t = Task(title=f"t-{uuid.uuid4().hex[:8]}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    return t


def _output(db, task, gen, *, asset_path, source, meta_json=None, is_gold=False):
    o = ModelOutput(
        task_id=task.id,
        generator_id=gen.id,
        asset_path=asset_path,
        asset_format="glb",
        source=source,
        meta_json=meta_json,
        is_gold=is_gold,
    )
    db.add(o)
    db.flush()
    return o


def test_gold_aliasing_uncleared_recon_twin_raises():
    with SessionLocal() as db:
        t = _task(db)
        _output(
            db,
            t,
            _gen(db),
            asset_path="c.glb",
            source="api:fal:trellis",
            meta_json=json.dumps({"input_image": "reference/zzz_ref.jpg"}),  # 'zzz' uncleared
        )
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="c.glb", source="bio3d-arena", is_gold=True
        )
        with pytest.raises(ReferenceProvenanceError):
            assert_recon_photos_cleared_for_gold(db, {gold.id})
        db.rollback()


def test_gold_aliasing_cleared_recon_twin_does_not_raise(monkeypatch):
    with SessionLocal() as db:
        t = _task(db)
        _output(
            db,
            t,
            _gen(db),
            asset_path="c2.glb",
            source="api:fal:trellis",
            meta_json=json.dumps({"input_image": "reference/okok_ref.jpg"}),  # this photo cleared
        )
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="c2.glb", source="bio3d-arena", is_gold=True
        )
        monkeypatch.setattr(
            "app.reference_provenance.cleared_reference_images", lambda: {"okok_ref.jpg"}
        )
        assert_recon_photos_cleared_for_gold(db, {gold.id})  # no raise
        db.rollback()


def test_gold_aliasing_non_recon_twin_is_not_checked():
    with SessionLocal() as db:
        t = _task(db)
        _output(db, t, _gen(db), asset_path="cc.glb", source="plant3d")
        gold = _output(
            db, t, _gen(db, "decoy"), asset_path="cc.glb", source="bio3d-arena", is_gold=True
        )
        assert_recon_photos_cleared_for_gold(db, {gold.id})  # no raise: twin isn't commercial
        db.rollback()


def test_gold_with_no_matching_twin_falls_back_to_own_and_is_not_checked():
    with SessionLocal() as db:
        t = _task(db)
        gold = _output(
            db,
            t,
            _gen(db, "decoy"),
            asset_path=f"orphan-{uuid.uuid4().hex}.glb",
            source="bio3d-arena",
            is_gold=True,
        )
        assert_recon_photos_cleared_for_gold(db, {gold.id})  # no raise: own source not commercial
        db.rollback()
