from app import ingest, spotlight
from app.database import SessionLocal, init_db
from app.assets_gen import build_asset
from app.models import Category, Task


def setup_module(_m):
    init_db()


def test_build_spotlight_marks_found_and_label(tmp_path, monkeypatch):
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(
            slug="plants", name="Plants"
        )
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="Found Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = tmp_path / "x.glb"
        build_asset("flower", 2, glb)
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="objaverse",
            generator_name="Objaverse",
            data=glb.read_bytes(),
            ext="glb",
            title="Ripe tomato",
            meta={"depiction": "fruit", "found": True},
        )
        out.source = "objaverse"
        db.commit()
        monkeypatch.setattr(
            spotlight,
            "SPOTLIGHTS",
            [
                {
                    "slug": "f",
                    "task_title": "Found Subject",
                    "featured": True,
                    "order": 0,
                    "blurb": "b",
                    "reference_image": None,
                },
            ],
        )
        m = spotlight.build_spotlight(db, "f")["models"][0]
        assert m["found"] is True
        assert m["label"] == "Ripe tomato"  # object name, not "Objaverse"
        assert m["depiction"] == "fruit"
    finally:
        db.close()
