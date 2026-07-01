from __future__ import annotations

import trimesh

from app import commission
from app.database import SessionLocal, init_db
from app.models import CommissionAttempt, Generator, ModelOutput, Task, Category, TraitRubric


def setup_module(_m):
    init_db()


def _task(db, cat_slug="t-cat"):
    cat = Category(slug=cat_slug, name="c")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title="tomato", prompt="a tomato")
    db.add(t)
    db.commit()
    return t.id


def _rubric_task(db, taxon):
    cat = Category(slug=f"c-{taxon}", name="c")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=taxon, prompt=f"a {taxon}")
    db.add(t)
    db.flush()
    db.add(TraitRubric(taxon=taxon, task_id=t.id, traits_json="[]"))
    db.commit()
    return t.id


def test_ingest_ok_creates_output_and_attempt(tmp_path):
    with SessionLocal() as db:
        tid = _task(db)
        glb = tmp_path / "gen.glb"
        trimesh.creation.box().export(str(glb))
        run = {
            "status": "ok",
            "stderr": "",
            "duration_ms": 1234,
            "glb_path": str(glb),
            "mesh_stats": {"vertices": 8, "faces": 12},
        }
        att = commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="anthropic/claude-opus-4.8",
            run=run,
            script="import bpy",
            asset_dir=tmp_path / "assets",
        )
        assert att.status == "ok" and att.output_id is not None
        out = db.get(ModelOutput, att.output_id)
        assert out.source == "commissioned" and out.asset_format == "glb"
        assert (tmp_path / "assets" / out.asset_path).exists()
        assert db.query(Generator).filter_by(id=att.generator_id).one().kind == "model"


def test_ingest_failure_writes_attempt_without_output(tmp_path):
    with SessionLocal() as db:
        tid = _task(db, cat_slug="t-cat-2")
        run = {
            "status": "error",
            "stderr": "boom",
            "duration_ms": 50,
            "glb_path": None,
            "mesh_stats": {},
        }
        att = commission.ingest_attempt(
            db,
            task_id=tid,
            model_id="openai/gpt-x",
            run=run,
            script="bad",
            asset_dir=tmp_path / "assets",
        )
        assert att.status == "error" and att.output_id is None
        assert att.error == "boom" and att.script == "bad"


def test_run_batch_persists_and_resumes(tmp_path):
    import trimesh

    with SessionLocal() as db:
        tid = _rubric_task(db, "Solanum lycopersicum")

        def complete_fn(model_id, prompt):
            return "```python\nimport bpy\n```"

        def run_fn(script, out_glb):
            trimesh.creation.box().export(str(out_glb))
            return {
                "status": "ok",
                "stderr": "",
                "duration_ms": 5,
                "glb_path": str(out_glb),
                "mesh_stats": {"vertices": 8, "faces": 12},
            }

        roster = ["anthropic/claude-opus-4.8"]
        tt = [("Solanum lycopersicum", tid)]
        res = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=roster,
            taxon_tasks=tt,
            asset_dir=tmp_path / "assets",
        )
        assert res["ok"] == 1
        att = db.query(CommissionAttempt).filter_by(model_id=roster[0], task_id=tid).one()
        assert att.status == "ok"

        # resume: same pair is skipped, no second attempt
        res2 = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=roster,
            taxon_tasks=tt,
            asset_dir=tmp_path / "assets",
        )
        assert res2["skipped"] == 1 and res2["ok"] == 0
        assert db.query(CommissionAttempt).filter_by(model_id=roster[0], task_id=tid).count() == 1


def test_dry_run_plan_counts_uncovered_pairs(tmp_path):
    from scripts import commission_arena

    with SessionLocal() as db:
        tid = _rubric_task(db, "Zea mays")
        plan = commission_arena.plan(db, roster=["m1", "m2"])
        assert plan["tasks"] == 1 and plan["roster"] == 2 and plan["calls_needed"] == 2
