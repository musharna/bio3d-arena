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
    assert "Perceptual ranking by tier" in t  # the judge-BT-per-tier section header


def test_difficulty_discoverable_from_research_hub():
    # Was: a top-level sidebar link. The board now sits behind the internal-only /research
    # hub, so discoverability is asserted there. What this test protects is that the board
    # is reachable by clicking — not that it holds a sidebar slot.
    assert '/difficulty"' in TestClient(app).get("/research").text


def test_tier_perceptual_ranking_ranks_judge_votes():
    """Per-tier VLM-judge BT: the method that wins all judge votes tops its tier."""
    from sqlalchemy import select

    from app import service
    from app.models import Criterion, JudgeVote

    db = SessionLocal()
    try:
        db.query(TaskDifficulty).delete()
        db.commit()
        r = random.randint(0, 10**6)
        cat = Category(slug=f"c-tpr-{r}", name="Plants")
        db.add(cat)
        db.flush()
        task = Task(category_id=cat.id, title=f"TPR {r}", prompt="p")
        db.add(task)
        db.flush()
        crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        if crit is None:
            crit = Criterion(slug="overall", name="Overall")
            db.add(crit)
            db.flush()
        win = Generator(slug=f"win-{r}", name="Winner")
        lose = Generator(slug=f"lose-{r}", name="Loser")
        db.add_all([win, lose])
        db.flush()

        def out(g):
            o = ModelOutput(
                task_id=task.id, generator_id=g.id, asset_path="seed/x.glb", asset_format="glb"
            )
            db.add(o)
            db.flush()
            return o

        ow, ol = out(win), out(lose)
        set_task_difficulty(db, task.id, "easy", "", commit=False)
        for _ in range(4):
            db.add(
                JudgeVote(
                    criterion_id=crit.id,
                    view_condition="multi4",
                    task_id=task.id,
                    output_a_id=ow.id,
                    output_b_id=ol.id,
                    winner="a",
                    judge_model="m",
                    swap_group=f"sg-{r}",
                )
            )
        db.commit()

        ranking = service.tier_perceptual_ranking(db)
        easy = next(b for b in ranking if b["tier"] == "easy")
        assert easy["n_matches"] == 4
        assert easy["rows"][0]["generator"] == "Winner"  # won all → top BT
    finally:
        db.close()
