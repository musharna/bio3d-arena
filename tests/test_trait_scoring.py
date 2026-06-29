# tests/test_trait_scoring.py
from __future__ import annotations

from app import service
from app.database import SessionLocal, init_db
from app.models import TraitCalibration, TraitScore, TraitVerdict


def setup_module(_m):
    init_db()


def _clear(db):
    db.query(TraitScore).filter(TraitScore.output_id.in_([9001, 9002])).delete(False)
    db.query(TraitVerdict).filter(TraitVerdict.output_id.in_([9001, 9002])).delete(False)
    db.query(TraitCalibration).delete(False)
    db.commit()


def test_scores_use_only_accepted_classes_and_skip_not_assessable():
    with SessionLocal() as db:
        _clear(db)
        # color is accepted; phyllotaxy is NOT calibrated → excluded from the score
        db.add(TraitCalibration(trait_class="color", kappa=0.8, n=30, accepted=True))
        vs = [
            ("color", "present_correct"),
            ("color", "absent"),
            ("color", "not_assessable"),  # excluded
            ("phyllotaxy", "present_correct"),  # excluded (class not accepted)
        ]
        for i, (cls, v) in enumerate(vs):
            db.add(
                TraitVerdict(
                    output_id=9001,
                    rubric_id=1,
                    trait_key=f"k{i}",
                    trait_class=cls,
                    verdict=v,
                    judge_model="m",
                )
            )
        db.commit()
        service.recompute_trait_scores(db)
        ts = db.query(TraitScore).filter_by(output_id=9001).one()
        # accepted+assessable color verdicts: 1 correct of 2 → 0.5
        assert ts.n_scored == 2 and ts.botanical_accuracy == 0.5


def test_calibration_gate_threshold():
    with SessionLocal() as db:
        _clear(db)
        labels = [(9002, "k", "color", "present_correct")]
        db.add(
            TraitVerdict(
                output_id=9002,
                rubric_id=1,
                trait_key="k",
                trait_class="color",
                verdict="present_correct",
                judge_model="m",
            )
        )
        db.commit()
        # n below MIN_N → not accepted even at perfect agreement
        res = service.recompute_trait_calibration(db, labels)
        cal = db.query(TraitCalibration).filter_by(trait_class="color").one()
        assert cal.accepted is False  # n=1 < MODE_C_MIN_N
