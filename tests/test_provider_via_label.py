"""Model results read "Name via fal" / "Name via Replicate" instead of the bare parenthetical
"Name (fal)". The provider paren is only rewritten for real API models (source starts with
"api:"); non-provider parentheticals (Plant3D (Salk), Blender (procedural), XfrogPlants
(botanical)) are left untouched."""

from __future__ import annotations

import random

from app import service
from app.database import SessionLocal, init_db
from app.models import Generator, ModelOutput, Task


def test_provider_via_label_rewrites_only_api_sources():
    f = service.provider_via_label
    # API models: trailing provider paren → "via <provider>"
    assert f("TRELLIS (fal)", "api:fal:trellis") == "TRELLIS via fal"
    assert f("TRELLIS (Replicate)", "api:replicate:trellis") == "TRELLIS via Replicate"
    assert (
        f("Hunyuan3D v3 text (fal)", "api:text:fal:hunyuan3d-v3-text")
        == "Hunyuan3D v3 text via fal"
    )
    assert (
        f("Rodin text (Replicate)", "api:text:replicate:rodin-text") == "Rodin text via Replicate"
    )
    # recon: (multi-view) is also provider-hosted.
    assert f("TRELLIS multi-view (fal)", "recon:trellis-mv") == "TRELLIS multi-view via fal"
    # Non-provider parentheticals are left alone (incl. frontier:partcrafter's "(part-based)").
    assert f("PartCrafter (part-based)", "frontier:partcrafter") == "PartCrafter (part-based)"
    assert f("Plant3D (Salk)", "plant3d") == "Plant3D (Salk)"
    assert f("Blender (procedural)", "procedural:blender") == "Blender (procedural)"
    assert f("XfrogPlants (botanical)", "found:xfrog") == "XfrogPlants (botanical)"
    # No paren / no source → unchanged.
    assert f("Hunyuan3D", "bio3d-arena") == "Hunyuan3D"
    assert f("TRELLIS (fal)", None) == "TRELLIS (fal)"


def test_generator_display_names_applies_via_for_api_generator():
    init_db()
    db = SessionLocal()
    try:
        r = random.randint(0, 10**6)
        g = Generator(slug=f"trellis-fal-{r}", name="TRELLIS (fal)")
        db.add(g)
        db.flush()
        t = Task(title=f"t-via-{r}", prompt="p", category_id=1)
        db.add(t)
        db.flush()
        db.add(
            ModelOutput(
                task_id=t.id,
                generator_id=g.id,
                asset_path="x.glb",
                asset_format="glb",
                source="api:fal:trellis",
            )
        )
        db.commit()
        names = service.generator_display_names(db)
        assert names[g.id] == "TRELLIS via fal"
    finally:
        db.close()
