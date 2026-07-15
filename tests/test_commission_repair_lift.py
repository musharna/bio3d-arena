"""Lever A: raise the VALID-MESH pass rate of the procedural sweep.

Grounded in the real failure data: the 32 cells that still failed after 2 repairs were NOT logic
errors — they were bpy API hallucination/version misuse (16 AttributeError, 11 TypeError, 2
ReferenceError), e.g. a fabricated `bmesh.ops.create_cylinder`, a stale object ref ("StructRNA ...
has been removed"), a tuple handed to a float `default_value`, `.data` on a None active object.

So the levers are: (1) name those exact pitfalls in the base prompt (fair API context, like the
Principled-BSDF note already there — not a per-cell answer), (2) foreground the real exception in
the repair prompt instead of burying it in the dump, (3) allow a targeted re-measurement of just the
failed cells so the lift is provable without re-running the whole sweep.
"""

from __future__ import annotations

import trimesh

from app import commission
from app.database import SessionLocal, init_db
from app.models import Category, Task, TraitRubric


def setup_module(_m):
    init_db()


def test_build_prompt_names_the_bpy_api_pitfalls_that_actually_broke_the_sweep():
    p = commission.build_prompt("Solanum lycopersicum", "tomato").lower()
    assert "bmesh.ops" in p, "hallucinated bmesh ops (create_cylinder) were the single top failure"
    assert "default_value" in p, "NodeSocket default_value type mismatch (float vs tuple)"
    assert "active_object" in p, "unguarded None active object -> AttributeError on .data"
    assert "removed" in p, "stale StructRNA object reference after a mutating op"


def test_repair_prompt_foregrounds_the_real_exception_line():
    err = (
        "Traceback (most recent call last):\n"
        '  File "/tmp/x/runner.py", line 9, in <module>\n'
        "@@BIO3D_MODEL_ERROR@@\n"
        'AttributeError: BMeshOpsModule: operator "create_cylinder" doesn\'t exist\n'
    )
    fix = commission.repair_prompt("orig prompt", "import bpy", err)
    assert "create_cylinder" in fix
    # foregrounded: the exception is called out BEFORE the raw traceback dump, not only inside it
    assert fix.index("create_cylinder") < fix.index("Traceback")


def test_final_exception_line_extracts_through_the_sentinel():
    err = (
        "Traceback ...\n@@BIO3D_MODEL_ERROR@@\n"
        "TypeError: NodeSocketFloat.default_value expected a float type, not tuple\n"
    )
    line = commission._final_exception_line(err)
    assert line.startswith("TypeError:") and "default_value" in line


def _rubric_task(db, taxon, slug):
    cat = Category(slug=slug, name="c")
    db.add(cat)
    db.flush()
    t = Task(category_id=cat.id, title=taxon, prompt=f"a {taxon}")
    db.add(t)
    db.flush()
    db.add(TraitRubric(taxon=taxon, task_id=t.id, traits_json="[]"))
    db.commit()
    return t.id


def test_failed_pairs_returns_only_the_non_ok_cells(tmp_path):
    """The re-measurement needs the exact (model, task) cells that failed under the source protocol
    — an ok cell must never be re-run (it already has an entrant and would be a wasted call)."""
    with SessionLocal() as db:
        t = _rubric_task(db, "Zea mays", "fp-src")
        assets = tmp_path / "fp"
        commission.ingest_attempt(
            db,
            task_id=t,
            model_id="good/m",
            protocol="repair",
            run={
                "status": "ok",
                "stderr": "",
                "duration_ms": 1,
                "glb_path": str(_box(tmp_path)),
                "mesh_stats": {"vertices": 8},
            },
            script="import bpy",
            asset_dir=assets,
        )
        commission.ingest_attempt(
            db,
            task_id=t,
            model_id="bad/m",
            protocol="repair",
            run={"status": "script_error", "stderr": "boom", "duration_ms": 1, "glb_path": None},
            script="import bpy",
            asset_dir=assets,
        )

        fp = commission.failed_pairs(db, "repair")
        assert fp == {("bad/m", t)}


def _box(tmp_path):
    p = tmp_path / "b.glb"
    trimesh.creation.box().export(str(p))
    return p


def test_run_batch_pairs_filter_attempts_only_the_named_cells(tmp_path):
    """A targeted re-measurement of just the previously-failed cells must run EXACTLY those
    (model, task) pairs and leave every other pair untouched — otherwise measuring the lift would
    re-generate (and re-bill) the whole roster."""
    with SessionLocal() as db:
        t1 = _rubric_task(db, "Zea mays", "pf-1")
        t2 = _rubric_task(db, "Solanum lycopersicum", "pf-2")

        attempted = []

        def complete_fn(model_id, prompt):
            attempted.append((model_id, prompt))
            return "```python\nimport bpy\n```"

        def run_fn(script, out_glb):
            trimesh.creation.box().export(str(out_glb))
            return {
                "status": "ok",
                "stderr": "",
                "duration_ms": 5,
                "glb_path": str(out_glb),
                "mesh_stats": {"vertices": 8},
            }

        res = commission.run_batch(
            db,
            complete_fn=complete_fn,
            run_fn=run_fn,
            roster=["m1"],
            taxon_tasks=[("Zea mays", t1), ("Solanum lycopersicum", t2)],
            asset_dir=tmp_path / "a",
            pairs={("m1", t1)},
        )
        assert res["ok"] == 1
        assert len(attempted) == 1  # only the one named pair ever hit the model
        from app.models import CommissionAttempt

        rows = db.query(CommissionAttempt).filter_by(model_id="m1").all()
        assert {r.task_id for r in rows} == {t1}
