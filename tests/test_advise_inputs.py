from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from app import config, morphology
from app.database import SessionLocal, init_db
from app.models import PlantMorphology
from scripts.advise_inputs import advise, build_report


def setup_module(_m):
    init_db()


def _write_ref(slug, w=1200, h=1200):
    p = Path(config.ASSET_DIR) / "reference" / f"{slug}_ref.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    b = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(b, "JPEG")
    p.write_bytes(b.getvalue())


def test_advise_grades_present_ref_and_skips_missing():
    _write_ref("arabidopsis")
    # ensure pinus ref is absent
    miss = Path(config.ASSET_DIR) / "reference" / "pinus_ref.jpg"
    if miss.exists():
        miss.unlink()
    db = SessionLocal()
    try:
        results = advise(
            db,
            subjects=["arabidopsis", "pinus"],
            asset_dir=config.ASSET_DIR,
            heuristics_only=True,
        )
        by = {r["subject"]: r for r in results}
        assert by["arabidopsis"]["growth_form"] == morphology.ROSETTE
        assert by["arabidopsis"]["grade"].verdict == "good"
        assert "skipped" in by["pinus"]  # missing ref
        # morphology rows were seeded/upserted
        assert db.query(PlantMorphology).filter_by(subject_slug="arabidopsis").one()
        # report renders the key fields
        md = build_report(results)
        assert "arabidopsis" in md and "rosette" in md and "single" in md
    finally:
        db.close()
