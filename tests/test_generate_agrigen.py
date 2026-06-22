import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_agrigen import ingest_agrigen

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


def _fake_to_glb(path):
    return trimesh.load(path, force="mesh").export(file_type="glb")


def test_ingest_agrigen_hosts_procedural(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "tomato_0.obj"
        trimesh.creation.box().export(str(obj))
        # unique variant → unique attribution for the read-back (shared file-backed test DB)
        report = ingest_agrigen(db, [str(obj)], to_glb=_fake_to_glb, variant="agriHost")
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("agriHost")))
            .scalars()
            .one()
        )
        assert out.source == "procedural:agrigen"
        assert sourcing.source_class(out.source) == "procedural"
        assert "AgriGen" in out.license
        assert json.loads(out.meta_json)["variant"] == "agriHost"
    finally:
        db.close()


def test_ingest_agrigen_caveat_flows_into_meta(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "cav_0.obj"
        trimesh.creation.box().export(str(obj))
        ingest_agrigen(
            db,
            [str(obj)],
            to_glb=_fake_to_glb,
            variant="agriCaveat",
            caveat="L-system + neural-leaf — low fidelity",
        )
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("agriCaveat")))
            .scalars()
            .one()
        )
        assert json.loads(out.meta_json)["caveat"] == "L-system + neural-leaf — low fidelity"
    finally:
        db.close()


def test_ingest_agrigen_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising_to_glb(path):
            raise MeshConvertError("bad glb")

        report = ingest_agrigen(db, [str(tmp_path / "x.glb")], to_glb=raising_to_glb)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_agrigen_scoring_failure_keeps_hosted_object(tmp_path):
    """A scoring failure rolls back only the metric — the hosted object survives, hosted stays 1."""
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "iso_0.obj"
        trimesh.creation.box().export(str(obj))

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_agrigen(
            db, [str(obj)], to_glb=_fake_to_glb, score_fn=boom_score, variant="agriIso"
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0  # a scoring failure is not a provider error
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("agriIso")))
            .scalars()
            .one()
        )
        assert out.asset_path  # hosted GLB survived the scoring rollback
    finally:
        db.close()
