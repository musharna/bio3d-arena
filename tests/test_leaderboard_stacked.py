"""The leaderboard defaults to stacked per-paradigm sections (each its own within-paradigm BT
rank — the rigorous comparison); ?overall=true shows the caveated merged board; ?paradigm=X shows
one paradigm."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.main import app
from app.models import Criterion, Generator, ModelOutput, Rating, Task

client = TestClient(app)


def setup_module(_m):
    init_db()
    with SessionLocal() as db:
        crit = db.execute(select(Criterion).where(Criterion.slug == "overall")).scalars().first()
        if crit is None:
            crit = Criterion(slug="overall", name="Overall")
            db.add(crit)
            db.flush()
        for slug, paradigm, bt in [
            ("lbstack-recon", "image_recon", 30.0),
            ("lbstack-text", "text_native", 20.0),
        ]:
            if db.execute(select(Generator).where(Generator.slug == slug)).scalars().first():
                continue
            g = Generator(slug=slug, name=slug, paradigm=paradigm)
            db.add(g)
            db.flush()
            t = Task(title=f"t-{slug}", prompt="p", category_id=1)
            db.add(t)
            db.flush()
            db.add(
                ModelOutput(
                    task_id=t.id,
                    generator_id=g.id,
                    asset_path="x.glb",
                    asset_format="glb",
                    source="api:fal:x",
                )
            )
            db.add(
                Rating(
                    criterion_id=crit.id,
                    category_id=None,
                    generator_id=g.id,
                    elo=1000.0,
                    bt_score=bt,
                    bt_lower=bt - 1,
                    bt_upper=bt + 1,
                    n_games=5,
                )
            )
        db.commit()


def _headings(html: str) -> list[str]:
    return re.findall(r'paradigm-heading">\s*([^<]+?)\s*<', html)


def test_default_is_stacked_per_paradigm():
    html = client.get("/leaderboard").text
    heads = _headings(html)
    # our two seeded paradigms each render as their own section; none is the merged "Overall"
    assert any("Image" in h for h in heads), heads
    assert any("Text" in h or "text" in h for h in heads), heads
    assert not any("Overall" in h for h in heads)
    assert "By paradigm" in html and "Overall" in html  # both tabs present


def test_overall_toggle_shows_single_merged_board():
    heads = _headings(client.get("/leaderboard?overall=true").text)
    assert len(heads) == 1 and "Overall" in heads[0]


def test_single_paradigm_tab_shows_one_board():
    heads = _headings(client.get("/leaderboard?paradigm=image_recon").text)
    assert len(heads) == 1 and "Overall" not in heads[0]
