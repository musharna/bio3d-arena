"""--calibration-only scope: judge ONLY the calibration ladder, skip the full grid.

The grid explodes with coverage (15k+ pairs); the calibration ladder is a bounded
150-pair subset across all view conditions. calibration_only=True must emit only the
calibration work — every condition for the CalibrationPair, and NO grid-only pairs.
"""

from __future__ import annotations

from app.database import SessionLocal, init_db
from app.judge_render import CONDITIONS
from app.models import CalibrationPair, Category, Criterion, Generator, JudgeVote, ModelOutput, Task
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


def _seed(db):
    db.query(JudgeVote).delete()
    db.query(CalibrationPair).delete()
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like("jco/%.glb"))
    cascade_delete(db, Task, Task.title == "jco-task")
    cascade_delete(db, Generator, Generator.slug.like("jco-g%"))
    db.query(Category).filter_by(slug="jco-cat").delete(synchronize_session=False)
    db.commit()
    cat = Category(slug="jco-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    gens = [Generator(slug=f"jco-g{i}", name=f"G{i}") for i in range(3)]
    db.add_all(gens)
    db.flush()
    task = Task(category_id=cat.id, title="jco-task", prompt="p")  # active by default
    db.add(task)
    db.flush()
    for g in gens:
        db.add(ModelOutput(task_id=task.id, generator_id=g.id, asset_path=f"jco/{g.id}.glb"))
    db.commit()
    return task, crit


def test_calibration_only_enumerate_skips_grid():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, crit = _seed(db)
        outs = sorted(o.id for o in task.outputs)
        a, b = outs[0], outs[1]  # one of C(3,2)=3 grid pairs becomes the calibration pair
        db.add(CalibrationPair(task_id=task.id, output_a_id=a, output_b_id=b, criterion_id=crit.id))
        db.commit()

        items = jv.enumerate_work(db, criteria_slugs=["overall"], calibration_only=True)
        mine = [it for it in items if it["task_id"] == task.id]
        # 1 calibration pair × 3 conditions × 2 orders = 6 rows, and NOTHING from the grid.
        assert len(mine) == 6
        assert {it["condition"] for it in mine} == set(CONDITIONS)
        # every row is the calibration pair — no grid-only pair leaks in (incl. multi4).
        assert all(tuple(sorted((it["output_a_id"], it["output_b_id"]))) == (a, b) for it in mine)


def test_run_batch_calibration_only_writes_only_ladder():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, crit = _seed(db)
        outs = sorted(o.id for o in task.outputs)
        a, b = outs[0], outs[1]
        db.add(CalibrationPair(task_id=task.id, output_a_id=a, output_b_id=b, criterion_id=crit.id))
        db.commit()

        jv.run_batch(
            db,
            judge_fn=lambda *a: ("a", "r"),
            sheet_b64=lambda oid, cond: "QQ==",
            criteria_slugs=["overall"],
            calibration_only=True,
        )
        votes = db.query(JudgeVote).filter_by(task_id=task.id).all()
        assert len(votes) == 6  # ladder only; grid (3 pairs × multi4) suppressed
        assert {v.view_condition for v in votes} == set(CONDITIONS)
        assert all(tuple(sorted((v.output_a_id, v.output_b_id))) == (a, b) for v in votes)
