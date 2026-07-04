# tests/test_taxon_difficulty_model.py
import json

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import TaxonDifficulty


def setup_module(_m):
    init_db()


def test_taxon_difficulty_roundtrip():
    # flush (not commit) → rolls back on close; assert within the same session.
    with SessionLocal() as db:
        db.add(
            TaxonDifficulty(
                species_slug="rosa",
                tier="hard",
                axis_scores=json.dumps({"fine_detail": 2}),
                rationale=json.dumps({"fine_detail": "layered petals"}),
            )
        )
        db.flush()
        row = (
            db.execute(select(TaxonDifficulty).where(TaxonDifficulty.species_slug == "rosa"))
            .scalars()
            .one()
        )
        assert row.tier == "hard"
        assert json.loads(row.axis_scores)["fine_detail"] == 2
        assert row.updated is not None
