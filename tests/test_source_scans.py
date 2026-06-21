# tests/test_source_scans.py
import json

import numpy as np
import trimesh

from app.database import SessionLocal, init_db
from app.mesh_convert import to_glb
from app.models import Category, ModelOutput, Task
from scripts.source_scans import ingest_scans

TOMATO = "Solanum lycopersicum — single-image → 3D reconstruction"


def setup_module(_m):
    init_db()


def _tomato_task(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=TOMATO, prompt="p")
    db.add(t)
    db.commit()
    return t


def test_ingest_scans_hosts_mesh_skips_cloud(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        mesh = tmp_path / "scan1.obj"
        trimesh.creation.box().export(str(mesh))
        cloud = tmp_path / "scan2.ply"
        trimesh.PointCloud(np.random.rand(40, 3)).export(str(cloud))
        report = ingest_scans(
            db,
            [str(mesh), str(cloud)],
            dataset="plant3d",
            to_glb=to_glb,
            score_fn=None,
        )
        assert report["hosted"] == 1
        assert report["skipped_pointcloud"] == 1
        out = db.query(ModelOutput).filter(ModelOutput.source == "plant3d").one()
        assert out.license == "CC-BY 4.0"
        assert out.asset_format == "glb"
        assert "Salk" in (out.attribution or "")
        assert json.loads(out.meta_json)["depiction"] == "whole_plant"
        assert json.loads(out.meta_json)["dataset"] == "plant3d"
    finally:
        db.close()
