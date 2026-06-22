import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_blender import ingest_blender

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
    # Blender exports GLB natively; the adapter's real to_glb just reads bytes. Here we synthesize.
    return trimesh.creation.box().export(file_type="glb")


def test_ingest_blender_hosts_procedural_no_caveat(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        report = ingest_blender(
            db, [str(tmp_path / "tomato_0.glb")], to_glb=_fake_glb, variant="blenderHost"
        )
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("blenderHost")))
            .scalars()
            .one()
        )
        assert out.source == "procedural:blender"
        assert sourcing.source_class(out.source) == "procedural"
        assert "Blender" in out.license
        assert out.external_url and "blender.org" in out.external_url
        # passed the gate → no caveat
        assert "caveat" not in json.loads(out.meta_json)
    finally:
        db.close()


def test_ingest_blender_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising(path):
            raise MeshConvertError("bad glb")

        report = ingest_blender(
            db, [str(tmp_path / "x.glb")], to_glb=raising, variant="blenderSkip"
        )
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_blender_scoring_failure_keeps_hosted_object(tmp_path):
    """A scoring failure rolls back only the metric — the hosted object survives, hosted stays 1."""
    db = SessionLocal()
    try:
        _tomato_task(db)

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_blender(
            db,
            [str(tmp_path / "iso_0.glb")],
            to_glb=_fake_glb,
            score_fn=boom_score,
            variant="blenderIso",
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("blenderIso")))
            .scalars()
            .one()
        )
        assert out.asset_path
    finally:
        db.close()
