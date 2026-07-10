"""The hosting provider (fal / Replicate) is shown ONLY when it disambiguates two entries — i.e.
when the same base model runs on more than one provider (TRELLIS, Rodin text produce genuinely
different meshes per provider). A model whose base name is already unique drops the provider as
noise; non-provider parentheticals are left untouched; still-colliding names fall back to a slug."""

from __future__ import annotations

import random

from app import service
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def _gen(db, slug: str, name: str, source: str) -> Generator:
    g = Generator(slug=slug, name=name)
    db.add(g)
    db.flush()
    t = Task(title=f"t-{random.randint(0, 10**9)}", prompt="p", category_id=1)
    db.add(t)
    db.flush()
    db.add(
        ModelOutput(
            task_id=t.id, generator_id=g.id, asset_path="x.glb", asset_format="glb", source=source
        )
    )
    db.flush()
    return g


def test_split_provider_only_api_recon_sources():
    f = service._split_provider
    assert f("TRELLIS (fal)", "api:fal:trellis") == ("TRELLIS", "fal")
    assert f("Rodin text (Replicate)", "api:text:replicate:rodin-text") == (
        "Rodin text",
        "Replicate",
    )
    assert f("TRELLIS multi-view (fal)", "recon:trellis-mv") == ("TRELLIS multi-view", "fal")
    # non-provider parentheticals: no split
    assert f("Plant3D (Salk)", "plant3d") == ("Plant3D (Salk)", None)
    assert f("PartCrafter (part-based)", "frontier:partcrafter") == (
        "PartCrafter (part-based)",
        None,
    )
    assert f("XfrogPlants (botanical)", "found:xfrog") == ("XfrogPlants (botanical)", None)
    assert f("Hunyuan3D", "bio3d-arena") == ("Hunyuan3D", None)


def test_unique_model_drops_provider():
    init_db()
    with SessionLocal() as db:
        r = random.randint(0, 10**6)
        g = _gen(db, f"uniq-{r}", f"UniqModel{r} (fal)", "api:fal:uniq")
        db.commit()
        names = service.generator_display_names(db)
        assert names[g.id] == f"UniqModel{r}"  # unique base → provider dropped as noise


def test_colliding_model_keeps_provider():
    init_db()
    with SessionLocal() as db:
        r = random.randint(0, 10**6)
        a = _gen(db, f"coll-a-{r}", f"CollModel{r} (fal)", "api:fal:coll")
        b = _gen(db, f"coll-b-{r}", f"CollModel{r} (Replicate)", "api:replicate:coll")
        db.commit()
        names = service.generator_display_names(db)
        assert names[a.id] == f"CollModel{r} via fal"
        assert names[b.id] == f"CollModel{r} via Replicate"


def test_shared_nonprovider_name_still_gets_slug_suffix():
    init_db()
    with SessionLocal() as db:
        r = random.randint(0, 10**6)
        a = _gen(db, f"xfrog-AG15-s2-{r}", "XfrogPlants (botanical)", "found:xfrog")
        b = _gen(db, f"xfrog-AG20-s5-{r}", "XfrogPlants (botanical)", "found:xfrog")
        db.commit()
        names = service.generator_display_names(db)
        assert names[a.id] != names[b.id]
        assert names[a.id].startswith("XfrogPlants (botanical) · ")
