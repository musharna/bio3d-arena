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


def test_generate_api_recon_scoring_failure_keeps_hosted_object():
    """Core invariant: a scoring failure rolls back ONLY the metric — the hosted
    ModelOutput survives and `generated` stays 1 (a scoring failure is NOT a provider
    error). Uses a unique slug so the shared file-backed test DB cannot dedup-collide
    with the api:tripo row another test leaves behind."""
    db = SessionLocal()
    try:
        _tomato_task(db)
        glb = _box_glb()

        def fake(image_bytes, *, api_key):
            return glb

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        providers = {"tripoiso": (fake, "TRIPOISO_KEY", "TripoIso")}
        report = generate_api_recon(
            db, b"img", providers=providers, env={"TRIPOISO_KEY": "k"}, score_fn=boom_score
        )
        assert report["generated"] == 1  # incremented before scoring
        assert report["errors"] == 0  # a scoring failure is not a provider error
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.source == "api:tripoiso"))
            .scalars()
            .one()
        )
        assert out.asset_path  # the hosted GLB survived the scoring rollback
    finally:
        db.close()
