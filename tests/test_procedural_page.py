"""/procedural HTML page + /api/procedural.json render and expose the scorecard."""

from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app
from app.models import Category, CommissionAttempt, Generator, Task

client = TestClient(app)


def setup_module(_m):
    init_db()


def _seed_one() -> str:
    with SessionLocal() as db:
        tag = uuid.uuid4().hex
        name = f"proc-page-{tag}"
        g = Generator(slug=f"pp-{tag}", name=name, kind="model", paradigm="procedural_llm")
        cat = Category(slug=f"pp-cat-{tag}", name="C")
        db.add_all([g, cat])
        db.flush()
        t = Task(category_id=cat.id, title=f"pp-{tag}", prompt="p")
        db.add(t)
        db.flush()
        db.add(
            CommissionAttempt(
                task_id=t.id,
                model_id=name,
                generator_id=g.id,
                status="ok",
                mesh_stats_json=json.dumps({"vertices": 42}),
            )
        )
        db.commit()
        return name


def test_procedural_page_renders_and_names_model():
    name = _seed_one()
    r = client.get("/procedural")
    assert r.status_code == 200
    body = r.text
    assert name in body  # models are named, not anonymized
    assert "experimental" in body.lower()  # fidelity caveat present
    assert "pass@1" in body


def test_procedural_json_shape():
    name = _seed_one()
    r = client.get("/api/procedural.json")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    row = next(d for d in data if d["model"] == name)
    for k in (
        "model",
        "attempts",
        "valid",
        "pass_at_1",
        "morph_correct",
        "morph_assessable",
        "morph_fidelity",
        "median_verts",
        "n",
    ):
        assert k in row
    assert row["pass_at_1"] == 1.0
