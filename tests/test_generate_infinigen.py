import json

import trimesh
from sqlalchemy import select

from app import sourcing
from app.database import SessionLocal, init_db
from app.mesh_convert import MeshConvertError
from app.models import Category, ModelOutput, Task
from scripts.generate_infinigen import ingest_infinigen

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


def test_ingest_infinigen_hosts_procedural(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        obj = tmp_path / "bush_0.obj"
        trimesh.creation.box().export(str(obj))  # a real OBJ with faces

        def fake_to_glb(path):
            return trimesh.load(path, force="mesh").export(file_type="glb")

        report = ingest_infinigen(db, [str(obj)], to_glb=fake_to_glb)
        assert report["hosted"] == 1
        out = (
            db.execute(select(ModelOutput).where(ModelOutput.source == "infinigen")).scalars().one()
        )
        assert sourcing.source_class(out.source) == "procedural"
        assert "BSD-3" in out.license
        assert out.external_url and "infinigen" in out.external_url
        assert json.loads(out.meta_json)["factory"] == "BushFactory"
    finally:
        db.close()


def test_ingest_infinigen_skips_unconvertible(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)

        def raising_to_glb(path):
            raise MeshConvertError("point cloud / no faces")

        report = ingest_infinigen(db, [str(tmp_path / "x.obj")], to_glb=raising_to_glb)
        assert report["skipped"] == 1
        assert report["hosted"] == 0
    finally:
        db.close()
