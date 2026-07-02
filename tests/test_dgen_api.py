# tests/test_dgen_api.py
from app.database import SessionLocal, init_db
from app.models import DGenRun, DGenIteration
from app import service


def setup_module(_m):
    init_db()


def test_dgen_trajectory_groups_rounds_and_computes_lift():
    with SessionLocal() as db:
        run = DGenRun(model_id="m2")
        db.add(run)
        db.flush()
        db.add_all(
            [
                DGenIteration(run_id=run.id, taxon="Rosa", round=0, fidelity=0.4, status="ok"),
                DGenIteration(
                    run_id=run.id, taxon="Rosa", round=1, fidelity=0.7, status="ok", is_best=True
                ),
            ]
        )
        db.commit()
        traj = [t for t in service.dgen_trajectory(db, run_id=run.id) if t["taxon"] == "Rosa"]
        assert len(traj) == 1
        t = traj[0]
        assert [r["round"] for r in t["rounds"]] == [0, 1]
        assert t["fidelity_0"] == 0.4 and t["fidelity_best"] == 0.7
        assert abs(t["lift"] - 0.3) < 1e-9
