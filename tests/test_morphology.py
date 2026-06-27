from __future__ import annotations

from app import morphology
from app.database import SessionLocal, init_db
from app.models import PlantMorphology


def setup_module(_m):
    init_db()


def test_strategy_covers_every_seeded_form():
    # every growth form we actually assign must have a recipe; every recipe key is a valid form
    assert set(morphology.SEED.values()) <= set(morphology.STRATEGY)
    assert set(morphology.STRATEGY) <= morphology.GROWTH_FORMS
    for entry in morphology.STRATEGY.values():
        assert entry.recon_mode in {
            "single",
            "multiview",
            "multiview_preferred",
            "multiview_required",
        }
        assert entry.min_px >= 1024


def test_seed_morphology_is_idempotent():
    db = SessionLocal()
    try:
        morphology.seed_morphology(db)
        morphology.seed_morphology(db)  # second call must not duplicate
        rows = db.query(PlantMorphology).all()
        assert len(rows) == len(morphology.SEED)
        by_slug = {r.subject_slug: r.growth_form for r in rows}
        assert by_slug["arabidopsis"] == morphology.ROSETTE
        assert by_slug["pinus"] == morphology.TREE_CONIFER
    finally:
        db.close()
