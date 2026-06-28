"""Tests for GT-scan rendering: bundle .npy → baked reference GLB + reference_for_task fallback."""

from __future__ import annotations

import random

import numpy as np

from app import gt_render, recon_service
from app.database import SessionLocal, init_db
from app.models import Category, ReconTask, Task
from app.storage import LocalStorageBackend


def _make_bundle(tmp_path, species, n=3):
    """A fake GT bundle: n +Z-up (tall in Z, offset) .npy clouds for a species."""
    d = tmp_path / "bundle" / species
    d.mkdir(parents=True)
    for i in range(n):
        pts = np.random.RandomState(i).rand(500, 3) * np.array([0.3, 0.3, 2.0]) + np.array(
            [1, 1, 0.5]
        )
        np.save(d / f"{i:04d}.npy", pts)
    return tmp_path / "bundle"


def test_representative_picks_first_sorted(tmp_path):
    bundle = _make_bundle(tmp_path, "rosa", 3)
    npy = gt_render.representative_gt_npy("rosa", bundle_dir=bundle)
    assert npy is not None and npy.name == "0000.npy"


def test_representative_none_when_species_missing(tmp_path):
    bundle = _make_bundle(tmp_path, "rosa", 1)
    assert gt_render.representative_gt_npy("ghost_species", bundle_dir=bundle) is None


def test_bake_and_find_roundtrip(tmp_path):
    bundle = _make_bundle(tmp_path, "rosa", 2)
    store = LocalStorageBackend(tmp_path / "assets")
    rel = gt_render.bake_species_gt("rosa", bundle_dir=bundle, storage=store)
    assert rel == "gt/rosa.glb"
    assert store.exists(rel)
    assert store.read(rel)[:4] == b"glTF"
    assert gt_render.find_gt_glb("rosa", storage=store) == rel
    assert gt_render.find_gt_glb("zea_mays", storage=store) is None


def test_bake_returns_none_when_no_npy(tmp_path):
    store = LocalStorageBackend(tmp_path / "assets")
    (tmp_path / "bundle").mkdir()
    assert gt_render.bake_species_gt("ghost", bundle_dir=tmp_path / "bundle", storage=store) is None


def test_reference_for_task_falls_back_to_gt(tmp_path, monkeypatch):
    """With no reference_asset_id, reference_for_task serves the baked GT (is_gt=True)."""
    bundle = _make_bundle(tmp_path, "rosa", 1)
    store = LocalStorageBackend(tmp_path / "assets")
    gt_render.bake_species_gt("rosa", bundle_dir=bundle, storage=store)
    monkeypatch.setattr(gt_render, "get_storage", lambda: store)
    monkeypatch.setattr(recon_service, "get_storage", lambda: store)

    init_db()
    db = SessionLocal()
    try:
        cat = Category(slug="c-gt-%d" % random.randint(0, 10**6), name="Plants")
        db.add(cat)
        db.flush()
        t = Task(category_id=cat.id, title="gt-task", prompt="p")
        db.add(t)
        db.flush()
        db.add(ReconTask(task_id=t.id, species_slug="rosa", species_name="Rosa"))
        db.commit()

        ref = recon_service.reference_for_task(db, t.id)
        assert ref is not None
        assert ref["is_gt"] is True
        assert ref["format"] == "glb"
        assert "gt/rosa.glb" in ref["url"]
    finally:
        db.close()


def test_reference_for_task_none_without_gt_or_exemplar(tmp_path, monkeypatch):
    """A recon task whose species has no baked GT and no exemplar returns None."""
    store = LocalStorageBackend(tmp_path / "assets")  # empty store
    monkeypatch.setattr(gt_render, "get_storage", lambda: store)
    monkeypatch.setattr(recon_service, "get_storage", lambda: store)

    init_db()
    db = SessionLocal()
    try:
        cat = Category(slug="c-gt2-%d" % random.randint(0, 10**6), name="Plants")
        db.add(cat)
        db.flush()
        t = Task(category_id=cat.id, title="gt-task2", prompt="p")
        db.add(t)
        db.flush()
        db.add(ReconTask(task_id=t.id, species_slug="ghost_species", species_name="Ghost"))
        db.commit()

        assert recon_service.reference_for_task(db, t.id) is None
    finally:
        db.close()
