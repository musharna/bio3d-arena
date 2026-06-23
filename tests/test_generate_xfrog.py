import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_xfrog import ingest_xfrog

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"
MAIZE = "Zea mays — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _task(db, title):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=title, prompt="p"))
    db.commit()


def _tomato_task(db):
    _task(db, TOMATO)


def _fake_glb(path):
    return trimesh.creation.box().export(file_type="glb")


def test_ingest_xfrog_hosts_found_growth_stage(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        report = ingest_xfrog(db, [(str(tmp_path / "s7.glb"), 7)], to_glb=_fake_glb)
        assert report["hosted"] == 1
        out = (
            db.execute(
                select(ModelOutput).where(ModelOutput.attribution.contains("growth stage 7"))
            )
            .scalars()
            .one()
        )
        assert out.source == "found:xfrog"
        assert (
            sourcing.source_class(out.source) == "found"
        )  # purchased asset = found, not procedural
        assert "XfrogPlants" in out.license and "re-vet" in out.license  # commercial + re-vet flag
        assert json.loads(out.meta_json)["growth_stage"] == 7
    finally:
        db.close()


def test_ingest_xfrog_maize_crop_labeling(tmp_path):
    """A second crop (maize/AG20) labels variant, title, and attribution from its own crop fields."""
    db = SessionLocal()
    try:
        _task(db, MAIZE)
        report = ingest_xfrog(
            db,
            [(str(tmp_path / "s8.glb"), 8)],
            to_glb=_fake_glb,
            ag_code="AG20",
            species="Zea mays",
            common="maize",
            task_title=MAIZE,
        )
        assert report["hosted"] == 1
        out = (
            db.execute(
                select(ModelOutput).where(ModelOutput.attribution.contains("Zea mays (AG20)"))
            )
            .scalars()
            .one()
        )
        assert out.source == "found:xfrog"
        meta = json.loads(out.meta_json)
        assert meta["variant"] == "xfrog-AG20-s8"
        assert meta["growth_stage"] == 8
        assert "maize" in out.title
    finally:
        db.close()


def test_ingest_xfrog_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising(path):
            raise MeshConvertError("bad fbx")

        report = ingest_xfrog(db, [(str(tmp_path / "x.glb"), 99)], to_glb=raising)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_xfrog_scoring_failure_keeps_hosted_object(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_xfrog(
            db, [(str(tmp_path / "i.glb"), 88)], to_glb=_fake_glb, score_fn=boom_score
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0
        out = (
            db.execute(
                select(ModelOutput).where(ModelOutput.attribution.contains("growth stage 88"))
            )
            .scalars()
            .one()
        )
        assert out.asset_path
    finally:
        db.close()
