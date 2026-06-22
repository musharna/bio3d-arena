import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.generate_api_multiview import generate_api_multiview

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _tomato_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=TOMATO, prompt="p"))
    db.commit()


def _box_glb():
    return trimesh.creation.box().export(file_type="glb")


def test_recon_source_class_is_ai():
    assert sourcing.source_class("recon:trellis-mv") == "ai"  # multi-view recon = AI reconstruction


def test_generate_api_multiview_feeds_views_and_hosts(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        seen = {}

        def fake_fn(views, *, api_key):
            seen["n"] = len(views)
            seen["key"] = api_key
            return _box_glb()

        providers = {"recon:trellis-mv-tMV": (fake_fn, "FAL_KEY", "TRELLIS multi-view")}
        views = [b"view1", b"view2", b"view3"]
        report = generate_api_multiview(db, views, providers=providers, env={"FAL_KEY": "k"})
        assert report["generated"] == 1
        assert seen["n"] == 3 and seen["key"] == "k"  # all views + key passed through
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.source == "recon:trellis-mv-tMV"))
            .scalars()
            .one()
        )
        assert sourcing.source_class(out.source) == "ai"
        meta = json.loads(out.meta_json)
        assert meta["modality"] == "multiview" and meta["n_views"] == 3
    finally:
        db.close()


def test_generate_api_multiview_skips_when_no_key():
    db = SessionLocal()
    try:
        _tomato_task(db)

        def fake_fn(views, *, api_key):
            raise AssertionError("must not be called without a key")

        providers = {"recon:trellis-mv-tNK": (fake_fn, "FAL_KEY", "TRELLIS mv")}
        report = generate_api_multiview(db, [b"a", b"b"], providers=providers, env={})
        assert report["skipped_no_key"] == 1 and report["generated"] == 0
    finally:
        db.close()


def test_multiview_providers_catalog_and_mode():
    import functools

    from app.image3d import MULTIVIEW_PROVIDERS

    assert MULTIVIEW_PROVIDERS and all(k.startswith("recon:") for k in MULTIVIEW_PROVIDERS)
    assert all(v[1] == "FAL_KEY" for v in MULTIVIEW_PROVIDERS.values())
    fn = MULTIVIEW_PROVIDERS["recon:trellis-mv"][0]
    assert isinstance(fn, functools.partial) and fn.keywords.get("mode") == "multiview"
