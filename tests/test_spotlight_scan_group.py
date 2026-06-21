import trimesh
from fastapi.testclient import TestClient

from app import ingest, spotlight
from app.database import SessionLocal, init_db
from app.main import app
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


def test_scan_card_renders_under_scan_group_with_caveat(monkeypatch):
    """Template-render gate: a scan output lands under the 'Real scan' heading and
    the modality caveat is shown (so an 'outside natural variation' flag is not
    misread as reconstruction error)."""
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(
            slug="plants", name="Plants"
        )
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="Scan Caveat Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = trimesh.creation.box().export(file_type="glb")
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="scan:plant3d",
            generator_name="Plant3D (Salk)",
            data=glb,
            ext="glb",
            title="scanCaveatA",
            meta={"depiction": "whole_plant", "dataset": "plant3d"},
        )
        out.source = "plant3d"
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [
            {
                "slug": "scancaveat",
                "task_title": "Scan Caveat Subject",
                "featured": True,
                "order": 0,
                "blurb": "b",
                "reference_image": None,
            },
        ],
    )
    page = TestClient(app).get("/spotlight/scancaveat")
    assert page.status_code == 200
    assert "Real scan — whole plant" in page.text  # scan card grouped, not dropped
    assert "scored against the synthetic GT bundle for context only" in page.text


def test_points_scan_card_renders_point_cloud_badge(monkeypatch):
    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [
            {
                "slug": "pcbadge",
                "task_title": "PC Badge Subject",
                "featured": True,
                "order": 0,
                "blurb": "b",
                "reference_image": None,
            }
        ],
    )
    db = SessionLocal()
    try:
        cat = db.query(Category).filter_by(slug="plants").first() or Category(
            slug="plants", name="Plants"
        )
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title="PC Badge Subject", prompt="p")
        db.add(task)
        db.flush()
        glb = trimesh.PointCloud([[0, 0, 0], [1, 1, 1], [0, 1, 0]]).export(file_type="glb")
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug="scan:tomatowur",
            generator_name="TomatoWUR",
            data=glb,
            ext="glb",
            title="pcBadgeA",
            meta={"depiction": "whole_plant", "dataset": "tomatowur", "render": "points"},
        )
        out.source = "tomatowur"
        db.commit()
        model = spotlight.build_spotlight(db, "pcbadge")["models"][0]
        assert model["render"] == "points"
    finally:
        db.close()

    page = TestClient(app).get("/spotlight/pcbadge")
    assert page.status_code == 200
    assert "point cloud" in page.text
