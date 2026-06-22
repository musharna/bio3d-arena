
import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_sketchfab import ingest_sketchfab

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


def _fake_glb(path):
    return trimesh.creation.box().export(file_type="glb")


def _asset(variant):
    return {
        "variant": variant,
        "uid": "abc123",
        "name": "Test Tomato",
        "author": variant,  # unique per test -> appears in attribution for read-back
        "license": "CC-BY 4.0",
        "keep": None,
    }


def test_ingest_sketchfab_hosts_found_with_cc_provenance(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        items = [(str(tmp_path / "a.glb"), _asset("sketchfab-tHost"))]
        report = ingest_sketchfab(db, items, to_glb=_fake_glb)
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("sketchfab-tHost")))
            .scalars()
            .one()
        )
        assert out.source == "found:sketchfab"
        assert sourcing.source_class(out.source) == "found"  # game asset = found, not procedural
        assert "CC-BY" in out.license and "sketchfab-tHost" in out.license  # author label carried into license
        assert out.external_url and "sketchfab.com" in out.external_url
    finally:
        db.close()


def test_ingest_sketchfab_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising(path):
            raise MeshConvertError("bad glb")

        report = ingest_sketchfab(db, [("x.glb", _asset("sketchfab-tSkip"))], to_glb=raising)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_sketchfab_scoring_failure_keeps_hosted_object(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_sketchfab(
            db,
            [(str(tmp_path / "i.glb"), _asset("sketchfab-tIso"))],
            to_glb=_fake_glb,
            score_fn=boom_score,
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("sketchfab-tIso")))
            .scalars()
            .one()
        )
        assert out.asset_path
    finally:
        db.close()
