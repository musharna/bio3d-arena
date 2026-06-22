import json

import trimesh
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.generate_api_text import generate_api_text

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


def test_generate_api_text_hosts_with_text_modality(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        seen = {}

        def fake_fn(prompt, *, api_key):
            seen["prompt"] = prompt
            seen["key"] = api_key
            return _box_glb()

        providers = {"fal:hunyuan3d-v3-text-tTxt": (fake_fn, "FAL_KEY", "Hunyuan3D v3 text")}
        report = generate_api_text(db, "a tomato plant", providers=providers, env={"FAL_KEY": "k"})
        assert report["generated"] == 1
        assert seen["prompt"] == "a tomato plant" and seen["key"] == "k"  # prompt + key passed
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.generator_id.isnot(None)))
            .scalars()
            .all()
        )
        hit = [o for o in out if o.source == "api:fal:hunyuan3d-v3-text-tTxt"]
        assert len(hit) == 1
        meta = json.loads(hit[0].meta_json)
        assert meta["modality"] == "text" and meta["from_prompt"] is True
        assert meta["depiction"] == "whole_plant"  # targets the whole plant, like image-recon baseline
        assert "text→3D" in hit[0].attribution
    finally:
        db.close()


def test_generate_api_text_skips_when_no_key():
    db = SessionLocal()
    try:
        _tomato_task(db)

        def fake_fn(prompt, *, api_key):
            raise AssertionError("must not be called without a key")

        providers = {"fal:rodin-text-tNK": (fake_fn, "FAL_KEY", "Rodin text")}
        report = generate_api_text(db, "x", providers=providers, env={})  # no FAL_KEY
        assert report["skipped_no_key"] == 1 and report["generated"] == 0
    finally:
        db.close()


def test_generate_api_text_one_failure_does_not_abort_batch():
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom(prompt, *, api_key):
            raise RuntimeError("provider 500")

        def ok(prompt, *, api_key):
            return _box_glb()

        providers = {
            "fal:bad-text-tB": (boom, "FAL_KEY", "Bad"),
            "fal:good-text-tG": (ok, "FAL_KEY", "Good"),
        }
        report = generate_api_text(db, "x", providers=providers, env={"FAL_KEY": "k"})
        assert report["errors"] == 1 and report["generated"] == 1
    finally:
        db.close()
