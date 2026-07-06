# tests/test_assign_difficulty_seed.py
import json

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.difficulty_rubric import RUBRIC
from app.models import TaxonDifficulty
from scripts import assign_difficulty


def setup_module(_m):
    init_db()


def test_seed_taxon_difficulty_from_rubric():
    with SessionLocal() as db:  # commit=False → rolls back on close
        res = assign_difficulty.seed_taxon_difficulty(db, commit=False)
        assert res["seeded"] == len(RUBRIC)  # every rubric taxon seeded
        rows = {
            r.species_slug: r
            for r in db.execute(select(TaxonDifficulty)).scalars()
            if r.species_slug in RUBRIC
        }
        assert rows["solanum_lycopersicum"].tier == "easy"
        assert rows["hordeum_vulgare"].tier == "hard"
        assert json.loads(rows["pinus_sylvestris"].axis_scores)["thin_structure"] == 2
        # idempotent — re-seed, still exactly one row per rubric taxon (upsert by slug)
        assign_difficulty.seed_taxon_difficulty(db, commit=False)
        rubric_rows = [
            r for r in db.execute(select(TaxonDifficulty)).scalars() if r.species_slug in RUBRIC
        ]
        assert len(rubric_rows) == len(RUBRIC)
