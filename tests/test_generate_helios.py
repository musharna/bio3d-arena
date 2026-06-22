import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_helios import ingest_helios

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


def test_ingest_helios_hosts_procedural(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "tomato_0.obj"
        trimesh.creation.box().export(str(obj))  # a real OBJ with faces

        def fake_to_glb(path):
            return trimesh.load(path, force="mesh").export(file_type="glb")

        # unique variant → unique attribution for the read-back (shared file-backed test DB)
        report = ingest_helios(db, [str(obj)], to_glb=fake_to_glb, variant="tomatoHost")
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("tomatoHost")))
            .scalars()
            .one()
        )
        assert out.source == "procedural:helios"
        assert sourcing.source_class(out.source) == "procedural"
        assert "GPL-2.0" in out.license
        assert out.external_url and "Helios" in out.external_url
        assert json.loads(out.meta_json)["variant"] == "tomatoHost"
    finally:
        db.close()


def test_ingest_helios_caveat_flows_into_meta(tmp_path):
    """A caveat is stored in meta_json (so the spotlight badges it); omitted when not given."""
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "cav_0.obj"
        trimesh.creation.box().export(str(obj))

        def fake_to_glb(path):
            return trimesh.load(path, force="mesh").export(file_type="glb")

        ingest_helios(
            db,
            [str(obj)],
            to_glb=fake_to_glb,
            variant="heliosCaveat",
            caveat="FSPM sim mesh \u2014 low standalone fidelity",
        )
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("heliosCaveat")))
            .scalars()
            .one()
        )
        assert json.loads(out.meta_json)["caveat"] == "FSPM sim mesh \u2014 low standalone fidelity"

        # no caveat -> no caveat key in meta
        obj2 = tmp_path / "nocav_0.obj"
        trimesh.creation.box().export(str(obj2))
        ingest_helios(db, [str(obj2)], to_glb=fake_to_glb, variant="heliosNoCaveat")
        out2 = (
            db.execute(
                select(ModelOutput).where(ModelOutput.attribution.contains("heliosNoCaveat"))
            )
            .scalars()
            .one()
        )
        assert "caveat" not in json.loads(out2.meta_json)
    finally:
        db.close()


def test_ingest_helios_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising_to_glb(path):
            raise MeshConvertError("no faces")

        report = ingest_helios(db, [str(tmp_path / "x.obj")], to_glb=raising_to_glb)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_helios_scoring_failure_keeps_hosted_object(tmp_path):
    """A scoring failure rolls back only the metric — the hosted object survives, hosted stays 1."""
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "iso_0.obj"
        trimesh.creation.box().export(str(obj))

        def fake_to_glb(path):
            return trimesh.load(path, force="mesh").export(file_type="glb")

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_helios(
            db, [str(obj)], to_glb=fake_to_glb, score_fn=boom_score, variant="heliosIso"
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0  # a scoring failure is not a provider error
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("heliosIso")))
            .scalars()
            .one()
        )
        assert out.asset_path  # hosted GLB survived the scoring rollback
    finally:
        db.close()
