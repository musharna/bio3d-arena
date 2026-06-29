from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import TraitCalibration, TraitRubric, TraitScore, TraitVerdict


def setup_module(_m):
    init_db()


def test_trait_tables_roundtrip():
    with SessionLocal() as db:
        r = TraitRubric(taxon="Solanum lycopersicum", task_id=None, traits_json="[]")
        db.add(r)
        db.flush()
        db.add(
            TraitVerdict(
                output_id=1,
                rubric_id=r.id,
                trait_key="habit",
                trait_class="habit",
                verdict="present_correct",
                rationale="ok",
                judge_model="m",
            )
        )
        db.add(
            TraitScore(output_id=1, botanical_accuracy=0.5, n_scored=2, n_total=4, judge_model="m")
        )
        db.add(TraitCalibration(trait_class="color", kappa=0.7, n=25, accepted=True))
        db.commit()
        assert db.query(TraitVerdict).filter_by(rubric_id=r.id).count() == 1
        assert db.query(TraitScore).filter_by(output_id=1).one().botanical_accuracy == 0.5
        assert db.query(TraitCalibration).filter_by(trait_class="color").one().accepted is True
