"""xfrog + partcrafter are internal-data-only: their rows stay in the DB but they are
hidden from the whole app UI (arena pool, leaderboards, significance, spotlight) — the same
posture as AgriGen's procedural-expert testers, keyed by SOURCE because xfrog uses many
per-crop variant generator slugs (all named "XfrogPlants (botanical)")."""

from __future__ import annotations

import random

from app import config, service
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def _gen_with_output(db, slug: str, name: str, source: str) -> Generator:
    g = Generator(slug=slug, name=name)
    db.add(g)
    db.flush()
    t = Task(title=f"t-{random.randint(0, 10**9)}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    db.add(
        ModelOutput(
            task_id=t.id,
            generator_id=g.id,
            asset_path="seed/x.glb",
            asset_format="glb",
            source=source,
        )
    )
    db.flush()
    return g


def test_app_hidden_sources_configured():
    assert "found:xfrog" in config.APP_HIDDEN_SOURCES
    assert "frontier:partcrafter" in config.APP_HIDDEN_SOURCES


def test_app_hidden_generator_ids_catches_xfrog_variant_and_partcrafter():
    init_db()
    db = SessionLocal()
    try:
        r = random.randint(0, 10**6)
        xf = _gen_with_output(db, f"xfrog-AG15-s2-{r}", "XfrogPlants (botanical)", "found:xfrog")
        pc = _gen_with_output(
            db, f"partcrafter-{r}", "PartCrafter (part-based)", "frontier:partcrafter"
        )
        visible = _gen_with_output(
            db, f"visible-{r}", f"VisibleModel-internal-only-test-{r}", "api:fal:trellis"
        )
        db.commit()

        hidden = service.app_hidden_generator_ids(db)
        assert xf.id in hidden  # xfrog variant slug hidden by SOURCE
        assert pc.id in hidden
        assert visible.id not in hidden  # a real commercial generator stays visible in the app

        # propagates to the Mode-A perceptual exclusion set (leaderboard / pool / significance)
        excluded = service.mode_a_excluded_generator_ids(db)
        assert xf.id in excluded and pc.id in excluded
    finally:
        db.close()
