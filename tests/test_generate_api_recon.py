import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.image3d import Image3DError
from app.models import Category, ModelOutput, Task
from scripts.generate_api_recon import generate_api_recon

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


def test_generate_api_recon_ingests_ai_output():
    db = SessionLocal()
    try:
        _tomato_task(db)
        glb = _box_glb()

        def fake_tripo(image_bytes, *, api_key):
            assert api_key == "secret-key"
            return glb

        providers = {"tripo": (fake_tripo, "TRIPO_API_KEY", "Tripo")}
        report = generate_api_recon(
            db, b"img", providers=providers, env={"TRIPO_API_KEY": "secret-key"}
        )
        assert report["generated"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.source == "api:tripo")).scalars().one()
        )
        assert sourcing.source_class(out.source) == "ai"
        assert json.loads(out.meta_json)["provider"] == "tripo"
        assert out.attribution and "Tripo" in out.attribution
    finally:
        db.close()


def test_generate_api_recon_skips_provider_without_key():
    db = SessionLocal()
    try:
        _tomato_task(db)
        providers = {"tripo": (lambda *a, **k: b"x", "TRIPO_API_KEY", "Tripo")}
        report = generate_api_recon(db, b"img", providers=providers, env={})
        assert report["skipped_no_key"] == 1
        assert report["generated"] == 0
    finally:
        db.close()


def test_generate_api_recon_counts_provider_error():
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom(image_bytes, *, api_key):
            raise Image3DError("provider down")

        providers = {"tripo": (boom, "TRIPO_API_KEY", "Tripo")}
        report = generate_api_recon(db, b"img", providers=providers, env={"TRIPO_API_KEY": "k"})
        assert report["errors"] == 1
        assert report["generated"] == 0
    finally:
        db.close()
