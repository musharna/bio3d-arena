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
    assert "capture_scan" in config.APP_HIDDEN_PARADIGMS  # data-capture reference, internal-only


def test_selfhosted_recon_dups_pruned_but_instantmesh_kept():
    # bio3d-arena self-hosted TRELLIS/Hunyuan3D dupe the API versions → pruned by slug; the only
    # InstantMesh stays. API-served TRELLIS has a distinct slug and must NOT be caught.
    assert "trellis" in config.APP_HIDDEN_GENERATOR_SLUGS
    assert "hunyuan3d" in config.APP_HIDDEN_GENERATOR_SLUGS
    assert "instantmesh" not in config.APP_HIDDEN_GENERATOR_SLUGS
    init_db()
    with SessionLocal() as db:
        r = random.randint(0, 10**9)

        # Get-or-create by slug: seed_all() (run by another test on the shared temp DB) already
        # commits generators named "trellis"/"instantmesh", so a bare INSERT would hit the
        # generator.slug UNIQUE constraint. The hidden-ness is a property of the slug, so reusing
        # the existing generator tests exactly the same mapping.
        def mk(slug):
            g = db.query(Generator).filter_by(slug=slug).first()
            if g is None:
                g = Generator(slug=slug, name=slug, paradigm="image_recon")
                db.add(g)
                db.flush()
            db.add(
                ModelOutput(
                    task_id=1,
                    generator_id=g.id,
                    asset_path="x.glb",
                    asset_format="glb",
                    source="bio3d-arena",
                )
            )
            db.flush()
            return g

        selfhosted, api, instant = mk("trellis"), mk(f"fal:trellis-{r}"), mk("instantmesh")
        db.commit()
        hidden = service.app_hidden_generator_ids(db)
        assert selfhosted.id in hidden
        assert api.id not in hidden and instant.id not in hidden


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
