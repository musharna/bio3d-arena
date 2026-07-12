"""_build_calibration_comparison must skip calibration pairs with dangling refs (deleted
outputs/task/criterion) — never select one as target (would 500) and exclude it from total."""

from __future__ import annotations

from app.database import SessionLocal, init_db
from app.main import _build_calibration_comparison
from app.models import CalibrationPair, Category, Criterion, Generator, ModelOutput, Task


def setup_module(_m):
    init_db()


def _clear(db):
    db.query(CalibrationPair).delete(synchronize_session=False)
    db.query(ModelOutput).filter(ModelOutput.asset_path.like("cdg/%.glb")).delete(
        synchronize_session=False
    )
    db.query(Task).filter(Task.title == "cdg-task").delete(synchronize_session=False)
    db.query(Generator).filter(Generator.slug.like("cdg-g%")).delete(synchronize_session=False)
    db.query(Category).filter_by(slug="cdg-cat").delete(synchronize_session=False)
    db.commit()


def _seed_base(db):
    cat = Category(slug="cdg-cat", name="C")
    db.add(cat)
    db.flush()
    crit = db.query(Criterion).filter_by(slug="overall").first() or Criterion(
        slug="overall", name="Overall"
    )
    db.add(crit)
    db.flush()
    task = Task(category_id=cat.id, title="cdg-task", prompt="p")
    db.add(task)
    db.flush()
    outs = []
    for i in range(2):
        g = Generator(slug=f"cdg-g{i}", name=f"G{i}")
        db.add(g)
        db.flush()
        o = ModelOutput(
            task_id=task.id, generator_id=g.id, asset_path=f"cdg/{i}.glb", asset_format="glb"
        )
        db.add(o)
        db.flush()
        outs.append(o)
    return task, crit, outs


def test_dangling_pair_skipped_usable_served():
    with SessionLocal() as db:
        _clear(db)
        task, crit, outs = _seed_base(db)
        # dangling pair FIRST (lower id) so a naive impl would pick it as target and crash
        db.add(
            CalibrationPair(
                task_id=task.id, output_a_id=10**9, output_b_id=10**9 + 1, criterion_id=crit.id
            )
        )
        db.flush()
        db.add(
            CalibrationPair(
                task_id=task.id,
                output_a_id=outs[0].id,
                output_b_id=outs[1].id,
                criterion_id=crit.id,
            )
        )
        db.commit()

        res = _build_calibration_comparison(db, "cdg-sess")
        # served the usable pair (no crash), dangling excluded from total
        assert res is not None and not res.get("done")
        assert res["progress"]["total"] == 1
        served = {res["a"]["url"], res["b"]["url"]}
        # URLs are opaque + output-scoped (/media/o/{id}.{ext}), not the raw asset_path.
        assert served == {f"/media/o/{outs[0].id}.glb", f"/media/o/{outs[1].id}.glb"}


def test_only_dangling_returns_done_not_crash():
    with SessionLocal() as db:
        _clear(db)
        task, crit, _outs = _seed_base(db)
        db.add(
            CalibrationPair(
                task_id=task.id, output_a_id=10**9, output_b_id=10**9 + 1, criterion_id=crit.id
            )
        )
        db.commit()
        res = _build_calibration_comparison(db, "cdg-sess2")
        assert res == {"set": "calibration", "done": True, "progress": {"voted": 0, "total": 0}}
