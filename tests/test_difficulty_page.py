"""The /difficulty UI page: renders per-tier scorecard + cross-tier gradient."""

from __future__ import annotations

import random

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.difficulty import set_task_difficulty
from app.main import app
from app.models import Category, Generator, Metric, ModelOutput, Task, TaskDifficulty


def setup_module(_m):
    init_db()


def _scored_output(db, task, gen, chamfer):
    o = ModelOutput(
        task_id=task.id, generator_id=gen.id, asset_path="seed/x.glb", asset_format="glb"
    )
    db.add(o)
    db.flush()
    db.add(Metric(output_id=o.id, status="ok", chamfer=chamfer, fscore=0.5, coverage=0.7))
    return o


def test_difficulty_page_renders_tiers_and_gradient():
    db = SessionLocal()
    try:
        db.query(TaskDifficulty).delete()
        db.commit()
        r = random.randint(0, 10**6)
        cat = Category(slug=f"c-dp-{r}", name="Plants")
        db.add(cat)
        db.flush()
        easy = Task(category_id=cat.id, title=f"Easy Sp {r}", prompt="p")
        hard = Task(category_id=cat.id, title=f"Hard Sp {r}", prompt="p")
        gen = Generator(slug=f"g-dp-{r}", name="GradGen")
        db.add_all([easy, hard, gen])
        db.flush()
        _scored_output(db, easy, gen, 0.05)  # better on easy
        _scored_output(db, hard, gen, 0.15)  # worse on hard → "degrades ↑"
        set_task_difficulty(db, easy.id, "easy", "open", commit=False)
        set_task_difficulty(db, hard.id, "hard", "occluded", commit=False)
        db.commit()
    finally:
        db.close()

    page = TestClient(app).get("/difficulty")
    assert page.status_code == 200
    t = page.text
    assert "Difficulty tiers" in t
    assert "Cross-tier gradient" in t
    assert "GradGen" in t  # appears in gradient + tier tables
    assert "Easy tier" in t and "Hard tier" in t
    assert "degrades" in t  # the gradient trend label for a worsening method


def test_difficulty_in_nav():
    page = TestClient(app).get("/")
    assert '/difficulty"' in page.text  # nav link present
