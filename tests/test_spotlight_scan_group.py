import trimesh

from app import ingest, spotlight
from app.database import SessionLocal, init_db
from app.models import Category, Task


def setup_module(_m):
    init_db()


def test_build_spotlight_marks_scan_class(tmp_path, monkeypatch):
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(
            slug="plants", name="Plants"
        )
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="Scan Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = tmp_path / "s.glb"
        trimesh.creation.box().export(str(glb))
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="scan:plant3d",
            generator_name="Plant3D (Salk)",
            data=glb.read_bytes(),
            ext="glb",
            title="scanA",
            meta={"depiction": "whole_plant", "dataset": "plant3d"},
        )
        out.source = "plant3d"
        db.commit()
        monkeypatch.setattr(
            spotlight,
            "SPOTLIGHTS",
            [
                {
                    "slug": "s",
                    "task_title": "Scan Subject",
                    "featured": True,
                    "order": 0,
                    "blurb": "b",
                    "reference_image": None,
                },
            ],
        )
        m = spotlight.build_spotlight(db, "s")["models"][0]
        assert m["cls"] == "scan"
        assert m["dataset"] == "plant3d"
        assert m["label"] == "scanA"
    finally:
        db.close()
