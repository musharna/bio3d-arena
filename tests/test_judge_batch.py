from __future__ import annotations

from app.database import SessionLocal, init_db
from app.models import CalibrationPair, Category, Criterion, Generator, JudgeVote, ModelOutput, Task


def setup_module(_m):
    init_db()


def _seed(db):
    # Clean previous run's jb-prefixed rows (Category+Generator slugs are unique).
    db.query(JudgeVote).delete()
    db.query(CalibrationPair).delete()
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("seed/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title == "jb-task").delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("jb-g%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="jb-cat").delete(synchronize_session=False)
    db.commit()
    cat = Category(slug="jb-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    gens = [Generator(slug=f"jb-g{i}", name=f"G{i}") for i in range(3)]
    db.add_all(gens)
    db.flush()
    task = Task(category_id=cat.id, title="jb-task", prompt="p")
    db.add(task)
    db.flush()
    for g in gens:
        db.add(ModelOutput(task_id=task.id, generator_id=g.id, asset_path=f"seed/{g.id}.glb"))
    db.commit()
    return task, crit


def test_run_batch_writes_both_orders_and_resumes():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        task, _crit = _seed(db)

        def judge_fn(species, prompt, cname, cdesc, a_b64, b_b64):
            return "a", "stub rationale"

        def sheet_b64(output_id, condition):
            return "QQ=="  # 1-byte PNG stub; not actually decoded by the stub judge

        jv.run_batch(
            db,
            judge_fn=judge_fn,
            sheet_b64=sheet_b64,
            grid_condition="multi4",
            criteria_slugs=["overall"],
        )
        # 3 generators → C(3,2)=3 logical pairs × 2 orders = 6 votes for THIS task.
        # Scope to the jb-seeded task so any active tasks/pairs another test file
        # left behind in the shared persistent DB can't perturb the count.
        votes = db.query(JudgeVote).filter_by(task_id=task.id).all()
        assert len(votes) == 6
        groups = {v.swap_group for v in votes}
        assert len(groups) == 3  # each logical pair shares one swap_group
        for g in groups:
            assert db.query(JudgeVote).filter_by(swap_group=g).count() == 2

        # Resume: a second run writes nothing new (resume key matches written rows).
        res2 = jv.run_batch(
            db,
            judge_fn=judge_fn,
            sheet_b64=sheet_b64,
            grid_condition="multi4",
            criteria_slugs=["overall"],
        )
        assert res2["written"] == 0
        assert db.query(JudgeVote).filter_by(task_id=task.id).count() == 6


def test_calibration_enumerates_all_conditions():
    import scripts.judge_vlm as jv
    from app.judge_render import CONDITIONS

    with SessionLocal() as db:
        task, crit = _seed(db)
        # Disable the GRID branch for this task so only the CALIBRATION branch
        # produces its rows — the calibration branch processes CalibrationPair
        # regardless of Task.active. (Grid would otherwise duplicate the multi4 row.)
        task.active = False
        outs = sorted(o.id for o in task.outputs)
        a, b = outs[0], outs[1]
        db.add(CalibrationPair(task_id=task.id, output_a_id=a, output_b_id=b, criterion_id=crit.id))
        db.commit()

        items = jv.enumerate_work(db, criteria_slugs=["overall"])
        cal = [it for it in items if it["task_id"] == task.id]
        # 1 calibration pair × 3 conditions × 2 orders = 6 rows.
        assert len(cal) == 6
        # All three perception conditions fire (single, multi4, turntable).
        assert {it["condition"] for it in cal} == set(CONDITIONS)
        groups = {it["swap_group"] for it in cal}
        assert len(groups) == 3  # one swap_group per condition
        for g in groups:
            rows = [it for it in cal if it["swap_group"] == g]
            assert len(rows) == 2  # both orders share the swap_group
            assert {(r["output_a_id"], r["output_b_id"]) for r in rows} == {(a, b), (b, a)}


def test_max_votes_caps_writes():
    import scripts.judge_vlm as jv

    with SessionLocal() as db:
        _seed(db)
        res = jv.run_batch(
            db,
            judge_fn=lambda *a: ("b", "r"),
            sheet_b64=lambda oid, cond: "QQ==",
            grid_condition="multi4",
            criteria_slugs=["overall"],
            max_votes=2,
        )
        assert res["written"] == 2
