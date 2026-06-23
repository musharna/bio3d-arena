import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_lpy import CROPS, ingest_lpy

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def test_lpy_crops_include_maize():
    # Track B LLM-synth: an authored maize L-system wired as a crop variant.
    assert CROPS["maize"]["task_title"] == "Zea mays — single-image → 3D reconstruction"
    assert CROPS["maize"]["model"].endswith("maize.lpy")
    assert CROPS["tomato"]["variant"] == "tomato"


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


def test_ingest_lpy_hosts_procedural_no_caveat(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "tomato_0.obj"
        trimesh.creation.box().export(str(obj))
        report = ingest_lpy(db, [str(obj)], to_glb=_fake_to_glb, variant="lpyHost")
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("lpyHost")))
            .scalars()
            .one()
        )
        assert out.source == "procedural:lpy"
        assert sourcing.source_class(out.source) == "procedural"
        assert "L-Py" in out.license or "PlantGL" in out.license
        assert out.external_url and "lpy" in out.external_url
        # L-Py passed the gate → no caveat badge
        assert "caveat" not in json.loads(out.meta_json)
    finally:
        db.close()


def test_ingest_lpy_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising_to_glb(path):
            raise MeshConvertError("bad obj")

        report = ingest_lpy(db, [str(tmp_path / "x.obj")], to_glb=raising_to_glb, variant="lpySkip")
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()


def test_ingest_lpy_scoring_failure_keeps_hosted_object(tmp_path):
    """A scoring failure rolls back only the metric — the hosted object survives, hosted stays 1."""
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "iso_0.obj"
        trimesh.creation.box().export(str(obj))

        def boom_score(db_, out):
            raise RuntimeError("scorer unreachable")

        report = ingest_lpy(
            db, [str(obj)], to_glb=_fake_to_glb, score_fn=boom_score, variant="lpyIso"
        )
        assert report["hosted"] == 1
        assert report["errors"] == 0
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.attribution.contains("lpyIso")))
            .scalars()
            .one()
        )
        assert out.asset_path
    finally:
        db.close()
