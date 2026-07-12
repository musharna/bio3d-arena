"""Consistency gate: anything hidden from the app UI everywhere (internal-only — retrieval +
procedural_expert paradigms, the pruned self-hosted recon dups, xfrog/partcrafter/AgriGen) must
never enter the public redistribute dataset either, even when the curator's allowlist names it."""

from __future__ import annotations

import uuid

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task
from app.public_export import resolve_include_ids


def test_resolve_include_ids_drops_app_hidden_generators():
    init_db()
    with SessionLocal() as db:
        cat = Category(slug=f"c-{uuid.uuid4().hex[:8]}", name="Plants")
        db.add(cat)
        db.flush()
        title = f"exhide-{uuid.uuid4().hex[:8]}"
        task = Task(category_id=cat.id, title=title, prompt="p", active=True)
        db.add(task)
        db.flush()

        def mk(slug, paradigm):
            g = Generator(slug=slug, name=slug, paradigm=paradigm)
            db.add(g)
            db.flush()
            db.add(
                ModelOutput(
                    task_id=task.id,
                    generator_id=g.id,
                    asset_path="x.glb",
                    asset_format="glb",
                    source="api:fal:x",
                )
            )
            db.flush()
            return g

        ret = mk(f"ret-{uuid.uuid4().hex[:6]}", "retrieval")  # app-hidden paradigm
        pex = mk(f"pex-{uuid.uuid4().hex[:6]}", "procedural_expert")  # app-hidden paradigm
        recon = mk(f"recon-{uuid.uuid4().hex[:6]}", "image_recon")  # a real model → included
        db.commit()

        inc = resolve_include_ids(
            db, task_titles=[title], generator_slugs=[ret.slug, pex.slug, recon.slug]
        )
        assert recon.id in inc.generator_ids  # real generative model is exportable
        assert ret.id not in inc.generator_ids  # retrieval never exported
        assert pex.id not in inc.generator_ids  # procedural_expert never exported
