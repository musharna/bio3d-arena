# tests/test_spotlight_page.py
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app import ingest, spotlight
from app.assets_gen import build_asset
from app.database import SessionLocal, init_db
from app.main import app
from app.models import Category, Metric, Task


def setup_module(_m):
    init_db()


def _glb_bytes(seed: int = 1) -> bytes:
    tmp = Path(tempfile.mkdtemp(prefix="bio3d_sptl_")) / "stub.glb"
    build_asset("flower", seed, tmp)
    return tmp.read_bytes()


def _seed_subject(db):
    cat = db.query(Category).filter_by(slug="plants").first() or Category(
        slug="plants", name="Plants"
    )
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="Spotlight Test Subject", prompt="p")
    db.add(task)
    db.flush()
    for gslug, ch in [("m-a", 0.12), ("m-b", 0.18)]:
        out, _ = ingest.register_output(
            db,
            task_id=task.id,
            generator_slug=gslug,
            data=_glb_bytes(),
            ext="glb",
            title=f"out {gslug}",
        )
        db.add(
            Metric(
                output_id=out.id,
                status="ok",
                chamfer=ch,
                gt_band_lo=0.10,
                gt_band_hi=0.14,
                coverage=0.8,
                fscore=0.8,
            )
        )
    db.commit()
    return task


def test_build_spotlight_assembles_models(monkeypatch):
    db = SessionLocal()
    try:
        _seed_subject(db)
        monkeypatch.setattr(
            spotlight,
            "SPOTLIGHTS",
            [
                {
                    "slug": "test",
                    "task_title": "Spotlight Test Subject",
                    "featured": True,
                    "order": 0,
                    "blurb": "b",
                    "reference_image": None,
                },
            ],
        )
        data = spotlight.build_spotlight(db, "test")
        assert data is not None
        assert len(data["models"]) == 2
        # the 0.18 model must carry a shape flag; the 0.12 model must be ok
        flags = {m["generator"]: [k for k, _ in m["flags"]] for m in data["models"]}
        assert "shape" in flags["m-b"]
        assert "ok" in flags["m-a"]
        assert data["models"][0]["provenance"]["source"] == "bio3d-arena"
    finally:
        db.close()


def test_spotlight_route_renders(monkeypatch):
    db = SessionLocal()
    try:
        _seed_subject(db)
    finally:
        db.close()
    monkeypatch.setattr(
        spotlight,
        "SPOTLIGHTS",
        [
            {
                "slug": "test",
                "task_title": "Spotlight Test Subject",
                "featured": True,
                "order": 0,
                "blurb": "b",
                "reference_image": None,
            },
        ],
    )
    client = TestClient(app)
    assert client.get("/spotlight").status_code == 200
    page = client.get("/spotlight/test")
    assert page.status_code == 200
    assert "m-a" in page.text and "m-b" in page.text
    assert client.get("/spotlight/does-not-exist").status_code == 404
