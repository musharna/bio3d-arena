import json

from app.assets_gen import build_asset
from app.database import SessionLocal, init_db
from app.models import Category, ModelOutput, Task
from scripts.source_objaverse import CROPS, MAIZE_TITLE, ingest_found

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


def _maize_task(db):
    if db.query(Task).filter_by(title=MAIZE_TITLE).first():
        return  # get-or-create: the test DB is shared across modules
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    db.add(Task(category_id=cat.id, title=MAIZE_TITLE, prompt="p"))
    db.commit()


def test_maize_crop_config():
    m = CROPS["maize"]
    assert m["task_title"] == MAIZE_TITLE
    assert m["lvis_keyword"] == "edible_corn"
    assert m["require_public_safe"] is True  # ears are decorative game assets, keep the set clean
    assert m["depiction_override"] == "fruit"  # a corn ear is the fruit, not a whole plant


def test_maize_filters_public_safe_corn_ears(tmp_path):
    """Maize pull: keep public-safe corn ears (depiction='fruit'); drop NC, off-subject, and noise."""
    db = SessionLocal()
    try:
        _maize_task(db)
        glb = tmp_path / "obj.glb"
        build_asset("flower", 1, glb)
        annotations = {
            "u_ear": {  # on-subject, public-safe CC-BY → host as fruit
                "license": "by",
                "name": "Corn On The Cob",
                "viewerUrl": "https://sketchfab.com/3d-models/corn-u_ear",
            },
            "u_nc": {  # NC is not public-safe → excluded under require_public_safe
                "license": "by-nc",
                "name": "Corn_2",
                "viewerUrl": "https://sketchfab.com/3d-models/corn2-u_nc",
            },
            "u_dog": {  # name-excluded noise (corn dog) → off_subject
                "license": "by",
                "name": "Corn Dog",
                "viewerUrl": "https://sketchfab.com/3d-models/corndog-u_dog",
            },
            "u_banana": {  # no corn/maize token → off_subject
                "license": "by",
                "name": "Fuzzy Banna",
                "viewerUrl": "https://sketchfab.com/3d-models/banna-u_banana",
            },
        }
        m = CROPS["maize"]
        report = ingest_found(
            db,
            list(annotations),
            fetch_annotations=lambda uids: {u: annotations[u] for u in uids},
            fetch_objects=lambda uids: {u: str(glb) for u in uids},
            score_fn=None,
            task_title=MAIZE_TITLE,
            name_includes=m["name_includes"],
            name_excludes=m["name_excludes"],
            require_public_safe=m["require_public_safe"],
            depiction_override=m["depiction_override"],
        )
        assert report["hosted"] == 1, report
        assert report["excluded"] == 1, report  # the NC one
        assert report["off_subject"] == 2, report  # corn dog + banana
        assert report["by_depiction"] == {"fruit": 1}, report
        maize_task = db.query(Task).filter_by(title=MAIZE_TITLE).first()
        rows = (
            db.query(ModelOutput)
            .filter(ModelOutput.source == "objaverse", ModelOutput.task_id == maize_task.id)
            .all()
        )
        hit = [o for o in rows if o.title == "Corn On The Cob"]
        assert hit, rows
        assert json.loads(hit[0].meta_json)["depiction"] == "fruit"
    finally:
        db.close()
