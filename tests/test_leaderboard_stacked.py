"""Leaderboard views. `/leaderboard` defaults to the MODALITY HUB (one card per visible
modality, each linking to that modality's own board — the rigorous, within-paradigm comparison).
The old stacked sections and the caveated cross-paradigm `?overall=true` merged board are GONE
(paradigms are disconnected match pools). `?paradigm=X` still shows exactly one paradigm's board.
"""

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


def test_default_is_the_modality_hub():
    """The landing page is a hub of modality CARDS — no ranked board (and so no merged
    cross-paradigm ranking) is rendered on it at all."""
    html = client.get("/leaderboard").text
    assert 'class="lb-hub"' in html
    assert _headings(html) == []  # no board panels on the hub
    # one card per seeded modality, each linking to that modality's own board
    assert "/leaderboard/image_recon" in html
    assert "/leaderboard/text_native" in html


def test_overall_cross_paradigm_ranking_is_gone():
    """`?overall=true` no longer selects a merged board — the param is retired, so the request
    falls through to the hub and no cross-paradigm ranked board is ever rendered."""
    html = client.get("/leaderboard?overall=true").text
    assert 'class="lb-hub"' in html
    assert _headings(html) == []
    assert "overall=true" not in html  # no Overall tab/link anywhere


def test_single_paradigm_tab_shows_one_board():
    heads = _headings(client.get("/leaderboard?paradigm=image_recon").text)
    assert len(heads) == 1 and "Overall" not in heads[0]
