import json

from app.assets_gen import build_asset
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.source_objaverse import ingest_found

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


def test_ingest_found_hosts_cc_excludes_arr(tmp_path):
    db = SessionLocal()
    try:
        _tomato_task(db)
        glb = tmp_path / "obj.glb"
        build_asset("flower", 1, glb)  # a real, trimesh-valid GLB
        annotations = {
            "u_cc": {
                "license": "CC-BY 4.0",
                "name": "Tomato plant in pot",
                "uri": "https://api.sketchfab.com/v3/models/u_cc",
                "viewerUrl": "https://sketchfab.com/3d-models/tomato-plant-u_cc",
            },
            "u_arr": {
                "license": "All Rights Reserved",
                "name": "Ripe tomato",
                "uri": "https://api.sketchfab.com/v3/models/u_arr",
            },
        }
        report = ingest_found(
            db,
            ["u_cc", "u_arr"],
            fetch_annotations=lambda uids: {u: annotations[u] for u in uids},
            fetch_objects=lambda uids: {u: str(glb) for u in uids},
            score_fn=None,
        )
        assert report["hosted"] == 1
        assert report["excluded"] == 1
        assert report["by_depiction"]["whole_plant"] == 1
        out = db.query(ModelOutput).filter(ModelOutput.source == "objaverse").one()
        assert out.license == "CC-BY 4.0"
        # FIX 3: must use the public viewerUrl, not the API uri
        assert out.external_url == "https://sketchfab.com/3d-models/tomato-plant-u_cc"
        assert out.title == "Tomato plant in pot"
        assert json.loads(out.meta_json)["depiction"] == "whole_plant"
    finally:
        db.close()


def test_ingest_found_skips_off_subject(tmp_path):
    """Non-tomato items in a LVIS 'tomato' category must be counted in off_subject, not hosted."""
    db = SessionLocal()
    try:
        _tomato_task(db)
        glb = tmp_path / "obj.glb"
        build_asset("flower", 1, glb)
        annotations = {
            "u_tomato": {
                "license": "by",  # real Sketchfab short code
                "name": "Cherry tomato cluster",
                "uri": "https://api.sketchfab.com/v3/models/u_tomato",
                "viewerUrl": "https://sketchfab.com/3d-models/cherry-tomato-u_tomato",
            },
            "u_apple": {
                "license": "by",
                "name": "Apple",
                "uri": "https://api.sketchfab.com/v3/models/u_apple",
            },
        }
        report = ingest_found(
            db,
            ["u_tomato", "u_apple"],
            fetch_annotations=lambda uids: {u: annotations[u] for u in uids},
            fetch_objects=lambda uids: {u: str(glb) for u in uids},
            score_fn=None,
        )
        assert report["hosted"] == 1, report
        assert report["off_subject"] == 1, report
        assert report["excluded"] == 0, report
        # The hosted model should be the tomato, using viewerUrl
        out = db.query(ModelOutput).filter(ModelOutput.source == "objaverse").one()
        assert out.external_url == "https://sketchfab.com/3d-models/cherry-tomato-u_tomato"
    finally:
        db.close()
