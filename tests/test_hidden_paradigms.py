"""retrieval (found human assets) and procedural_expert (hand-authored, unscalable) are not what
the arena tests — they're excluded from the whole app UI (kept in the DB for internal analysis).
procedural_llm (LLM-authored) and image_recon stay."""

from __future__ import annotations

import random

from app import config, service
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def _gen(db, paradigm: str) -> Generator:
    r = random.randint(0, 10**9)
    g = Generator(slug=f"hp-{paradigm}-{r}", name=f"HP {paradigm} {r}", paradigm=paradigm)
    db.add(g)
    db.flush()
    t = Task(title=f"t-{r}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    db.add(
        ModelOutput(
            task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb", source="s"
        )
    )
    db.flush()
    return g


def test_hidden_paradigms_configured():
    assert "retrieval" in config.APP_HIDDEN_PARADIGMS
    assert "procedural_expert" in config.APP_HIDDEN_PARADIGMS
    assert "procedural_llm" not in config.APP_HIDDEN_PARADIGMS  # the scalable path stays
    assert "image_recon" not in config.APP_HIDDEN_PARADIGMS


def test_retrieval_and_procedural_expert_are_app_hidden():
    init_db()
    with SessionLocal() as db:
        ret = _gen(db, "retrieval")
        pex = _gen(db, "procedural_expert")
        pllm = _gen(db, "procedural_llm")
        recon = _gen(db, "image_recon")
        db.commit()

        hidden = service.app_hidden_generator_ids(db)
        assert ret.id in hidden and pex.id in hidden  # not what the arena tests
        assert pllm.id not in hidden and recon.id not in hidden  # stay

        # propagates to the Mode-A perceptual exclusion (leaderboard / arena pool / significance)
        excluded = service.mode_a_excluded_generator_ids(db)
        assert ret.id in excluded and pex.id in excluded
        assert pllm.id not in excluded and recon.id not in excluded
