# tests/test_dgen_models.py
import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal, init_db
from app.models import DGenRun, DGenIteration


def setup_module(_m):
    init_db()


def test_run_and_iteration_persist_and_unique():
    with SessionLocal() as db:
        run = DGenRun(model_id="gemini-x")
        db.add(run)
        db.flush()
        db.add(
            DGenIteration(
                run_id=run.id,
                taxon="Zea mays",
                round=0,
                fidelity=0.5,
                n_correct=2,
                n_assessable=4,
                completeness_category="complete",
                completeness_score=1.0,
                critique="",
                script="import bpy",
                status="ok",
                is_best=True,
            )
        )
        db.commit()
        got = db.query(DGenIteration).filter_by(run_id=run.id, taxon="Zea mays", round=0).one()
        assert got.fidelity == 0.5 and got.is_best is True

        # (run_id, taxon, round) is unique
        db.add(DGenIteration(run_id=run.id, taxon="Zea mays", round=0, status="ok"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
