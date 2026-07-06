# tests/test_difficulty_view.py
from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty
from app.main import app
from app.models import (
    Category,
    Generator,
    Metric,
    ModelOutput,
    Task,
    TaskDifficulty,
)


def setup_module(_m):
    init_db()


def _clean(db):
    db.query(TaskDifficulty).delete()
    db.query(Metric).filter(Metric.detail == "dvw").delete(synchronize_session=False)
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("dvw/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title.like("dvw-%")).delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("dvw-%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="dvw-cat").delete(synchronize_session=False)
    db.commit()


def _setup():
    # cross-session (TestClient opens its own session) → must COMMIT.
    with SessionLocal() as db:
        _clean(db)
        cat = Category(slug="dvw-cat", name="C")
        g = Generator(slug="dvw-recon", name="Recon", paradigm="image_recon")
        db.add_all([cat, g])
        db.flush()
        t = Task(category_id=cat.id, title="dvw-hard", prompt="p")
        db.add(t)
        db.flush()
        o = ModelOutput(task_id=t.id, generator_id=g.id, asset_path="dvw/1.glb")
        db.add(o)
        db.flush()
        db.add(
            Metric(output_id=o.id, chamfer=0.2, fscore=0.6, species_verdict="PASS", detail="dvw")
        )
        set_task_difficulty(db, t.id, "hard", "occlusion", commit=False)
        db.commit()


def test_api_difficulty_includes_paradigm_grid():
    _setup()
    data = TestClient(app).get("/api/difficulty.json").json()
    assert "scorecard" in data  # existing key preserved
    assert "paradigm_grid" in data
    hard = next(b for b in data["paradigm_grid"] if b["tier"] == "hard")
    assert any(r["paradigm"] == "image_recon" for r in hard["rows"])


def test_difficulty_page_renders_paradigm_grid():
    _setup()
    html = TestClient(app).get("/difficulty").text
    assert "Paradigm × difficulty" in html
    assert "Image→3D reconstruction" in html
