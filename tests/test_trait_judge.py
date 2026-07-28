from __future__ import annotations

import json

from app.database import SessionLocal, init_db
from app.models import Category, Generator, ModelOutput, Task, TraitRubric, TraitVerdict
from tests.factories import cascade_delete


def setup_module(_m):
    init_db()


def _seed(db):
    db.query(TraitVerdict).filter(TraitVerdict.judge_model == "stub").delete(False)
    cascade_delete(db, ModelOutput, ModelOutput.asset_path.like("tj/%"))
    db.query(TraitRubric).filter_by(taxon="TJ").delete(False)
    db.query(Task).filter_by(title="tj-task").delete(False)
    cascade_delete(db, Generator, Generator.slug.like("tj-%"))
    db.query(Category).filter_by(slug="tj-cat").delete(False)
    db.commit()
    cat = Category(slug="tj-cat", name="C")
    db.add(cat)
    db.flush()
    task = Task(category_id=cat.id, title="tj-task", prompt="p")
    db.add(task)
    db.flush()
    db.add(
        TraitRubric(
            taxon="TJ",
            task_id=task.id,
            traits_json=json.dumps([{"key": "habit", "trait_class": "habit", "expected": "herb"}]),
        )
    )
    g = Generator(slug="tj-g", name="G")
    db.add(g)
    db.flush()
    o = ModelOutput(
        task_id=task.id,
        generator_id=g.id,
        asset_path="tj/a.glb",
        asset_format="glb",
        source="api:fal:trellis",
    )
    db.add(o)
    db.flush()
    db.commit()
    return task, o


def test_run_batch_writes_verdicts_and_is_resumable():
    import scripts.trait_judge as tj

    with SessionLocal() as db:
        task, o = _seed(db)
        work = tj.enumerate_work(db, [task.id])
        assert len(work) == 1

        calls = {"check": 0, "sheet": 0}

        def check_fn(species, prompt, sheet_b64, traits):
            calls["check"] += 1
            return [
                {
                    "trait_key": "habit",
                    "trait_class": "habit",
                    "verdict": "present_correct",
                    "rationale": "ok",
                }
            ]

        def sheet_b64(oid):
            calls["sheet"] += 1
            return "x"

        res = tj.run_batch(
            db, check_fn=check_fn, sheet_b64=sheet_b64, work=work, judge_model="stub"
        )
        assert res["written"] == 1
        assert calls["check"] == 1
        assert db.query(TraitVerdict).filter_by(output_id=o.id, judge_model="stub").count() == 1
        # resumable AND no re-spend: a full re-run must skip the paid check_fn/sheet entirely
        res2 = tj.run_batch(
            db, check_fn=check_fn, sheet_b64=sheet_b64, work=work, judge_model="stub"
        )
        assert res2["written"] == 0 and res2["skipped"] >= 1
        assert calls["check"] == 1  # NOT called again — spend avoided, not just dedup at write
        assert calls["sheet"] == 1
